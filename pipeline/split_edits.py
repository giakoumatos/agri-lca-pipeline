"""
Correct many datasets at once, in the split Excels.

The split Excels (output/<project>/crop_datasets_new/agri_diesel_new_output_
part_N.xlsx) hold every dataset of a project in one workbook per chunk, so one
Find & Replace, one column filled down or a row deleted corrects every
dataset it touches.  This carries such edits back into the dataset workbooks
they belong to:

  1. Every pipeline run and every re-check ends by rebuilding the splits from
     the final datasets (`--rebuild`), and keeps a copy of each part as built
     (crop_datasets_new/as_built/).
  2. You edit a part file and save it.
  3. recheck.py (or the Re-check button) compares each part with its as-built
     copy: the differences are exactly your edits -- changed cells, added rows,
     deleted rows -- and they are applied to the dataset workbook that owns the
     row (by its activityName), listed in the review's Changes Applied, and a
     deleted row in its Deleted sheet.  The checks then run on the corrected
     datasets, and the splits are rebuilt.

Rows are matched by their content, so inserting or deleting rows elsewhere in
the part file is fine.  A row the dataset no longer has as it was (it changed
since the split was built) is reported and left alone, never guessed at.

    & .venv\\Scripts\\python.exe split_edits.py --project "grass, AR" --rebuild
    & .venv\\Scripts\\python.exe split_edits.py --project "grass, AR" --apply
"""

import argparse
import difflib
import glob
import os
import shutil
import sys

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
AS_BUILT_DIRNAME = 'as_built'
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


def _as_built(part: str) -> str:
    return os.path.join(os.path.dirname(part), AS_BUILT_DIRNAME, os.path.basename(part))


# ── Building the splits, and keeping them as built ──────────────────────────

def rebuild(project: str) -> int:
    """Rebuild the split Excels from the project's datasets and keep a copy of
    each part as built.  Returns the parts written."""
    import meta_model_new as model
    # The previous parts first: a project that now needs fewer would keep the
    # extra ones as orphans holding old data.
    for old in _parts(project):
        if edits_in(old):
            # Edited but never carried into the datasets (Re-check does that):
            # kept, not thrown away by the rebuild.
            from datetime import datetime
            keep = os.path.join(split_dir(project), 'unapplied_edits',
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
    keep = os.path.join(split_dir(project), AS_BUILT_DIRNAME)
    shutil.rmtree(keep, ignore_errors=True)
    os.makedirs(keep)
    for part in _parts(project):
        shutil.copy2(part, _as_built(part))
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


def edits_in(part: str) -> list:
    """[(sheet, before row | None, after row | None)] -- one per changed,
    added (before None) or deleted (after None) row of one part file."""
    built = _as_built(part)
    if not os.path.isfile(built):
        return []
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
    parts = _parts(project)
    if parts and not any(os.path.isfile(_as_built(p)) for p in parts):
        print(f'  split Excels of {project!r} have no as-built copy yet, so edits cannot '
              f'be told apart -- run the pipeline (or a re-check) once first')
        return {}
    edits = [e for part in parts for e in edits_in(part)]
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
