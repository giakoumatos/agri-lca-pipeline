"""
Correct many datasets at once, in the split Excels.

The split Excels (output/<project>/crop_datasets_new/<project>_part_N.xlsx, the
project name in letters, digits and _ only)
hold every dataset of a project in one workbook per chunk, so one
Find & Replace, one column filled down or a row deleted corrects every
dataset it touches.  This carries such edits back into the dataset workbooks
they belong to:

  1. Every pipeline run and every re-check ends by rebuilding the splits from
     the final datasets (`--rebuild`).  No copy is kept: each part carries a
     fingerprint of itself as built, in its document properties.
  2. You edit a part file and save it.
  3. recheck.py (or the Re-check button) finds the parts that no longer match
     their fingerprint, rebuilds them from the datasets in a temporary folder
     and compares: the differences are exactly your edits -- changed cells,
     added rows, deleted rows -- and they are applied to the dataset workbook
     that owns the row (by its activityName), listed in the review's Changes
     Applied, and a deleted row in its Deleted sheet.  The checks then run on
     the corrected datasets, and the splits are rebuilt.  If the datasets
     themselves changed since the part was built, the edits cannot be told
     apart from those changes and are not applied (the part is then kept in
     crop_datasets_new/unapplied_edits/ by the rebuild, never lost).

Rows are matched by their content, so inserting or deleting rows elsewhere in
the part file is fine.  A row the dataset no longer has as it was (it changed
since the split was built) is reported and left alone, never guessed at.

    & .venv\\Scripts\\python.exe split_edits.py --project "grass, AR" --rebuild
    & .venv\\Scripts\\python.exe split_edits.py --project "grass, AR" --apply
"""

import argparse
import contextlib
import difflib
import glob
import hashlib
import io
import os
import shutil
import sys
import tempfile

import pandas as pd

# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
from config_new_datasets import META_MODEL_DIR  # noqa: E402
sys.path.insert(0, META_MODEL_DIR)

from config_new_datasets import (BASE_DIR, complete_sheet_schema,  # noqa: E402
                                 describe_row, record_deletions)

SPLIT_DIRNAME = 'crop_datasets_new'
# Earlier versions kept a copy of each part as built beside it.  No copy is
# kept now -- a fingerprint inside the part does that job -- and these folders
# are removed by the next rebuild; until then a part without a fingerprint is
# compared with its copy there.
_OLD_COPY_DIRNAMES = ('as_built', '.as_built')
UNAPPLIED_DIRNAME = 'unapplied_edits'
# The custom document property holding the fingerprint of a part as built.
STAMP = 'pipeline split fingerprint'
EDITABLE_SHEETS = ('meta', 'exchanges', 'properties', 'parameters')
KIND = 'edited in the split Excel'
# What a row IS, whatever its values: a row edited in place keeps these.
IDENTITY_COLUMNS = ('activityName', 'geography', 'group', 'name', 'compartment',
                    'subcompartment', 'propertyName', 'variableName')


def split_dir(project: str) -> str:
    return os.path.join(BASE_DIR, 'output', project, SPLIT_DIRNAME)


def _parts(project: str) -> list:
    return sorted(p for p in glob.glob(os.path.join(split_dir(project), '*.xlsx'))
                  if not os.path.basename(p).startswith('~$'))


def _old_copy(part: str) -> str | None:
    """The copy an earlier version kept of a part as built, if it is there."""
    for dirname in _OLD_COPY_DIRNAMES:
        path = os.path.join(os.path.dirname(part), dirname, os.path.basename(part))
        if os.path.isfile(path):
            return path
    return None


# ── The fingerprint of a part as built ──────────────────────────────────────
# What a rebuild wrote, as one hash of the editable sheets' cells, kept in the
# part's own document properties (File > Info > Properties > Advanced >
# Custom in Excel, which keeps it on save).  A part whose cells no longer give
# it has been edited; a reference rebuilt from the datasets that still gives
# it shows the datasets have not changed since, so the differences from it are
# the edits and nothing else.

def _fingerprint(path: str) -> str:
    book = pd.read_excel(path, sheet_name=None)
    digest = hashlib.sha1()
    for sheet in EDITABLE_SHEETS:
        frame = book.get(sheet)
        if frame is None:
            continue
        columns = list(frame.columns)
        digest.update(repr((sheet, columns)).encode('utf-8'))
        for _, row in frame.iterrows():
            digest.update(repr(_key(row, columns)).encode('utf-8'))
    return digest.hexdigest()


def _stamp_of(part: str) -> str | None:
    import openpyxl
    try:
        props = openpyxl.load_workbook(part).custom_doc_props
    except Exception:
        return None
    return next((p.value for p in props if p.name == STAMP), None)


def _stamp(part: str, value: str) -> None:
    import openpyxl
    from openpyxl.packaging.custom import StringProperty
    book = openpyxl.load_workbook(part)
    props = book.custom_doc_props
    if STAMP in props.names:
        del props[STAMP]
    props.append(StringProperty(name=STAMP, value=value))
    book.save(part)


def _edited(part: str) -> bool:
    """Has the part been changed since it was built?"""
    stamp = _stamp_of(part)
    if stamp is not None:
        return _fingerprint(part) != stamp
    old = _old_copy(part)
    return bool(old and edits_in(part, old))


@contextlib.contextmanager
def _reference(project: str):
    """{part name: path} -- the parts as the datasets give them now, built in a
    temporary folder that is deleted afterwards."""
    import meta_model_new as model
    with tempfile.TemporaryDirectory() as folder:
        with contextlib.redirect_stdout(io.StringIO()):     # not a rebuild to report
            _parts_built, failed = model.rebuild_splits(project, into=folder)
        yield {} if failed else {os.path.basename(p): p
                                 for p in glob.glob(os.path.join(folder, '*.xlsx'))}


# ── Edits still in Excel ────────────────────────────────────────────────────
# Re-check reads the part files as saved.  An edit still open in Excel, or
# typed and then closed with "Don't Save", is not in the file: re-check would
# find no edits and rebuild the part, and the edit would be gone from disk.

def _excel_unsaved_copies(part: str) -> list:
    """Excel's own copies of a part's unsaved changes (Unsaved / Autorecovered
    .xlsb under %APPDATA%\\Microsoft\\Excel), newer than the part on disk."""
    appdata = os.environ.get('APPDATA')
    if not appdata:
        return []
    from urllib.parse import unquote
    stem = os.path.splitext(os.path.basename(part))[0]
    saved = os.path.getmtime(part)
    copies = glob.glob(os.path.join(glob.escape(os.path.join(appdata, 'Microsoft', 'Excel')),
                                    '*', '*.xlsb'))
    # 'grass,%20AR%20-%20part%201((Unsaved-...)).xlsb': Excel writes the name
    # URL-encoded; matched up to the '((' so part 1 does not take part 10's.
    return sorted(c for c in copies if unquote(os.path.basename(c)).startswith(stem + '((')
                  and os.path.getmtime(c) > saved)


def unsaved_in_excel(project: str) -> list:
    """What stops a re-check: a part open in Excel, or with changes Excel kept
    but never saved to the part.  One line each."""
    from datetime import datetime
    problems = []
    for part in _parts(project):
        name = os.path.basename(part)
        if os.path.isfile(os.path.join(os.path.dirname(part), '~$' + name)):
            problems.append(f'{name} is open in Excel -- save and close it')
        for copy in _excel_unsaved_copies(part):
            when = datetime.fromtimestamp(os.path.getmtime(copy)).strftime('%Y-%m-%d %H:%M')
            problems.append(f'{name} has changes Excel kept but never saved ({when}): {copy} '
                            f'-- open it, save it over the part, or delete it if they are not wanted')
    return problems


# ── Building the splits, and keeping them as built ──────────────────────────

def rebuild(project: str) -> int:
    """Rebuild the split Excels from the project's datasets, each stamped with
    its fingerprint as built.  Returns the parts written."""
    import meta_model_new as model
    # The previous parts first: a project that now needs fewer would keep the
    # extra ones as orphans holding old data.
    for old in _parts(project):
        if _edited(old):
            # Edited but never carried into the datasets (Re-check does that):
            # kept, not thrown away by the rebuild.
            from datetime import datetime
            keep = os.path.join(split_dir(project), UNAPPLIED_DIRNAME,
                                datetime.now().strftime('%Y-%m-%d %H-%M-%S'))
            os.makedirs(keep, exist_ok=True)
            shutil.copy2(old, keep)
            print(f'  {os.path.basename(old)} had edits not carried into the datasets -- '
                  f'kept in {keep}; to apply them, copy it back over the rebuilt part '
                  f'and press Re-check')
        try:
            os.remove(old)
        except OSError as exc:
            print(f'  {os.path.basename(old)} could not be replaced ({exc}) -- close it in Excel')
            return 0
    parts, failed = model.rebuild_splits(project)
    if failed:
        print(f'  split Excels of {project!r} not rebuilt: {failed[0]}')
        return 0
    for part in _parts(project):
        _stamp(part, _fingerprint(part))
    for dirname in _OLD_COPY_DIRNAMES:
        shutil.rmtree(os.path.join(split_dir(project), dirname), ignore_errors=True)
    print(f'  split Excels of {project!r}: {parts} part file(s) rebuilt from the datasets')
    return parts


# ── Finding the edits ───────────────────────────────────────────────────────

def _cell(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    if isinstance(value, float):
        return format(value, '.12g')
    return str(value).strip()


def _key(row, columns) -> tuple:
    return tuple(_cell(row.get(c)) for c in columns)


def edits_in(part: str, built: str) -> list:
    """[(sheet, before row | None, after row | None)] -- one per changed,
    added (before None) or deleted (after None) row of one part file, against
    the same part as built."""
    try:
        old_book = pd.read_excel(built, sheet_name=None)
        new_book = pd.read_excel(part, sheet_name=None)
    except Exception as exc:
        print(f'  {os.path.basename(part)} could not be read ({exc}) -- skipped')
        return []
    edits = []
    for sheet in EDITABLE_SHEETS:
        old, new = old_book.get(sheet), new_book.get(sheet)
        if old is None or new is None:
            continue
        if list(old.columns) != list(new.columns):
            print(f'  {os.path.basename(part)} [{sheet}]: its columns were changed -- '
                  f'edit cells and rows, not the columns; skipped')
            continue
        columns = list(old.columns)
        a = [_key(r, columns) for _, r in old.iterrows()]
        b = [_key(r, columns) for _, r in new.iterrows()]
        matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
        gone, came = [], []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != 'equal':
                gone += [old.iloc[i] for i in range(i1, i2)]
                came += [new.iloc[j] for j in range(j1, j2)]
        # A row whose cells changed reads as one gone and one come: paired
        # again by what it is, it is a change, not a deletion and an addition.
        identity = [c for c in IDENTITY_COLUMNS if c in columns]
        for before in gone:
            twin = next((i for i, after in enumerate(came)
                         if _key(after, identity) == _key(before, identity)), None)
            if twin is None:
                edits.append((sheet, before, None))
            else:
                edits.append((sheet, before, came.pop(twin)))
        edits.extend((sheet, None, after) for after in came)
    return edits


# ── Applying them to the datasets ───────────────────────────────────────────

def _owners(project: str) -> dict:
    """{activityName: dataset workbook path}."""
    import meta_model_new as model
    owners = {}
    for path in model.dataset_files(project):
        try:
            meta = pd.read_excel(path, sheet_name='meta')
        except Exception:
            continue
        for name in meta.get('activityName', pd.Series(dtype=object)).dropna():
            owners[str(name).strip()] = path
    return owners


def _find(frame, row, columns) -> int | None:
    """The index of the frame's row equal to `row` on the shared columns."""
    want = _key(row, columns)
    for index, candidate in frame.iterrows():
        if _key(candidate, columns) == want:
            return index
    return None


def apply(project: str) -> dict:
    """Carry the edits of every part file into the dataset workbooks.
    Returns {dataset: [what changed]}."""
    parts = [p for p in _parts(project) if _edited(p)]
    if not parts:
        return {}
    edited = {}
    with _reference(project) as reference:
        for part in parts:
            name = os.path.basename(part)
            stamp = _stamp_of(part)
            built = _old_copy(part) if stamp is None else reference.get(name)
            if stamp is not None and (built is None or _fingerprint(built) != stamp):
                # The datasets changed after the part was built (corrected by
                # hand, say): the part's own edits cannot be told from those
                # changes, and applying the difference would undo them.
                print(f'  {name}: the datasets changed since this part was built, so its '
                      f'edits cannot be told apart from those changes -- not applied. '
                      f'Redo them in the rebuilt part and re-check.')
                continue
            edited[part] = edits_in(part, built)
    edits = [e for part_edits in edited.values() for e in part_edits]
    if not edits:
        return {}
    owners = _owners(project)
    books: dict = {}                       # path -> sheets, loaded once
    changes: dict = {}
    skipped = []

    def book(path):
        if path not in books:
            books[path] = pd.read_excel(path, sheet_name=None)
        return books[path]

    for sheet, before, after in edits:
        source = before if before is not None else after
        activity = _cell(source.get('activityName'))
        path = owners.get(activity)
        if not path:
            skipped.append(f'[{sheet}] {describe_row(source)}: no dataset has activity {activity!r}')
            continue
        frame = book(path).get(sheet)
        if frame is None:
            skipped.append(f'[{sheet}] {describe_row(source)}: the dataset has no {sheet} sheet')
            continue
        columns = [c for c in frame.columns if c in source.index]
        dataset = os.path.basename(path)
        log = changes.setdefault(dataset, [])
        if after is None:                                       # a deleted row
            index = _find(frame, before, columns)
            if index is None:
                skipped.append(f'[{sheet}] {describe_row(before)}: not in {dataset} as it was')
                continue
            record_deletions(path, 'split Excel', sheet, frame.loc[[index]],
                             'row deleted in the split Excel')
            book(path)[sheet] = frame.drop(index=index).reset_index(drop=True)
            log.append(f'[{sheet}] deleted: {describe_row(before)}')
        elif before is None:                                    # an added row
            added = pd.DataFrame([{c: after.get(c) for c in frame.columns if c in after.index}])
            book(path)[sheet] = pd.concat([frame, added], ignore_index=True)
            log.append(f'[{sheet}] added: {describe_row(after)}')
        else:                                                   # changed cells
            index = _find(frame, before, columns)
            if index is None:
                skipped.append(f'[{sheet}] {describe_row(before)}: not in {dataset} as it was')
                continue
            cells = [c for c in columns if _cell(before.get(c)) != _cell(after.get(c))]
            for column in cells:
                if frame[column].dtype != object:
                    frame[column] = frame[column].astype(object)
                frame.at[index, column] = after.get(column)
            log.append(f'[{sheet}] {describe_row(before)}: ' + '; '.join(
                f'{c} {_cell(before.get(c))!r} -> {_cell(after.get(c))!r}' for c in cells))

    for path, sheets in books.items():
        complete_sheet_schema(sheets)
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            for name, frame in sheets.items():
                frame.to_excel(writer, sheet_name=name, index=False)
    changes = {d: lines for d, lines in changes.items() if lines}
    _log_in_review(project, changes)
    # Applied, the edits are part of the datasets: the part is stamped as it
    # now is, or the rebuild would find them again and keep the part as
    # unapplied.  With any edit left unapplied the stamp stays, so the rebuild
    # keeps the part and the edit is not lost.
    if not skipped:
        for part, part_edits in edited.items():
            if part_edits:
                _stamp(part, _fingerprint(part))

    print(f'  split Excel edits of {project!r}: {sum(map(len, changes.values()))} applied '
          f'to {len(changes)} dataset(s)')
    for dataset, lines in changes.items():
        print(f'    {dataset}')
        for line in lines[:12]:
            print(f'      {line[:160]}')
        if len(lines) > 12:
            print(f'      ... and {len(lines) - 12} more (Changes Applied in the review)')
    for line in skipped:
        print(f'    NOT applied: {line[:180]}')
    return changes


def _log_in_review(project: str, changes: dict) -> None:
    """Every edit applied, in the review's Changes Applied and Corrections."""
    if not changes:
        return
    try:
        import review_workbook
        review_workbook.append_changes(project, applied={
            f'{project}||{dataset}|split Excel|{KIND}': ('; '.join(lines), dataset)
            for dataset, lines in changes.items()})
    except Exception as exc:
        print(f'  (the review workbook was not updated: {exc})')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--project', required=True)
    parser.add_argument('--rebuild', action='store_true',
                        help='rebuild the split Excels from the datasets, kept as built')
    parser.add_argument('--apply', action='store_true',
                        help='carry the edits made in the split Excels into the datasets')
    args = parser.parse_args()
    if args.apply:
        apply(args.project)
    if args.rebuild:
        rebuild(args.project)
    return 0


if __name__ == '__main__':
    sys.exit(main())
