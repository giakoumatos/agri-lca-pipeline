"""
Verify the pipeline's output

Answers one question -- "is what is in output/ correct and complete?" -- without
changing anything, so it can be run at any time, on any project.

    python verify_output.py                       # every project
    python verify_output.py --project "Olive,TN"  # one project
    python verify_output.py --details             # list every finding, not a count
    python verify_output.py --fix                 # re-run the steps that are missing or stale

What it checks, per dataset:

  COMPLETE   every field operation that has a diesel intensity has its
             "diesel used in, ..." row, and no pesticide is left half
             redistributed (some compartments OLCApest, some not).  A dataset
             that went through step 1 but never step 3 looks finished; this is
             what catches it.

  FRESH      the .spold and the split Excel are newer than the workbook they
             were built from, and the split actually contains this dataset's
             activities.  A correction that stops at the workbook leaves them
             contradicting it.

  INTEGRITY  no blank or activity-less rows; every activityName in the
             exchanges exists in meta; the emitted mass of each substance
             matches the input (redistribution preserves mass) or, for a
             substance the model generated, matches the applied dose; and the
             converter's inconsistency report is empty.

  INPUT      rows in the SOURCE workbook that are blank or name neither an
             activity nor a flow.  They are dropped on every run, so they are
             reported here once rather than being a surprise each time.

Exit code is 0 when everything passes and 1 when anything fails, so it can gate
a delivery.

Author: Andreas Giakoumatos
Last Updated: 2026-09-23
"""

import os
import re
import sys
import glob
import argparse
import subprocess

import pandas as pd

# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
_BASE_DIR = _ROOT
sys.path.insert(0, _BASE_DIR)

from config_new_datasets import FO_DIESEL_INTENSITIES, PIPELINE, PIPELINE_PYTHON

# The field operations that have a diesel intensity, whatever the geography.
_FO_WITH_DIESEL = {name for name, _geo in FO_DIESEL_INTENSITIES}

# The four compartments OLCApest distributes a pesticide into.  A substance
# emitted into some of these AND into something else has been half handled.
_OLCAPEST_SUBC = {'agricultural', 'forestry', 'surface water',
                  'non-urban air or from high stacks'}

# Not datasets: the calculators' own aggregates, the meta model's sheets, the
# converter's report, and Excel's lock files.
_NOT_A_DATASET = ('~', 'meta_model_', 'master_data')


_PESTICIDE_NAMES: set | None = None


def pesticide_names() -> set:
    """
    Every substance the OLCApest mapping sheet classes as a pesticide,
    lowercased.  Loaded once, from the same cache the calculator uses, so
    that a metal emission is never mistaken for a half-split pesticide.
    """
    global _PESTICIDE_NAMES
    if _PESTICIDE_NAMES is None:
        try:
            sys.path.insert(0, os.path.join(_BASE_DIR,
                                            'calculators', 'olcapest'))
            import read_pesticide_data_new as olcapest
            from config_new_datasets import OLCAPEST_TOOL_FILE
            tool = olcapest.load_tool_data(OLCAPEST_TOOL_FILE)
            _PESTICIDE_NAMES = {str(n).strip().lower()
                                for n in tool['df_pesticides']['name']}
        except Exception as exc:
            print(f'  (could not read the OLCApest mapping sheet: {exc} -- '
                  f'the pesticide checks are skipped)')
            _PESTICIDE_NAMES = set()
    return _PESTICIDE_NAMES


def _is_dataset_workbook(path) -> bool:
    from config_new_datasets import is_review_workbook
    name = os.path.basename(path)
    return (not name.startswith(_NOT_A_DATASET)
            and not name.endswith('_new_output.xlsx')
            and not is_review_workbook(path))


def projects() -> list:
    root = os.path.join(_BASE_DIR, 'output')
    if not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root)
                  if os.path.isdir(os.path.join(root, name))
                  and name != 'meta_output')


def workbooks(project: str) -> list:
    pattern = os.path.join(_BASE_DIR, 'output', project, '*.xlsx')
    return [p for p in sorted(glob.glob(pattern)) if _is_dataset_workbook(p)]


def source_of(project: str, book: str):
    """The input dataset a workbook was built from, or None."""
    stem = os.path.splitext(os.path.basename(book))[0]
    for candidate in sorted(glob.glob(os.path.join(
            _BASE_DIR, 'input datasets', project, stem + '.*'))):
        if os.path.isfile(candidate):
            return candidate
    return None


def _mtime(path) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# ── The checks ──────────────────────────────────────────────────────────────
# Each returns a list of strings.  Empty means the dataset passed.

def check_complete(sheets: dict) -> list:
    """Did every step actually run on this dataset?"""
    problems = []
    exchanges = sheets.get('exchanges')
    if exchanges is None or exchanges.empty or 'group' not in exchanges.columns:
        return ['no exchanges sheet']

    tech = exchanges[exchanges['group'].astype(str) == 'FromTechnosphere']
    names = set(tech['name'].astype(str))
    # "diesel used in <operation>"; older outputs carry a comma after "in".
    diesel = {re.sub(r'^diesel used in,?\s+', '', n)
              for n in names if n.startswith('diesel used in')}
    missing = {n for n in names if n in _FO_WITH_DIESEL} - diesel
    for name in sorted(missing):
        problems.append(f"no diesel row for the field operation '{name}' "
                        f"-- the Agri Diesel step has not run on this dataset")

    env = exchanges[exchanges['group'].astype(str) == 'ToEnvironment']
    if not env.empty and 'subcompartment' in env.columns:
        known = pesticide_names()
        for substance, rows in env.groupby(env['name'].astype(str)):
            if substance.strip().lower() not in known:
                continue      # a metal or a nutrient: OLCApest never touches it
            subc = set(rows['subcompartment'].astype(str))
            outside = subc - _OLCAPEST_SUBC
            if outside and (subc & _OLCAPEST_SUBC):
                problems.append(f"the pesticide '{substance}' is partly split "
                                f"across the OLCApest compartments and partly "
                                f"emitted to {', '.join(sorted(outside))}")
            elif outside:
                problems.append(f"the pesticide '{substance}' is emitted to "
                                f"{', '.join(sorted(outside))} and was never "
                                f"redistributed -- the OLCApest step has not "
                                f"run on this dataset")
    return problems


def check_fresh(project: str, book: str) -> list:
    """Are the files built FROM this workbook newer than it?"""
    problems = []
    stem = os.path.splitext(os.path.basename(book))[0]
    book_time = _mtime(book)

    spold_dir = os.path.join(_BASE_DIR, 'output', project, 'spold_output')
    spold = (glob.glob(os.path.join(spold_dir, stem + '.spold'))
             + glob.glob(os.path.join(spold_dir, stem + '_*.spold')))
    if not spold:
        problems.append('no .spold has been written for this dataset')
    elif max(_mtime(p) for p in spold) < book_time - 1:
        problems.append(f'the .spold ({len(spold)} file(s)) is older than the '
                        f'workbook -- it does not carry the latest changes')

    split = split_parts(project)
    if not split:
        problems.append('no split Excel has been written for this project')
    elif max(_mtime(p) for p in split) < book_time - 1:
        problems.append('the split Excel is older than the workbook')
    return problems


def split_parts(project: str) -> list:
    """The project's split Excels -- not Excel's ~$ lock file of one that is open."""
    return [p for p in glob.glob(os.path.join(_BASE_DIR, 'output', project,
                                              'crop_datasets_new', '*.xlsx'))
            if not os.path.basename(p).startswith('~$')]


def check_split_covers(project: str, books: list) -> list:
    """Every activity of the project has to appear in the split."""
    split = split_parts(project)
    if not split:
        return []
    in_split = set()
    for part in split:
        try:
            frame = pd.read_excel(part, sheet_name='exchanges')
        except Exception as exc:
            return [f'the split part {os.path.basename(part)} cannot be read ({exc})']
        in_split |= set(frame['activityName'].dropna().astype(str))

    expected = set()
    for book in books:
        try:
            meta = pd.read_excel(book, sheet_name='meta')
        except Exception:
            continue
        expected |= set(meta['activityName'].dropna().astype(str))

    missing = expected - in_split
    extra = in_split - expected
    problems = []
    if missing:
        problems.append(f'{len(missing)} activity(ies) of this project are NOT '
                        f'in the split, e.g. {sorted(missing)[0][:60]!r}')
    if extra:
        problems.append(f'the split holds {len(extra)} activity(ies) that no '
                        f'dataset produces, e.g. {sorted(extra)[0][:60]!r}')
    return problems


def check_integrity(sheets: dict, source_sheets: dict | None) -> list:
    """Is the workbook internally sound, and is its mass accounted for?"""
    problems = []
    exchanges = sheets.get('exchanges')
    meta = sheets.get('meta')
    if exchanges is None or exchanges.empty:
        return ['no exchanges sheet']

    blank = int(exchanges.isna().all(axis=1).sum())
    if blank:
        problems.append(f'{blank} completely blank row(s)')
    if {'activityName', 'name'} <= set(exchanges.columns):
        orphan = int((exchanges['activityName'].isna()
                      & exchanges['name'].isna()).sum())
        if orphan:
            problems.append(f'{orphan} row(s) naming neither an activity nor a flow')
        nameless = int(exchanges['activityName'].isna().sum()) - orphan
        if nameless > 0:
            problems.append(f'{nameless} exchange(s) with no activityName')

    if meta is not None and not meta.empty and 'activityName' in exchanges.columns:
        known = set(meta['activityName'].dropna().astype(str))
        used = set(exchanges['activityName'].dropna().astype(str))
        for stray in sorted(used - known):
            problems.append(f"exchanges reference the activity '{stray[:50]}' "
                            f"which is not in the meta sheet")

    # Mass: OLCApest redistributes, so a substance present in both the source
    # and the output must weigh the same.  A substance the model GENERATED is
    # only in the output, and must weigh what was applied.
    if source_sheets and 'exchanges' in source_sheets:
        problems += _check_mass(exchanges, source_sheets['exchanges'])
    return problems


def _totals(frame, group):
    rows = frame[frame['group'].astype(str) == group]
    if rows.empty:
        return {}
    amounts = pd.to_numeric(rows['amount'], errors='coerce').fillna(0.0)
    return rows.assign(_a=amounts).groupby(
        rows['name'].astype(str))['_a'].sum().to_dict()


def _check_mass(out_exchanges, in_exchanges) -> list:
    problems = []
    try:
        emitted_out = _totals(out_exchanges, 'ToEnvironment')
        emitted_in = _totals(in_exchanges, 'ToEnvironment')
        applied = _totals(out_exchanges, 'FromTechnosphere')
    except KeyError as exc:
        return [f'the mass balance could not be checked: the source workbook has '
                f'no {exc} column (a blank header -- the calculator renames it at '
                f'run time, but it cannot be compared here)']
    except Exception as exc:
        return [f'the mass balance could not be checked ({exc})']

    applied_lower = {str(k).strip().lower(): v for k, v in applied.items()}

    for substance, mass_in in emitted_in.items():
        if substance not in emitted_out:
            problems.append(f"'{substance}' is emitted in the input "
                            f"({mass_in:.4g} kg) but not in the output")
            continue
        mass_out = emitted_out[substance]
        if mass_in and abs(mass_out / mass_in - 1.0) > 1e-6:
            problems.append(f"'{substance}' changed mass: {mass_in:.6g} kg in, "
                            f"{mass_out:.6g} kg out")

    for substance, mass_out in emitted_out.items():
        if substance in emitted_in:
            continue
        dose = applied_lower.get(str(substance).strip().lower())
        if dose is None:
            problems.append(f"'{substance}' is emitted ({mass_out:.4g} kg) but "
                            f"appears in neither the input nor the applied "
                            f"inputs -- where did it come from?")
        elif dose and abs(mass_out / dose - 1.0) > 1e-6:
            problems.append(f"'{substance}' was generated as {mass_out:.6g} kg "
                            f"but {dose:.6g} kg is applied")
    return problems


def check_source(source: str | None) -> list:
    """Rows in the INPUT workbook that the pipeline has to throw away."""
    if not source or not source.lower().endswith(('.xlsx', '.xls')):
        return []
    try:
        frame = pd.read_excel(source, sheet_name='exchanges')
    except Exception:
        return []
    junk = int(frame.isna().all(axis=1).sum())
    if {'activityName', 'name'} <= set(frame.columns):
        junk = int((frame.isna().all(axis=1)
                    | (frame['activityName'].isna() & frame['name'].isna())).sum())
    return [f'{junk} row(s) in the source workbook name neither an activity nor '
            f'a flow; they are dropped on every run'] if junk else []


def check_report(project: str) -> list:
    """The converter's own inconsistency report should be empty."""
    path = os.path.join(_BASE_DIR, 'output', project,
                        'master_data_inconsistencies.xlsx')
    if not os.path.isfile(path):
        return []
    try:
        frame = pd.read_excel(path)
    except Exception:
        return []
    if frame.empty:
        return []
    # One line per kind: a blank property and an unknown flow id are different
    # problems, and lumping them under "self-generated id" misnamed the first.
    counts = frame['type'].astype(str).value_counts() if 'type' in frame else {}
    return [f'{n} {kind} issue(s) -- see master_data_inconsistencies.xlsx'
            for kind, n in sorted(counts.items())]


def check_import_ids(project: str) -> list:
    """
    The exchanges the database import will reject, read from the .spold files.

    An exchange whose flow id or subcompartment id is not in Master Data --
    a name the converter could not match, so it made an id up -- fails the
    import's foreign keys (v3_valid_elementary_exchange, v3_valid_subcompartment
    ...).  The converter's own report is merged across runs and keeps issues
    that are long fixed, so this reads what the files contain now, against
    the same lookups the converter uses.  One line per flow and place, with
    the number of files carrying it.
    """
    import json
    from config_new_datasets import (STANDARD_ELEMENTARY_EXCHANGE_LOOKUP_FILE,
                                     STANDARD_INTERMEDIATE_EXCHANGE_LOOKUP_FILE,
                                     MASTER_DATA_LOOKUP_FILE, SUBCOMPARTMENT_ID_LOOKUP_FILE,
                                     UNIT_ID_LOOKUP_FILE)

    def ids(path, field=None):
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception:
            return set()
        return {v[field] if field else v for v in data.values()}

    known = {'elementaryExchange': ids(STANDARD_ELEMENTARY_EXCHANGE_LOOKUP_FILE),
             'intermediateExchange': ids(STANDARD_INTERMEDIATE_EXCHANGE_LOOKUP_FILE)
             | ids(MASTER_DATA_LOOKUP_FILE, 'intermediateExchangeId')}
    subcompartments = ids(SUBCOMPARTMENT_ID_LOOKUP_FILE)
    units = ids(UNIT_ID_LOOKUP_FILE)
    exchange = re.compile(r'<(elementaryExchange|intermediateExchange) ([^>]*)>(.*?)</\1>', re.S)
    flow_id_of = re.compile(r'(?:elementaryExchangeId|intermediateExchangeId)="([0-9a-f-]{36})"')
    unit_id_of = re.compile(r'\bunitId="([0-9a-f-]{36})"')
    name_of = re.compile(r'<name xml:lang="en">([^<]*)</name>')
    place_of = re.compile(r'<compartment subcompartmentId="([0-9a-f-]{36})">\s*'
                          r'<compartment xml:lang="en">([^<]*)</compartment>\s*'
                          r'<subcompartment xml:lang="en">([^<]*)</subcompartment>')
    rejected: dict = {}
    for path in glob.glob(os.path.join(_BASE_DIR, 'output', project, 'spold_output', '*.spold')):
        try:
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
        except OSError:
            continue
        flow_data = text.split('<usedUserMasterData', 1)[0]
        for kind, attributes, body in exchange.findall(flow_data):
            flow_id = (flow_id_of.search(attributes) or [None, ''])[1]
            unit_id = (unit_id_of.search(attributes) or [None, ''])[1]
            name = (name_of.search(body) or [None, '?'])[1]
            place = place_of.search(body)
            why = []
            if flow_id not in known[kind]:
                why.append('flow id')
            if unit_id and unit_id not in units:
                why.append('unit id')
            if place and place.group(1) not in subcompartments:
                why.append('subcompartment id')
            if why:
                where = f' in {place.group(2)} / {place.group(3)}' if place else ''
                rejected.setdefault((name + where, ' and '.join(why)), set()).add(path)
    return [f'{name} -- {why} not in Master Data, {len(files)} file(s)'
            for (name, why), files in sorted(rejected.items())]


# ── Repair ──────────────────────────────────────────────────────────────────

def _step(basename: str):
    for display, path in PIPELINE:
        if os.path.basename(path) == basename:
            return display, path
    return None, None


def repair(project: str, filename: str, from_step: str) -> bool:
    """
    Re-run the pipeline for one dataset, starting at `from_step`.

    Only the existing steps are called, with the dataset named explicitly --
    nothing is edited directly, so a repair is exactly what a normal run does.
    """
    import json
    order = [os.path.basename(p) for _d, p in PIPELINE]
    if from_step not in order:
        return False
    batch = json.dumps([[project, filename]])
    for basename in order[order.index(from_step):]:
        display, path = _step(basename)
        if not path:
            continue
        print(f'      running {display} ...')
        result = subprocess.run([PIPELINE_PYTHON, path, '--batch', batch],
                                cwd=_BASE_DIR, check=False,
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        if result.returncode != 0:
            print(f'      {display} FAILED (exit {result.returncode})')
            return False
    return True


# ── Driver ──────────────────────────────────────────────────────────────────

def verify(project: str, details: bool, fix: bool) -> tuple:
    books = workbooks(project)
    if not books:
        return 0, 0
    print()
    print(f'  {project}   ({len(books)} dataset(s))')

    failed = 0
    needs = []           # (filename, first step to re-run)
    for book in books:
        name = os.path.basename(book)
        try:
            sheets = pd.read_excel(book, sheet_name=None)
        except Exception as exc:
            print(f'    {name[:60]:<62} UNREADABLE ({exc})')
            failed += 1
            continue

        source = source_of(project, book)
        source_sheets = None
        if source and source.lower().endswith(('.xlsx', '.xls')):
            try:
                source_sheets = pd.read_excel(source, sheet_name=None)
            except Exception:
                source_sheets = None

        groups = {
            'complete':  check_complete(sheets),
            'fresh':     check_fresh(project, book),
            'integrity': check_integrity(sheets, source_sheets),
            'source':    check_source(source),
        }
        if source is None:
            groups['source'] = ['no input dataset of this name -- the pipeline '
                                'did not build this workbook, and it is left out '
                                'of the split']

        total = sum(len(v) for v in groups.values())
        state = 'ok' if total == 0 else f'{total} finding(s)'
        marks = ' '.join(f'{k}:{"ok" if not v else len(v)}'
                         for k, v in groups.items())
        print(f'    {name[:56]:<58} {state:<14} {marks}')
        if total:
            failed += 1
            if details:
                for kind, items in groups.items():
                    for item in items:
                        print(f'        [{kind}] {item}')
            # Where to restart depends on what is wrong.  Blank rows and
            # orphan exchanges are dropped when the input is READ, so those
            # need the whole chain; a missing diesel row needs everything
            # from the calculators on; a stale .spold only needs converting.
            rereads = any('blank' in item or 'naming neither' in item
                          or 'no activityName' in item
                          for item in groups['integrity'])
            if source and rereads:
                needs.append((os.path.basename(source),
                              'read_pesticide_data_new.py'))
            elif source and groups['complete']:
                needs.append((os.path.basename(source),
                              'multinutrient_calculator_new.py'))
            elif source and groups['fresh']:
                needs.append((os.path.basename(source),
                              'spold_converter_new.py'))

    for item in check_split_covers(project, books):
        print(f'    [split]  {item}')
        failed += 1
    for item in check_report(project):
        print(f'    [spold]  {item}')
    blocked = check_import_ids(project)
    if blocked:
        print(f'    [import] {len(blocked)} exchange(s) the database import will reject '
              f'(id not in Master Data):')
        for item in blocked:
            print(f'      {item}')
        failed += 1

    if fix and needs:
        print(f'    repairing {len(needs)} dataset(s):')
        for filename, from_step in needs:
            print(f'      {filename[:58]}')
            repair(project, filename, from_step)

    return len(books), failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Check that the pipeline output is complete and current.')
    parser.add_argument('--project', default=None,
                        help='one project under output/ (default: all)')
    parser.add_argument('--details', action='store_true',
                        help='list every finding instead of counting them')
    parser.add_argument('--fix', action='store_true',
                        help='re-run the steps that are missing or stale')
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    chosen = [args.project] if args.project else projects()
    print('=' * 78)
    print('VERIFY OUTPUT')
    print('=' * 78)

    datasets = failures = 0
    for project in chosen:
        if not os.path.isdir(os.path.join(_BASE_DIR, 'output', project)):
            print(f'  project not found: {project}')
            return 1
        count, failed = verify(project, args.details, args.fix)
        datasets += count
        failures += failed

    print()
    print('-' * 78)
    if failures:
        print(f'  {failures} of {datasets} dataset(s) have findings.'
              + ('' if args.details else '  Re-run with --details to see them.'))
        print('  --fix re-runs the steps that are missing or stale.')
    else:
        print(f'  All {datasets} dataset(s) complete, current and consistent.')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
