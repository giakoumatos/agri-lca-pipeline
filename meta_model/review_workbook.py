"""
One review workbook per project: every check, every dataset, one answer per error.

The meta model used to leave three files per project -- a findings report, a
corrections sheet and a fix sheet -- each listing the same error once per
dataset.  A project of 130 datasets built from one template repeats most of
its errors 130 times, so the pattern was hidden and the same answer had to be
typed over and over.  This writes ONE workbook instead, in the format of the
team's two review skills (checkdataset and livestock-ecoinvent-check): a
written verdict and a severity-ordered action list in which each distinct
error appears once, with every dataset it affects.

    output/<project>/<project> review.xlsx      (beside the datasets)

The layout is the livestock-ecoinvent-check skill's report; the verdict and
checklist sheets are drawn by consolidate_reviews.py (copied unchanged from
the checkdataset skill) from a results.json-shaped dict per dataset:

  Verdict              per-dataset status, common to all, must fix, decisions,
                       resolved -- the written verdict, as the first sheet
  Activities overview  each dataset by name, with counts per severity and a verdict
  Action_list          #, Severity, Area, Issue, Detail -- one row per distinct
                       error, with its datasets, in severity order; and the
                       INPUT: `your_fix` answers it for every dataset on the row
                       (`choices`, `proposed_fix`)
  Structure            each activity: REF / BYPROD / INPUT, amount, unit, price,
                       the 6 mandatory properties
  Naming_check         the activity name, products and inputs, each with its status
  Emissions            the elementary flows, compartment / subcompartment, status
  Properties_check     each product x the 6 mandatory properties and price,
                       with the property equations
  Facts                the facts the comments are built from, per activity, and
                       where each comes from: the provider's facts sheet or the
                       dataset itself (meta_facts.py)
  Review_vs_decisions  one row per failing checklist item, with its datasets
  Checklist status     every row of the internal review checklist x dataset
  Changes Applied      everything written to the datasets (append-only)
  Deleted              every row, cell, column or sheet the meta model or a
                       calculator removed from a dataset, with its content and
                       why (from deletions_log.json beside the datasets)
  Corrections          INPUT -- the cell changes the model makes, one row per
                       distinct before -> after, `apply` = no rejects it everywhere
  All findings         every finding of every dataset, for the record
  _log, _register      (hidden) the change log and the corrections register

"Same error": the same field, the same issue and the same current value, and
it is answered ONCE for every dataset it affects.  Only where the answer
depends on the dataset's own figures (DATASET_SPECIFIC_KINDS: a yield, a
period, an allocation factor) does the error keep one number but get one
answer line per dataset (12-1, 12-2, ...).

Severity scale (both skills): Critical, High, Verify, Medium, Decision, Low.
  BLOCKING -> Critical          a finding offering choices -> Decision
  ACCEPTABLE_FOR_NOW -> Medium  WARNING not fixed -> High    INFO -> Verify
Fixed findings are not action items: they are in Changes Applied.
"""

import os
import re
import collections
from datetime import datetime

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
from config_new_datasets import CHECKLIST_FILE           # config/review/
CHECKLIST_SHEETS = (('Checks', 'Checks'), ('agri related checks', 'agri'))

ACTION_SHEET = 'Action_list'
CORRECTIONS_SHEET = 'Corrections'
CHANGES_SHEET = 'Changes Applied'

SEV_ORDER = {'Critical': 0, 'High': 1, 'Verify': 2, 'Medium': 3, 'Decision': 4, 'Low': 5}
SEV_FILL = {'Critical': 'FFC7CE', 'High': 'FFD966', 'Verify': 'FFF2CC',
            'Medium': 'FFF2CC', 'Decision': 'D9E1F2', 'Low': 'E7E6E6'}
STATUS_FILL = {'OK': 'D9EAD3', 'CORRECTED': 'CFE2F3', 'FAIL': 'F4CCCC',
               'VERIFY': 'FFF2CC', '-': 'FFFFFF'}
INPUT_FILL = 'FFFDE7'

WRAP = Alignment(wrap_text=True, vertical='top')
BOLD = Font(bold=True)

# The checklist rows the model's checks address.  Each finding names its row
# (Finding.checklist_row); these are every value those carry.  A row listed
# here with no finding for a dataset is OK for it; a row not listed is
# "not checked by the model" -- never OK by default.  Add a check's row here
# when the check is added.
MODEL_CHECKED_ROWS = (
    '14 (agri)', '26 (agri)', '28-34 (Checks) / 3 (agri)', '28-59 (Checks)',
    '35-38 (Checks)', '42 (Checks)', '45-46 (Checks)', '18 (agri)', '20 (agri)',
    # the cross-field checks (meta_model_new.cross_field_checks)
    '9 (Checks) / 5 (agri)', '85 (Checks)', '48 (Checks)',
)
from skill_checks import CHECKED_ROWS as _SKILL_ROWS     # the ported skill checks
from skill_checks import MANDATORY_PROPERTIES
MODEL_CHECKED_ROWS = tuple(dict.fromkeys(MODEL_CHECKED_ROWS + _SKILL_ROWS))


# Errors whose answer depends on the dataset's own figures -- each crop has
# its own yield, each dataset its own period and allocation factors -- so one
# answer cannot serve them all.  Every other error is answered once for every
# dataset it affects.
DATASET_SPECIFIC_KINDS = (
    'yield line missing',
    'the dates and timePeriodComment disagree',
    'stated allocation factor differs from the computed one',
    'the dates do not cover the generalComment period',
)


# The Checks rows of each metadata field (the checkdataset skill's
# references/field_mapping.md).  Several checks tag their findings with a
# broad range ('28-59 (Checks)'); for the Checklist status a finding on a
# known field counts against that field's rows only, so one generalComment
# finding does not mark 32 rows FAIL.  Agri rows on the tag are kept.
FIELD_ROWS = {
    'activityName': '25-27 (Checks) / 2 (agri)',
    'generalComment': '28-34 (Checks)',
    'includedActivitiesStart': '35-36 (Checks)',
    'includedActivitiesEnd': '37-38 (Checks)',
    'synonyms': '39 (Checks)',
    'tags': '40 (Checks)',
    'allocationComment': '42 (Checks)',
    'ISIC': '45-46 (Checks)',
    'geography': '47 (Checks) / 2 (agri)',
    'Geography': '47-48 (Checks) / 2 (agri)',
    'geographyComment': '48 (Checks)',
    'technologyLevel': '49 (Checks)',
    'technologyComment': '50 (Checks)',
    'startDate': '51-52 (Checks)',
    'endDate': '51-52 (Checks)',
    'timePeriodComment': '54 (Checks)',
    'modellingAndValidation_percent': '55 (Checks)',
    'modellingAndValidation_samplingProcedure': '56-58 (Checks)',
    'modellingAndValidation_extrapolations': '59-60 (Checks)',
}


def finding_rows(finding) -> list:
    """The checklist rows a finding counts against."""
    tagged = parse_checklist_rows(finding.checklist_row)
    field = FIELD_ROWS.get(str(finding.column))
    if not field and str(finding.column).startswith('dataGeneratorAndPublication_'):
        field = '61-68 (Checks)'
    agri = [r for r in tagged if r[0] == 'agri']
    if 'yield' in str(finding.kind).lower() and agri:
        return agri                 # the yield is agri 3, none of the comment's own questions
    if not field:
        return tagged
    # The field's first row asks whether the field is right at all; the rows
    # after it ask narrower things (images, recontextualisation, ...) that
    # one finding on the field does not answer.
    return parse_checklist_rows(field)[:1] + agri


def _model():
    # The meta model as it is already running -- as __main__ when started
    # from the command line -- not a second copy imported beside it.
    import sys
    main = sys.modules.get('__main__')
    if main is not None and hasattr(main, 'finding_key') and hasattr(main, 'process_dataset'):
        return main
    import meta_model_new
    return meta_model_new


def review_path(project: str) -> str:
    return os.path.join(_model().meta_output_dir(project), f'{project} review.xlsx')


def fill(color: str) -> PatternFill:
    return PatternFill(start_color=color, end_color=color, fill_type='solid')


def squash(text) -> str:
    return ' '.join(str('' if text is None else text).split())


def severity_of(finding) -> str | None:
    """A finding on the skills' scale, or None when it is no action item."""
    if finding.fixed or finding.severity in ('PASS', 'MINOR'):
        return None
    if finding.severity in ('BLOCKING', 'ACCEPTABLE_FOR_NOW') and finding.options:
        return 'Decision'
    return {'BLOCKING': 'Critical', 'ACCEPTABLE_FOR_NOW': 'Medium',
            'WARNING': 'High', 'INFO': 'Verify'}.get(finding.severity)


def parse_checklist_rows(text) -> list:
    """'28-34 (Checks) / 3 (agri)' -> [('Checks', 28), ..., ('agri', 3)]."""
    rows = []
    for part in str(text or '').split('/'):
        match = re.match(r'\s*(\d+)(?:\s*-\s*(\d+))?\s*\((\w+)', part)
        if not match:
            continue
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        sheet = 'agri' if match.group(3).lower().startswith('agri') else 'Checks'
        rows.extend((sheet, n) for n in range(first, last + 1))
    return rows


def dataset_labels(datasets, findings=()) -> dict:
    """
    {workbook: the name a dataset goes by in the review}.

    Its activity name -- what a reviewer knows it as -- not a D1..Dn code.  A
    workbook holding several activities (Olive,TN), or two workbooks of the
    same activity name, go by the file name instead, so every name is unique.
    """
    activities = collections.defaultdict(set)
    for f in findings:
        if f.dataset and str(f.activity or '').strip():
            activities[f.dataset].add(str(f.activity).strip())
    names = {}
    for dataset in sorted(datasets):
        acts = activities.get(dataset, set())
        names[dataset] = next(iter(acts)) if len(acts) == 1 else os.path.splitext(dataset)[0]
    counts = collections.Counter(names.values())
    return {d: (n if counts[n] == 1 else os.path.splitext(d)[0]) for d, n in names.items()}


def _compact(labels: list) -> str:
    """The datasets of a row, by name, one per line."""
    return '\n'.join(sorted(labels))


# ── Grouping ────────────────────────────────────────────────────────────────

def group_findings(findings: list) -> list:
    """
    The action items, one group per distinct error, most severe first.

    Each group: severity, the shared finding fields, the findings in it, and
    whether one answer can serve every dataset in it.
    """
    groups: dict = collections.OrderedDict()
    for finding in findings:
        severity = severity_of(finding)
        if not severity:
            continue
        signature = (finding.column, finding.kind, squash(finding.found))
        group = groups.setdefault(signature, {
            'severity': severity, 'column': finding.column, 'kind': finding.kind,
            'found': finding.found, 'findings': [], 'shared': True})
        group['findings'].append(finding)
        if SEV_ORDER[severity] < SEV_ORDER[group['severity']]:
            group['severity'] = severity
    for group in groups.values():
        kind = str(group['kind']).lower()
        group['shared'] = (len(group['findings']) == 1 or not (
            kind in DATASET_SPECIFIC_KINDS or 'yield' in kind))
    return sorted(groups.values(), key=lambda g: (
        SEV_ORDER[g['severity']], -len({f.dataset for f in g['findings']}),
        str(g['kind']), str(g['column'])))


def _cell_keys(value) -> list:
    return [k.strip() for k in str(value or '').splitlines() if k.strip()]


def _read_sheet(project: str, sheet: str) -> pd.DataFrame:
    path = review_path(project)
    if not os.path.isfile(path):
        return pd.DataFrame()
    try:
        return pd.read_excel(path, sheet_name=sheet, dtype=object)
    except Exception:
        return pd.DataFrame()


def resolve_answer(typed, choices, proposal) -> str | None:
    """
    A typed answer as the value to apply, or None when it is not one yet.

    A bare number picks that option from the row's `choices`; "yes" takes
    the proposed fix -- the same rules the fix sheet had.
    """
    model = _model()
    if model._blank(typed):
        return None
    text = str(typed).strip()
    picks = model.parse_choices(choices)
    number = re.fullmatch(r'(\d+)(?:\.0+)?', text)
    if number and picks:
        wanted = str(int(number.group(1)))
        if wanted in picks:
            return picks[wanted]
        print(f'  ! {text!r} is not one of the {len(picks)} options -- row skipped')
        return None
    if text.lower() in model.ACCEPT_ANSWERS:
        return None if model._blank(proposal) else str(proposal)
    return text


def load_answers(project: str) -> dict:
    """{finding key: value} -- one answer fans out to every dataset on its row.

    A reusable answer (a flow name, a placement, a house wording) is also
    remembered, as the review window does, so it still holds after the row
    has left the sheet -- a --fresh pipeline run starts the datasets again
    from their input, and the question would otherwise come back."""
    model = _model()
    frame = _input_rows(project)
    answers = {}
    for _, row in frame.iterrows():
        value = resolve_answer(row.get('your_fix'), row.get('choices'),
                               row.get('proposed_fix'))
        if value is None:
            continue
        keys = _cell_keys(row.get('keys'))
        for key in keys:
            answers[key] = value
        if keys:
            _project, _, after = keys[0].partition('||')
            dataset, _, rest = after.partition('|')
            column, kind = model.split_key_rest(rest)
            try:
                model.remember_decision(column, kind, row.get('current_value'), value,
                                        project, dataset)
            except Exception:
                pass
    return answers


def load_approvals(project: str) -> dict:
    """{correction key: bool} -- one 'no' rejects that change in every dataset.

    From the corrections register (every correction ever made, so a 'no'
    holds on every later run), with what is typed in the Corrections sheet
    on top of it."""
    model = _model()
    decisions = {key: entry['apply'].lower() not in model.NO_ANSWERS
                 for key, entry in _register(project).items() if entry['apply']}
    frame = _read_sheet(project, CORRECTIONS_SHEET)
    for _, row in frame.iterrows():
        answer = str(row.get('apply', '') or '').strip().lower()
        for key in _cell_keys(row.get('keys')):
            decisions[key] = answer not in model.NO_ANSWERS
    return decisions


def _typed_before(project: str) -> tuple:
    """What was typed in the current workbook, keyed per dataset, so a rewrite
    (a new scan, a re-run) keeps every answer not yet applied."""
    typed, applies = {}, {}
    if not os.path.isfile(review_path(project)):
        return typed, applies
    for _, row in _input_rows(project).iterrows():
        if not _model()._blank(row.get('your_fix')):
            for key in _cell_keys(row.get('keys')):
                typed[key] = row.get('your_fix')
    for _, row in _read_sheet(project, CORRECTIONS_SHEET).iterrows():
        answer = str(row.get('apply', '') or '').strip()
        for key in _cell_keys(row.get('keys')):
            applies[key] = answer
    return typed, applies


# ── Writing, in the skills' format ─────────────────────────────────────────
# The workbook IS the skills' consolidated review: consolidate_reviews.py --
# copied unchanged from the checkdataset skill -- draws every one of its
# sheets from a per-dataset results.json-shaped dict built here from the
# model's findings.  Only additions follow it: the answer columns at the
# right of Specific items (one row per distinct error, one answer for every
# dataset on it), the Corrections sheet, All findings, and a hidden log that
# keeps Changes Applied append-only across runs.

SPECIFIC_SHEET = 'Specific items'

# ── The corrections register ────────────────────────────────────────────────
# Every correction the model has made to a dataset -- metadata cells, the
# automatic flow fixes, your answers -- one entry per dataset and field, kept
# across runs.  The pipeline starts each dataset afresh from its input and
# makes the same corrections again, and its final pass then finds nothing
# left to correct: without the register a run's corrections vanished from the
# Corrections sheet the moment they were made, and a 'no' had no row to go
# on.  Now they stay listed, with when they were first and last applied, and
# apply = no is honoured on every later run (rejected_keys()).

REGISTER_SHEET = '_register'
REGISTER_COLUMNS = ('key', 'type', 'dataset', 'field', 'issue', 'before', 'after',
                    'first_applied', 'last_applied', 'apply')
_REJECTED_CACHE: dict = {}


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    text = str(value)
    return '' if text.strip().lower() == 'nan' else text


def _register(project: str) -> dict:
    """{key: entry} as last saved in the workbook."""
    register = {}
    for _, row in _read_sheet(project, REGISTER_SHEET).iterrows():
        key = _text(row.get('key')).strip()
        if key:
            register[key] = {c: _text(row.get(c)) for c in REGISTER_COLUMNS}
    return register


def rejected_keys(project: str) -> set:
    """The corrections you set apply = no for -- every automatic fix checks
    this before it changes anything.  Read once per change of the workbook."""
    path = review_path(project)
    if not os.path.isfile(path):
        return set()
    stamp = os.path.getmtime(path)
    cached = _REJECTED_CACHE.get(project)
    if cached and cached[0] == stamp:
        return cached[1]
    keys = {key for key, ok in load_approvals(project).items() if not ok}
    _REJECTED_CACHE[project] = (stamp, keys)
    return keys


def _update_register(register: dict, committed, applied, answers, proposed, applies) -> dict:
    """Fold one run into the register: what it applied (dated), what it only
    proposed, and the apply yes/no typed in the Corrections sheet."""
    model = _model()
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def entry(key):
        return register.setdefault(key, {c: '' for c in REGISTER_COLUMNS} | {'key': key})

    for c in committed:
        if c.get('minor'):
            continue
        e = entry(c['key'])
        e.update(type='metadata', dataset=c.get('dataset', ''), field=c.get('column', ''),
                 issue=c.get('issues', ''), before=_text(c.get('before')),
                 after=_text(c.get('after')), last_applied=stamp)
        e['first_applied'] = e['first_applied'] or stamp
    for key, (description, dataset) in (applied or {}).items():
        _project, _, after = key.partition('||')
        _dataset, _, rest = after.partition('|')
        column, kind = model.split_key_rest(rest)
        e = entry(key)
        e.update(type='your answer' if key in (answers or {}) else 'automatic fix',
                 dataset=dataset, field=column, issue=kind, after=_text(description),
                 last_applied=stamp)
        e['first_applied'] = e['first_applied'] or stamp
    for c in proposed:
        if c.get('minor'):
            continue
        e = entry(c['key'])
        if not e['last_applied']:          # never applied: still a proposal
            e.update(type='metadata', dataset=c.get('dataset', ''), field=c.get('column', ''),
                     issue=c.get('issues', ''), before=_text(c.get('before')),
                     after=_text(c.get('after')))
    for key, answer in (applies or {}).items():
        if key in register and _text(answer).strip():
            register[key]['apply'] = _text(answer).strip()
    for e in register.values():
        e['apply'] = e['apply'] or 'yes'
    return register


def _status(entry) -> str:
    if entry['apply'].strip().lower() in _model().NO_ANSWERS:
        return 'rejected -- not applied'
    return 'applied' if entry['last_applied'] else 'proposed'


def _write_corrections_sheet(wb, register: dict, labels: dict) -> None:
    """The Corrections sheet, drawn from the register: the same change in the
    same field across datasets is one row, with one `apply` for all of them."""
    index = wb.sheetnames.index(CORRECTIONS_SHEET) if CORRECTIONS_SHEET in wb.sheetnames else None
    if index is not None:
        del wb[CORRECTIONS_SHEET]
    groups = collections.OrderedDict()
    order = {'metadata': 0, 'automatic fix': 1, 'your answer': 2}
    for e in sorted(register.values(), key=lambda e: (order.get(e['type'], 3), e['field'], e['dataset'])):
        # "(3 row(s))" counts a dataset's rows: the same fix in two datasets
        # is still one fix, with one apply.
        after = re.sub(r'\s*\(\d+ row\(s\)\)', '', squash(e['after']))
        signature = (e['type'], e['field'], e['issue'], squash(e['before']), after,
                     _status(e), e['apply'].strip().lower())
        groups.setdefault(signature, []).append(e)
    rows = []
    for number, (signature, members) in enumerate(groups.items(), 1):
        e = members[0]
        names = sorted({labels.get(m['dataset'], os.path.splitext(m['dataset'])[0]) for m in members})
        firsts = [m['first_applied'] for m in members if m['first_applied']]
        lasts = [m['last_applied'] for m in members if m['last_applied']]
        rows.append([number, e['type'], e['field'], e['issue'], e['before'][:2000], e['after'][:2000],
                     _compact(names), len(members), min(firsts) if firsts else '',
                     max(lasts) if lasts else '', _status(e), e['apply'],
                     '\n'.join(m['key'] for m in members)])
    ws = _sheet(wb, CORRECTIONS_SHEET,
                ['#', 'type', 'field', 'issue', 'before', 'after', 'datasets', 'n',
                 'first applied', 'last applied', 'status', 'apply', 'keys'],
                rows, [6, 14, 30, 34, 50, 50, 30, 5, 19, 19, 20, 8, 30],
                input_columns=('apply',), freeze='D2')
    ws.column_dimensions['M'].hidden = True
    if index is not None:
        wb.move_sheet(ws, offset=index - wb.sheetnames.index(CORRECTIONS_SHEET))


def _write_register_sheet(wb, register: dict) -> None:
    if REGISTER_SHEET in wb.sheetnames:
        del wb[REGISTER_SHEET]
    ws = _sheet(wb, REGISTER_SHEET, list(REGISTER_COLUMNS),
                [[e[c] for c in REGISTER_COLUMNS] for e in register.values()],
                [40, 14, 40, 30, 34, 40, 40, 19, 19, 8])
    ws.sheet_state = 'hidden'


def _only_new(history: pd.DataFrame, rows: list) -> list:
    """The changes that are new or changed since the log last saw them: the
    pipeline makes the same corrections on every run, and logging each again
    filled Changes Applied with duplicates."""
    last = {}
    if not history.empty:
        for _, h in history.iterrows():
            last[(_text(h.get('dataset')), _text(h.get('field')), _text(h.get('issue')))] = \
                squash(_text(h.get('after')))
    fresh = []
    for row in rows:
        key = (_text(row.get('dataset')), _text(row.get('field')), _text(row.get('issue')))
        if last.get(key) != squash(_text(row.get('after'))):
            fresh.append(row)
            last[key] = squash(_text(row.get('after')))
    return fresh
LOG_SHEET = '_log'
LOG_COLUMNS = ('when', 'dataset', 'field', 'issue', 'before', 'after', 'by')
INPUT_COLUMNS = ('proposed_fix', 'choices', 'your_fix', 'how_to_fix', 'current_value', 'keys')


def _input_rows(project: str) -> pd.DataFrame:
    """The rows that carry answers: the Action_list, or -- a workbook written
    before the livestock layout -- Specific items."""
    frame = _read_sheet(project, ACTION_SHEET)
    if 'keys' not in frame.columns:
        frame = _read_sheet(project, SPECIFIC_SHEET)
    return frame if 'keys' in frame.columns else pd.DataFrame()


def _sheet(wb, title, header, rows, widths, input_columns=(), freeze='A2'):
    ws = wb.create_sheet(title)
    ws.append(list(header))
    for cell in ws[1]:
        cell.font = BOLD
        cell.alignment = WRAP
    for row in rows:
        ws.append([('' if v is None else v) for v in row])
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = width
    for column_name in input_columns:
        col = list(header).index(column_name) + 1
        ws.cell(1, col).fill = fill('FFEB9C')
        for r in range(2, ws.max_row + 1):
            ws.cell(r, col).fill = fill(INPUT_FILL)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = WRAP
    ws.freeze_panes = freeze
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions
    return ws


def _checklist_rows() -> list:
    """(sheet tag, row number) for every row of the checklist's two sheets."""
    if not os.path.isfile(CHECKLIST_FILE):
        return []
    rows = []
    wb = load_workbook(CHECKLIST_FILE, read_only=True)
    for sheet_name, tag in CHECKLIST_SHEETS:
        if sheet_name in wb.sheetnames:
            for number, values in enumerate(wb[sheet_name].iter_rows(min_row=2, values_only=True), 2):
                if any(values):
                    rows.append((tag, number))
    return rows


def _change_rows(committed, applied, answers) -> list:
    """Log rows for what one run wrote to the datasets."""
    model = _model()
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = [{'when': stamp, 'dataset': c.get('dataset'), 'field': c.get('column'),
             'issue': c.get('issues'), 'before': c.get('before'),
             'after': c.get('after'), 'by': 'model (correction)'}
            for c in committed if not c.get('minor')]
    for key, (description, dataset) in sorted((applied or {}).items()):
        _project, _, after = key.partition('||')
        _dataset, _, rest = after.partition('|')
        column, kind = model.split_key_rest(rest)
        rows.append({'when': stamp, 'dataset': dataset, 'field': column, 'issue': kind,
                     'before': '', 'after': description,
                     'by': 'your answer' if key in (answers or {}) else 'model (automatic)'})
    return rows


def _history(project: str) -> pd.DataFrame:
    """The change log so far: the hidden log (a workbook from before the
    skills' format kept it in Changes Applied)."""
    for sheet in (LOG_SHEET, CHANGES_SHEET):
        frame = _read_sheet(project, sheet)
        if 'when' in frame.columns:
            return frame.reindex(columns=list(LOG_COLUMNS))
    return pd.DataFrame(columns=list(LOG_COLUMNS))


def _skill_change(row, labels) -> list:
    """A log row as a Changes Applied row of the skills' format."""
    def text(v):
        return '' if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
    return [labels.get(text(row.get('dataset')), text(row.get('dataset'))),
            text(row.get('field')), text(row.get('before')) or '(empty / not present)',
            text(row.get('after')), '',
            f"{text(row.get('issue'))} -- {text(row.get('by'))}, {text(row.get('when'))}"]


def append_changes(project: str, committed: list = (), applied: dict | None = None,
                   answers: dict | None = None) -> None:
    """Add what was just saved to the log and to Changes Applied, leaving the
    rest of the workbook as it is (the review window commits without
    re-scanning, the pipeline's prepare pass before the final one)."""
    path = review_path(project)
    if not os.path.isfile(path):
        return
    rows = _only_new(_history(project), _change_rows(committed, applied, answers))
    # What you typed in the Corrections sheet first: redrawing it from the
    # register alone turned a fresh 'no' back into 'yes'.
    register = _update_register(_register(project), committed, applied, answers, (),
                                _typed_before(project)[1])
    wb = load_workbook(path)
    if LOG_SHEET in wb.sheetnames:
        for row in rows:
            wb[LOG_SHEET].append(['' if row.get(c) is None else str(row.get(c))
                                  for c in LOG_COLUMNS])
    if CHANGES_SHEET in wb.sheetnames:
        labels = {}
        if 'Activities overview' in wb.sheetnames:
            for label, _name, source, *_ in wb['Activities overview'].iter_rows(
                    min_row=2, values_only=True):
                if source:
                    labels[str(source)] = str(label)
        for row in rows:
            wb[CHANGES_SHEET].append(_skill_change(row, labels))
        _write_register_sheet(wb, register)
        if CORRECTIONS_SHEET in wb.sheetnames:
            _write_corrections_sheet(wb, register, labels)
    wb.save(path)


def _item_name(column: str) -> str:
    """How an item reads in the Action_list: the flow, not the model's key."""
    column = str(column)
    return column[len('exchange:'):] if column.startswith('exchange:') else column


# ── The livestock-ecoinvent-check layout ────────────────────────────────────
# The skill's report: an Action_list (#, Severity, Area, Issue, Detail) in
# severity order, and a structure map of each activity -- Structure,
# Naming_check, Emissions, Properties_check -- with, for a multi-dataset
# submission, the Activities overview and a Review_vs_decisions checklist.
# Kept from the model's own review: one Action_list row per distinct error,
# answered once for every dataset on it, and the Corrections and history.

REVIEW_SHEET = 'Review_vs_decisions'
# The internal checklist writes Master Data as "MD".
_MD_RE = re.compile(r'\bMD\b')
MD_SPELLED_OUT = 'Master Data (MD)'
OLD_SHEETS = ('Specific items', 'Flow issues')        # merged into the Action_list
DELETED_SHEET = 'Deleted'
FACTS_SHEET = 'Facts'
SHEET_ORDER = ('Verdict', 'Activities overview', ACTION_SHEET, 'Structure', 'Naming_check',
               'Emissions', 'Properties_check', FACTS_SHEET, REVIEW_SHEET, 'Checklist status',
               CHANGES_SHEET, DELETED_SHEET, CORRECTIONS_SHEET, 'All findings')
HEADER_FONT = Font(bold=True, color='FFFFFF')
HEADER_FILL = '305496'
CORRECTED_FILL = 'CFE2F3'
OK_FILL = 'D9EAD3'

PRODUCT_GROUPS = {'ReferenceProduct': 'REF', 'ByProduct': 'BYPROD'}
INPUT_GROUPS = {'FromTechnosphere': 'INPUT', 'ToTechnosphere': 'OUTPUT'}
ELEMENTARY_GROUPS = {'FromEnvironment': 'RESOURCE', 'ToEnvironment': 'EMISSION'}
PRICE = 'price'


def area_of(finding) -> str:
    """The skill's Area of a finding: Naming, Emissions, Properties,
    Allocation, Structure, Exchanges or Metadata."""
    column, kind = str(finding.column), str(finding.kind).lower()
    if column.startswith('exchange placement:') or any(
            word in kind for word in ('compartment', 'emission', 'elementary')):
        return 'Emissions'
    if 'propert' in kind or 'propert' in column.lower() or 'price' in kind:
        return 'Properties'
    if 'allocation' in kind or 'allocation' in column.lower():
        return 'Allocation'
    if any(word in kind for word in ('bull', 'herd', 'heifer', 'culled', 'manure', 'dairy')):
        return 'Structure'
    if column == 'activityName':
        return 'Naming'
    if column.startswith('exchange'):
        # A flow's name or unit not as in the master data is a naming slip;
        # the rest (a duplicate, a zero amount, uncertainty) is the exchange's.
        return ('Naming' if any(word in kind for word in ('name', 'unit', 'master', 'declared'))
                else 'Exchanges')
    return 'Metadata'


def flow_of(column) -> str | None:
    """The flow a finding is about, from its column ('exchange:Ammonia',
    'exchange placement:Ammonia :: air / ...'), or None."""
    prefix, colon, rest = str(column).partition(':')
    if not colon or not prefix.startswith('exchange') or prefix == 'exchanges':
        return None
    return rest.split(' :: ')[0].strip()


def _style_header(ws) -> None:
    for cell in ws[1]:
        if cell.value not in (None, ''):
            cell.font = HEADER_FONT
            cell.fill = fill(HEADER_FILL)
            cell.alignment = WRAP


def _status_text(open_, fixed) -> tuple:
    """(text, fill) of an item from its open and corrected findings."""
    if open_:
        worst = min((s for s, _ in open_), key=lambda s: SEV_ORDER[s])
        return ('; '.join(dict.fromkeys(f'{s}: {k}' for s, k in open_)), SEV_FILL[worst])
    if fixed:
        return ('corrected: ' + '; '.join(dict.fromkeys(fixed)), CORRECTED_FILL)
    return 'OK', OK_FILL


def _read_dataset(project: str, dataset: str) -> dict:
    path = os.path.join(_model().meta_output_dir(project, create=False), dataset)
    try:
        return pd.read_excel(path, sheet_name=['exchanges', 'properties'], dtype=object)
    except Exception:
        return {}


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def _write_structure_sheets(wb, project: str, datasets: list, labels: dict,
                            findings: list) -> None:
    """Structure, Naming_check, Emissions and Properties_check: what each
    activity is made of, with every item's status from the findings."""
    mandatory = tuple(MANDATORY_PROPERTIES)
    status = collections.defaultdict(lambda: ([], []))      # (dataset, item) -> open, fixed
    for f in findings:
        flow = flow_of(f.column)
        item = flow.lower() if flow else ('activityName' if f.column == 'activityName' else None)
        if item is None:
            continue
        open_, fixed = status[(f.dataset, item, area_of(f))]
        if severity_of(f):
            open_.append((severity_of(f), f.kind))
        elif f.fixed:
            fixed.append(f.kind)

    def state(dataset, item, areas):
        # Each sheet shows its own area: a missing property is not a naming
        # problem, and is in Properties_check.
        open_, fixed = [], []
        for area in areas:
            more_open, more_fixed = status.get((dataset, item, area), ([], []))
            open_ += more_open
            fixed += more_fixed
        return _status_text(open_, fixed)

    structure, naming, emissions, props = [], [], [], []
    for dataset in datasets:
        sheets = _read_dataset(project, dataset)
        exchanges = sheets.get('exchanges', pd.DataFrame())
        properties = sheets.get('properties', pd.DataFrame())
        if exchanges.empty or 'group' not in exchanges.columns:
            continue
        label = labels.get(dataset, dataset)
        exchanges = exchanges[exchanges['name'].notna()]
        valued = collections.defaultdict(dict)                # product -> {property: amount}
        if not properties.empty and 'propertyName' in properties.columns:
            for _, p in properties.iterrows():
                if not any(_blank_cell(p.get(c)) for c in ('name', 'propertyName', 'amount')):
                    valued[(str(p.get('activityName')), str(p['name']).strip())][
                        str(p['propertyName']).strip()] = p.get('amount')
        activities = list(dict.fromkeys(exchanges['activityName'].dropna().astype(str))) or [label]
        for activity in activities:
            rows = exchanges[exchanges['activityName'].astype(str) == activity] \
                if 'activityName' in exchanges.columns else exchanges
            geography = next(iter(rows['geography'].dropna().astype(str)), '') \
                if 'geography' in rows.columns else ''
            text, colour = state(dataset, 'activityName', ('Naming', 'Metadata'))
            naming.append(([label, activity, 'ACTIVITY', activity, '', text], colour))
            for _, e in rows.iterrows():
                group, name = str(e.get('group', '')).strip(), str(e['name']).strip()
                unit, amount = e.get('unitName', ''), e.get('amount')
                text, colour = state(dataset, name.lower(), ('Naming',))
                if group in PRODUCT_GROUPS or group in INPUT_GROUPS:
                    role = PRODUCT_GROUPS.get(group) or INPUT_GROUPS[group]
                    have = valued.get((activity, name), {})
                    price = mand = ''
                    if role in ('REF', 'BYPROD'):
                        price = 'YES' if PRICE in have else 'NO'
                        mand = 'all 6' if all(m in have for m in mandatory) else 'MISSING'
                    structure.append([label, activity, geography, role, name, amount, unit,
                                      price, mand])
                    naming.append(([label, activity, role, name, unit, text], colour))
                    if role in ('REF', 'BYPROD'):
                        missing = [m for m in mandatory + (PRICE,) if m not in have]
                        wet, dry, water = (_number(have.get(k)) for k in
                                           ('wet mass', 'dry mass', 'water in wet mass'))
                        content = _number(have.get('water content'))
                        checks = []
                        if None not in (wet, dry, water) and abs(wet - dry - water) > 0.01 * max(abs(wet), 1e-12):
                            checks.append('wet mass ≠ dry mass + water in wet mass')
                        if None not in (dry, water, content) and dry and \
                                abs(content - water / dry) > 0.01 * max(abs(content), 1e-12):
                            checks.append('water content ≠ water in wet mass / dry mass')
                        verdict = ('; '.join((['MISSING: ' + ', '.join(missing)] if missing else [])
                                             + checks) or 'all 7, equations hold')
                        props.append(([label, activity, role, name]
                                      + [have.get(m, '') for m in mandatory + (PRICE,)] + [verdict],
                                      OK_FILL if verdict.startswith('all 7') else SEV_FILL['Critical']))
                elif group in ELEMENTARY_GROUPS:
                    text, colour = state(dataset, name.lower(),
                                         ('Emissions', 'Naming', 'Exchanges', 'Metadata'))
                    emissions.append(([label, activity, ELEMENTARY_GROUPS[group], name, amount, unit,
                                       e.get('compartment', ''), e.get('subcompartment', ''), text],
                                      colour))

    def write(title, header, rows, widths, status_col=None):
        ws = _sheet(wb, title, header, [r for r, _ in rows] if status_col else rows, widths)
        if status_col:
            for r, (_, colour) in enumerate(rows, 2):
                ws.cell(r, status_col).fill = fill(colour)
        return ws

    ws = write('Structure', ['Dataset', 'Activity', 'Geography', 'Role', 'Name', 'Amount', 'Unit',
                             'Price', '6 mandatory'],
               structure, [30, 40, 10, 9, 45, 14, 9, 7, 11])
    for r in range(2, ws.max_row + 1):
        for col, bad in ((8, 'NO'), (9, 'MISSING')):
            value = ws.cell(r, col).value
            if value:
                ws.cell(r, col).fill = fill(SEV_FILL['Critical'] if value == bad else OK_FILL)
    write('Naming_check', ['Dataset', 'Activity', 'Role', 'Name', 'Unit', 'Status'],
          naming, [30, 40, 10, 45, 9, 60], status_col=6)
    write('Emissions', ['Dataset', 'Activity', 'Role', 'Flow', 'Amount', 'Unit', 'Compartment',
                        'Subcompartment', 'Status'],
          emissions, [30, 40, 10, 36, 14, 9, 16, 30, 60], status_col=9)
    write('Properties_check', ['Dataset', 'Activity', 'Role', 'Product', *mandatory, PRICE, 'Status'],
          props, [30, 40, 9, 40] + [11] * (len(mandatory) + 1) + [50],
          status_col=len(mandatory) + 6)


def _write_deleted_sheet(wb, project: str, labels: dict) -> int:
    """Deleted: every row, cell, column or sheet any step removed from the
    project's datasets -- the meta model and the three calculators -- in
    full, from the project's deletion log.  Returns how many."""
    from config_new_datasets import read_deletions
    entries = read_deletions(_model().meta_output_dir(project, create=False))
    rows = [[labels.get(e.get('dataset'), os.path.splitext(str(e.get('dataset', '')))[0]),
             e.get('step', ''), e.get('sheet', ''), e.get('deleted', ''), e.get('reason', ''),
             e.get('first_deleted', ''), e.get('last_deleted', '')]
            for e in entries]
    _sheet(wb, DELETED_SHEET, ['Dataset', 'Step', 'Sheet', 'What was deleted', 'Why',
                               'First deleted', 'Last deleted'],
           rows, [34, 16, 12, 90, 44, 19, 19])
    return len(rows)


def _blank_cell(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or not str(value).strip()


def write_review(project: str, findings: list, corrections: list = (),
                 committed: list = (), applied: dict | None = None,
                 answers: dict | None = None) -> str:
    """
    Write the project's one review workbook, in the skills' format.  Returns
    its path.  The written verdict is its first sheet; no separate file.

    findings     every finding of the project's datasets (this scan)
    corrections  the cell changes the model makes (proposed)
    committed    the corrections saved this run      -> Changes Applied
    applied      {key: (description, dataset)} written this run (answers,
                 automatic exchange fixes)            -> Changes Applied
    answers      the typed answers this run applied (to credit them as yours)
    """
    import consolidate_reviews as skill

    model = _model()
    findings = [f for f in findings if f.severity != model.MINOR]
    typed, applies = _typed_before(project)
    history = _history(project)
    new_changes = _only_new(history, _change_rows(committed, applied, answers))
    register = _update_register(_register(project), committed, applied, answers,
                                corrections, applies)
    if new_changes:
        history = pd.concat([history, pd.DataFrame(new_changes)], ignore_index=True)

    datasets = sorted({f.dataset for f in findings if f.dataset}
                      | {c['dataset'] for c in corrections if c.get('dataset')})
    labels = dataset_labels(datasets, findings)
    activity = {}
    for f in findings:
        if f.dataset and f.activity:
            activity.setdefault(f.dataset, f.activity)

    # ── One Specific items row per distinct error ───────────────────────────
    # The skill groups hotspots by (item, issue).  The model's groups are
    # finer -- the same issue with a different current value is a different
    # error, and a dataset's own figure (a yield) is answered per dataset --
    # so the item is named apart where a group would otherwise merge.
    groups = group_findings(findings)
    by_name = collections.Counter((_item_name(g['column']), g['kind']) for g in groups)
    actions = []                           # one Action_list row per distinct error
    hotspots = collections.defaultdict(list)
    flow_checks = collections.defaultdict(list)
    for number, group in enumerate(groups, 1):
        lines = ([group['findings']] if group['shared'] else
                 [[f] for f in group['findings']])
        for line, members in enumerate(lines, 1):
            item = _item_name(group['column'])
            own = sorted({labels[f.dataset] for f in members if f.dataset in labels})
            if not group['shared'] or by_name[(item, group['kind'])] > 1:
                item = f'{item} ({"; ".join(own)})'
            sample = members[0]
            keys = [model.finding_key(f) for f in members]
            detail = [str(sample.note).strip()] if str(sample.note or '').strip() else []
            detail.append(f'Now: {str(group["found"])[:400]}')
            if str(sample.expected or '').strip() and sample.expected != sample.proposal:
                detail.append(f'Expected: {str(sample.expected)[:400]}')
            actions.append({
                '#': str(number) if len(lines) == 1 else f'{number}-{line}',
                'severity': group['severity'], 'area': area_of(sample),
                'issue': group['kind'], 'item': item, 'detail': '\n'.join(detail),
                'datasets': _compact(own), 'n': len(own),
                'rows': list(dict.fromkeys(cell for f in members for cell in finding_rows(f))),
                'inputs': [sample.proposal, model.render_choices(sample),
                           next((typed[k] for k in keys if k in typed), ''),
                           model.how_to_fix(sample), str(group['found'])[:500], '\n'.join(keys)]})
            for f in members:
                hotspots[f.dataset].append({
                    'exchange_name': item, 'issue': group['kind'],
                    'checklist_row': f.checklist_row, 'expected': str(f.expected)[:500],
                    'actual': str(f.found)[:500], 'note': str(f.note)[:1500],
                    'severity': severity_of(f) or group['severity']})
                if str(f.column).startswith(('exchange:', 'exchange placement:')):
                    flow_checks[f.dataset].append({
                        'flow_name': _item_name(f.column), 'check': group['kind'],
                        'result': 'FAIL', 'fixable': bool(f.proposal and not f.options),
                        'comment': f'{str(f.found)[:300]} -- {str(f.note)[:600]}'})

    # ── Every checklist row, per dataset (coverage, as the skills require) ─
    covered = {cell for text in MODEL_CHECKED_ROWS for cell in parse_checklist_rows(text)}
    per_row = collections.defaultdict(list)          # (dataset, cell) -> findings
    for f in findings:
        for cell in finding_rows(f):
            per_row[(f.dataset, cell)].append(f)
    decision_of = {'Critical': 'BLOCKING', 'Medium': 'ACCEPTABLE_FOR_NOW', 'Decision': 'DECISION'}

    def entry(dataset, cell):
        hits = per_row.get((dataset, cell), [])
        open_ = [f for f in hits if severity_of(f) and severity_of(f) != 'Verify']
        if open_:
            severity = min((severity_of(f) for f in open_), key=lambda s: SEV_ORDER[s])
            text = '; '.join(dict.fromkeys(f'{f.kind} ({_item_name(f.column)})' for f in open_))
            return {'status': 'FAIL', 'comment': f'FAIL: {text}'[:1500],
                    'decision': decision_of.get(severity), 'severity': severity}
        verify = [f for f in hits if severity_of(f) == 'Verify']
        if verify:
            return {'status': 'INFO', 'comment': 'Verify: ' + '; '.join(
                dict.fromkeys(f.kind for f in verify))[:1500]}
        fixed = [f for f in hits if f.fixed]
        if fixed:
            return {'status': 'CORRECTED', 'comment': 'CORRECTED: ' + '; '.join(
                dict.fromkeys(f.kind for f in fixed))[:1500]}
        if cell in covered or any(f.severity == 'PASS' for f in hits):
            return {'status': 'OK', 'comment': 'OK -- checked by the model'}
        return {'status': 'INFO', 'comment': 'not checked by the model -- needs a reviewer'}

    checklist = _checklist_rows()
    results = []
    for dataset in datasets:
        log = history[history['dataset'].astype(str) == dataset] if not history.empty else history
        results.append({
            # The verdict reads "<label> = <dataset_name>": the dataset's name,
            # then the file it is in.
            'dataset_name': dataset, 'dataset_label': labels[dataset],
            'Checks': {str(n): entry(dataset, ('Checks', n)) for t, n in checklist if t == 'Checks'},
            'agri related checks': {str(n): entry(dataset, ('agri', n))
                                    for t, n in checklist if t == 'agri'},
            'hotspots': hotspots.get(dataset, []),
            'flow_checks': flow_checks.get(dataset, []),
            'changes': [dict(zip(('field', 'old_value', 'new_value', 'checklist_row', 'reason'),
                                 _skill_change(row, labels)[1:]))
                        for _, row in log.iterrows()],
        })

    # 'full': the datasets by name (dataset_label), not D1..Dn.
    data = skill.build(results, CHECKLIST_FILE, 'full')
    path = review_path(project)
    skill.write_workbook(data, path)

    # ── The additions: answer columns, Corrections, All findings, the log ──
    wb = load_workbook(path)
    # The overview's "Source file" is the workbook itself (the skill puts the
    # label there, which is now the dataset's name).
    ws = wb['Activities overview']
    header = [c.value for c in ws[1]]
    by_name = {name: dataset for dataset, name in labels.items()}
    for r in range(2, ws.max_row + 1):
        name = ws.cell(r, header.index('Label') + 1).value
        dataset = by_name.get(name, name)
        ws.cell(r, header.index('Activity name') + 1, activity.get(dataset, name))
        ws.cell(r, header.index('Source file') + 1, dataset)
    ws.column_dimensions[get_column_letter(header.index('Label') + 1)].width = 50

    # The checks themselves, not row numbers: "Checks row 76" or
    # "76-78 (Checks) / 17 (agri)" means nothing without the checklist open.
    texts = skill.load_checklist_text(CHECKLIST_FILE)     # {(sheet, row): (area, check)}
    sheet_of = {'Checks': 'Checks', 'agri': 'agri related checks'}

    def check_text(tag, number):
        area, check = texts.get((sheet_of[tag], number), ('', ''))
        topic = area.split(' / ')[-1].strip() if area else ''
        return f'{topic}: {check}' if topic and check else check or f'{tag} row {number}'

    for name in ('Action_list', 'Checklist status'):
        ws = wb[name]
        header = [c.value for c in ws[1]]
        col = header.index('Checklist ref') + 1
        ws.cell(1, col, 'Checklist')
        for r in range(2, ws.max_row + 1):
            ref = str(ws.cell(r, col).value or '')
            if ref:
                ws.cell(r, col, 'agri related checks' if ref.startswith('agri') else 'Checks')
    # The skill's per-checklist-item list is the Review_vs_decisions sheet;
    # the Action_list is the livestock skill's, one row per distinct error.
    wb[ACTION_SHEET].title = REVIEW_SHEET
    for name in OLD_SHEETS:
        if name in wb.sheetnames:
            del wb[name]
    ws = wb['Verdict']
    for r in range(1, ws.max_row + 1):
        line = ws.cell(r, 1).value
        if isinstance(line, str):
            line = re.sub(r'\b(?:Checks|agri) row \d+(?::| —) ', '', line)
            line = line.replace(ACTION_SHEET, REVIEW_SHEET).replace('Specific items', ACTION_SHEET)
            ws.cell(r, 1, line)

    header = ['#', 'Severity', 'Area', 'Issue', 'Item', 'Detail', 'Datasets', 'n',
              'Checklist check', *INPUT_COLUMNS]
    ws = _sheet(wb, ACTION_SHEET, header,
                [[a['#'], a['severity'], a['area'], a['issue'], a['item'], a['detail'],
                  a['datasets'], a['n'],
                  '\n'.join(dict.fromkeys(f'• {check_text(t, n)}' for t, n in a['rows'])),
                  *[('' if v is None else v) for v in a['inputs']]] for a in actions],
                [6, 10, 11, 34, 30, 60, 34, 5, 50, 30, 40, 30, 40, 26, 26],
                input_columns=('your_fix',), freeze='E2')
    for r, a in enumerate(actions, 2):
        ws.cell(r, 2).fill = fill(SEV_FILL.get(a['severity'], 'FFFFFF'))
    for name in ('current_value', 'keys'):
        ws.column_dimensions[get_column_letter(header.index(name) + 1)].hidden = True

    _write_structure_sheets(wb, project, datasets, labels, findings)
    deleted_count = _write_deleted_sheet(wb, project, labels)
    # The facts the comments were built from, and where each came from.
    import meta_facts
    _sheet(wb, FACTS_SHEET, ['Dataset', 'Activity', 'Fact', 'Value', 'Where it comes from'],
           [r for r in meta_facts.facts_rows(labels) if r[0] in set(labels.values())],
           [30, 45, 24, 60, 40])

    _write_corrections_sheet(wb, register, labels)
    _write_register_sheet(wb, register)

    _sheet(wb, 'All findings',
           ['label', 'dataset', 'activityName', 'field', 'checklist_row', 'issue',
            'severity', 'model_severity', 'fixed', 'found', 'expected', 'note'],
           [[labels.get(f.dataset, ''), f.dataset, f.activity, f.column, f.checklist_row,
             f.kind, severity_of(f) or ('Corrected' if f.fixed else f.severity.title()),
             f.severity, 'yes' if f.fixed else '', str(f.found)[:500],
             str(f.expected)[:500], str(f.note)[:1000]]
            for f in sorted(findings, key=lambda f: (labels.get(f.dataset, ''), f.column))],
           [7, 45, 40, 28, 16, 34, 10, 14, 6, 40, 40, 60])

    ws = _sheet(wb, LOG_SHEET, list(LOG_COLUMNS),
                history.reindex(columns=list(LOG_COLUMNS)).fillna('').astype(str).values.tolist(),
                [19, 50, 30, 34, 50, 50, 18])
    ws.sheet_state = 'hidden'

    # How to answer, at the end of the verdict.
    ws = wb['Verdict']
    ws.append([''])
    ws.append(['How to answer: type in the yellow `your_fix` column of the Action_list (one '
               'answer covers every dataset on the row; a number picks from `choices`; "yes" '
               'takes the proposed fix), or set `apply` to no in Corrections. Then run the '
               'pipeline -- or the meta model -- again and the answers are applied. Corrections '
               'lists every correction ever made to these datasets, however many runs; a `no` '
               'there holds on every later run, until you set it back to yes. Structure, '
               'Naming_check, Emissions and Properties_check map each activity, with the '
               'status of every item; Review_vs_decisions is the internal checklist.'])
    ws.cell(ws.max_row, 1).font = BOLD
    ws.cell(ws.max_row, 1).alignment = WRAP
    ws.append([f'Deleted: {deleted_count} item(s) were removed from these datasets by the model '
               f'or the calculators -- every one is listed, with its content, in the Deleted '
               f'sheet.' if deleted_count else 'Deleted: nothing was removed from these datasets.'])
    ws.cell(ws.max_row, 1).font = BOLD
    ws.cell(ws.max_row, 1).alignment = WRAP

    # The skill's look: its sheet order, white-on-blue headers.
    for index, name in enumerate(n for n in SHEET_ORDER if n in wb.sheetnames):
        wb.move_sheet(name, offset=index - wb.sheetnames.index(name))
    for ws in wb.worksheets:
        if ws.sheet_state == 'visible' and ws.title != 'Verdict':
            _style_header(ws)
    # The checklist's abbreviation, spelled out wherever its text is shown.
    for name in (ACTION_SHEET, REVIEW_SHEET, 'Checklist status'):
        if name in wb.sheetnames:
            for row in wb[name].iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and _MD_RE.search(cell.value):
                        cell.value = _MD_RE.sub(MD_SPELLED_OUT, cell.value)
    wb.active = 0
    wb.save(path)
    return path
