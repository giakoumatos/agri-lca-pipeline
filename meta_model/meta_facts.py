"""
The facts every metadata comment is built from, and the checks that set the
fields against each other.

A comment written by hand says the same thing as another field in its own
words -- the period, the source, the region -- and drifts from it: 41 of 129
datasets had dates and a timePeriodComment that disagree, 15 carried another
crop's name copied from a sibling dataset.  Here each fact is established
ONCE, and every comment is built from it:

  1. the provider's facts sheet ("facts" in dataset_input_template.xlsx),
     one row per activity, every column optional -- what they state wins;
  2. otherwise the fact is derived from the dataset itself: how the data were
     obtained from the technology / time-period comments, the sources from
     the citations in them, the production-volume years from PV_comment.

Every fact carries where it came from, and the review lists them (its Facts
sheet), so a derived fact that is wrong is seen and corrected at its source.

The cross-field checks report contradictions between two sources (the name
and the inventory, the PV and the period, the geography and the comments);
which side is wrong is for a human to say, so they never rewrite.
"""

import os
import re

import pandas as pd

import meta_spec as spec

FACTS_SHEET = 'facts'
PROVIDED = 'facts sheet'
_DATA_SOURCE_WORDS = {
    'field': 'field', 'survey': 'field', 'farm survey': 'field', 'measured': 'field',
    'statistical': 'statistical', 'statistics': 'statistical',
    'literature': 'literature',
}
# Where the dataset says how its data were obtained, most telling first.
_SOURCE_FIELDS = ('technologyComment', 'timePeriodComment',
                  'modellingAndValidation_samplingProcedure', 'generalComment')
_SOURCE_PATTERNS = (
    ('field', re.compile(r'collected (?:on|in the) field|field (?:survey|data|measurements)'
                         r'|farm(?:er)? (?:survey|interviews?)|questionnaire', re.I)),
    ('statistical', re.compile(r'\bstatistic', re.I)),
    ('literature', re.compile(r'\bliterature\b', re.I)),
)
_TEXT_FIELDS = ('generalComment', 'geographyComment', 'technologyComment', 'timePeriodComment',
                'modellingAndValidation_samplingProcedure', 'modellingAndValidation_extrapolations')
_FARMS_RE = re.compile(r'\b(\d{1,5})\s+(?:farms|holdings|farmers)\b', re.I)
_YEAR_RE = re.compile(r'\b(19[5-9]\d|20\d{2})\b')
# "Ribaudo, 2017)", "Ribaudo (2017)", "Zampori and Pant, 2019", "Nemecek et al. 2022"
_CITATION_RE = re.compile(r'\b([A-Z][A-Za-z\-]+(?: (?:and|&) [A-Z][A-Za-z\-]+| et al\.?)?)'
                          r',?\s*\(?((?:19|20)\d{2})\)?')
_NOT_AUTHORS = {'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                'September', 'October', 'November', 'December', 'Report', 'Table', 'Figure',
                'Annex', 'Data', 'Source', 'Sources', 'Year', 'Period', 'Yield', 'The', 'In',
                'From', 'Since', 'Until', 'Between', 'Average'}

_PROVIDED_FACTS: dict = {}       # the current dataset: {activity (lowercase) or '': {fact: value}}
USED: dict = {}                  # (dataset, activityName) -> {fact: (value, where)}


def _model():
    """The meta model as it is running (as __main__ from the command line)."""
    import sys
    main = sys.modules.get('__main__')
    if main is not None and hasattr(main, 'process_dataset') and hasattr(main, 'name_facts'):
        return main
    import meta_model_new
    return meta_model_new


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or not str(value).strip()


def _text(value) -> str:
    return '' if _blank(value) else ' '.join(str(value).split())


def whole(value):
    """A count as a whole number where it is one ('24.0' -> 24)."""
    number = pd.to_numeric(value, errors='coerce')
    return int(number) if pd.notna(number) and float(number).is_integer() else value


# ── The provider's facts ─────────────────────────────────────────────────────

def load(project: str, dataset: str, sheets: dict | None = None) -> dict:
    """
    Read the dataset's facts sheet -- its own, else the input dataset's of the
    same name (the calculators do not carry extra sheets through) -- and make
    it the current dataset's.  Returns {activity (lowercase) or '': {fact: value}}.
    """
    global _PROVIDED_FACTS
    frame = (sheets or {}).get(FACTS_SHEET)
    if frame is None and project:
        from config_new_datasets import BASE_DIR
        stem = os.path.splitext(dataset)[0]
        source = os.path.join(BASE_DIR, 'input datasets', project, stem + '.xlsx')
        if os.path.isfile(source):
            try:
                frame = pd.read_excel(source, sheet_name=FACTS_SHEET)
            except (ValueError, KeyError):
                frame = None             # no facts sheet
    facts = {}
    if frame is not None and not frame.empty:
        from config_new_datasets import drop_template_guidance
        frame = drop_template_guidance({FACTS_SHEET: frame})[FACTS_SHEET]
        for _, record in frame.iterrows():
            values = {str(k).strip(): (v.strip() if isinstance(v, str) else v)
                      for k, v in record.items() if not _blank(v)}
            activity = str(values.pop('activityName', '') or '').strip().lower()
            if values:
                facts.setdefault(activity, {}).update(values)
    _PROVIDED_FACTS = facts
    return facts


def provided(row) -> dict:
    """The provider's facts for this row's activity: the rows for every
    activity ('') overlaid with the activity's own."""
    activity = str(row.get('activityName', '') or '').strip().lower()
    return {**_PROVIDED_FACTS.get('', {}), **_PROVIDED_FACTS.get(activity, {})}


# ── Facts derived from the dataset ───────────────────────────────────────────

def citations(text) -> list:
    """The sources a text cites, each once, as 'Author (year)'."""
    found = []
    for author, year in _CITATION_RE.findall(str(text or '')):
        first = author.split()[0]
        if first in _NOT_AUTHORS:
            continue
        reference = f'{author.strip()} ({year})'
        if reference not in found:
            found.append(reference)
    return found


def _pv_years(row, exchanges) -> str:
    rows = _model()._activity_rows(row, exchanges)
    if rows is None or 'PV_comment' not in rows.columns or 'group' not in rows.columns:
        return ''
    own = rows[rows['group'].astype(str).str.strip() == 'ReferenceProduct']
    years = sorted({int(y) for c in own['PV_comment'] if not _blank(c)
                    for y in _YEAR_RE.findall(str(c))})
    if not years:
        return ''
    return str(years[0]) if years[0] == years[-1] else f'{years[0]}-{years[-1]}'


def derive(row, exchanges=None) -> dict:
    """{fact: (value, where)} that the dataset itself states."""
    found = {}
    for column in _SOURCE_FIELDS:
        text = _text(row.get(column))
        kind = next((k for k, pattern in _SOURCE_PATTERNS if pattern.search(text)), '')
        if kind:
            found['data_source'] = (kind, column)
            break
    # Only where the data's origin is described: the extrapolations and the
    # general comment cite the emission models (OLCA-Pest, heavy metals, PEF),
    # which are methods, not where the data come from.
    for column in ('technologyComment', 'timePeriodComment'):
        cited = citations(_text(row.get(column)).replace(spec.PESTICIDE_EMISSION_SENTENCE, ''))
        if cited:
            found['data_source_reference'] = ('; '.join(cited), column)
            break
    for column in _TEXT_FIELDS:
        match = _FARMS_RE.search(_text(row.get(column)))
        if match:
            found['farms_sampled'] = (int(match.group(1)), column)
            break
    years = _pv_years(row, exchanges)
    if years:
        found['pv_years'] = (years, 'PV_comment')
    return found


def facts_for(row, exchanges=None) -> dict:
    """{fact: (value, where)}: the provider's facts over the derived ones."""
    facts = derive(row, exchanges)
    for fact, value in provided(row).items():
        if fact == 'data_source':
            value = _DATA_SOURCE_WORDS.get(str(value).strip().lower(), str(value).strip().lower())
        facts[fact] = (value, PROVIDED)
    return facts


def values(row, exchanges=None) -> dict:
    """{fact: value} -- what the comment generators take."""
    return {fact: value for fact, (value, _where) in facts_for(row, exchanges).items()}


def record(dataset: str, row, exchanges=None) -> None:
    """Keep the facts used for one activity, for the review's Facts sheet."""
    USED[(dataset, _text(row.get('activityName')))] = facts_for(row, exchanges)


# ── Fields against each other ───────────────────────────────────────────────

_SYNTHETIC_N_RE = re.compile(r'^inorganic nitrogen fertiliser|\burea\b|ammonium nitrate', re.I)
PV_TOLERANCE_YEARS = 2          # a source this many years off the period still fits
# The checklist questions these answer.
ROW_REGIME = '9 (Checks) / 5 (agri)'     # exchanges vs the source; irrigation, organic stated
ROW_PV = '85 (Checks)'                   # PV consistent with the PV comment
ROW_GEO_TEXT = '48 (Checks)'             # comments consistent with the geography


def cross_field_checks(row, exchanges, families=None) -> list:
    """[(column, found, expected, kind, severity, note, checklist row)] for one meta row."""
    model = _model()
    out = []
    rows = model._activity_rows(row, exchanges)
    names = (rows['name'].map(lambda v: '' if pd.isna(v) else str(v).strip())
             if rows is not None and 'name' in rows.columns else pd.Series(dtype=object))
    groups = (rows['group'].map(lambda v: '' if pd.isna(v) else str(v).strip())
              if rows is not None and 'group' in rows.columns else pd.Series(dtype=object))
    named_facts = model.name_facts(row, families)

    # The water regime the name states, against the irrigation input and the
    # land-use flows (whose names carry irrigated / non-irrigated too).
    regime = str(named_facts.get('water_regime') or '').lower()
    if regime:
        named = 'irrigated' if regime == 'irrigated' else 'non-irrigated'
        irrigated = model.has_irrigation_exchange(row, exchanges)
        if named == 'irrigated' and not irrigated:
            out.append(('activityName', row.get('activityName'), 'an irrigation input',
                        'name says irrigated, the inventory has no irrigation', 'WARNING',
                        'The name says irrigated, but no exchange buys irrigation. Add the '
                        'irrigation input, or correct the name.', ROW_REGIME))
        if named == 'non-irrigated' and irrigated:
            out.append(('activityName', row.get('activityName'), 'no irrigation input',
                        'name says non irrigated, the inventory irrigates', 'WARNING',
                        'The name says non irrigated, but an exchange buys irrigation.', ROW_REGIME))
        land = {('non-irrigated' if 'non-irrigated' in n.lower() else 'irrigated')
                for n in names if re.match(r'(occupation|transformation)', n, re.I)
                and 'irrigated' in n.lower()}
        if land and land != {named}:
            out.append(('activityName', row.get('activityName'), f'{named} land use',
                        'land-use flows contradict the water regime', 'WARNING',
                        f'The name says {named}, the land-use flows are '
                        f'{" and ".join(sorted(land))}.', ROW_REGIME))

    # Organic production with synthetic nitrogen, which organic rules exclude.
    if str(named_facts.get('production_system') or '').lower() == 'organic':
        synthetic = sorted({n for n, g in zip(names, groups)
                            if g == 'FromTechnosphere' and _SYNTHETIC_N_RE.search(n)})
        if synthetic:
            out.append(('activityName', row.get('activityName'), 'no synthetic nitrogen',
                        'organic production with synthetic nitrogen fertiliser', 'WARNING',
                        'Organic farming excludes synthetic nitrogen, but the inventory has: '
                        + '; '.join(synthetic) + '.', ROW_REGIME))

    # The production volume's years against the dataset's period.
    start, end = model._year(row.get('startDate')), model._year(row.get('endDate'))
    stated = values(row, exchanges).get('pv_years')
    years = [int(y) for y in _YEAR_RE.findall(str(stated or ''))]
    if start and end and years and (max(years) < start - PV_TOLERANCE_YEARS
                                    or min(years) > end + PV_TOLERANCE_YEARS):
        out.append(('exchanges.PV_comment', f'{min(years)} - {max(years)}', f'{start} - {end}',
                    'production volume source outside the dataset period', 'INFO',
                    f'The production volume refers to {min(years)} - {max(years)}, the '
                    f'dataset to {start} - {end}. Use a production volume of the '
                    f"dataset's period, or say in PV_comment why this one stands.", ROW_PV))

    # Another country named in the comments than the dataset's geography --
    # often a comment copied from a sibling dataset; sometimes a deliberate
    # proxy, which is why it is only flagged for a look.
    own = spec.country_name(str(row.get('geography', '') or '').split('-')[0].strip().upper())
    if own:
        for column in _TEXT_FIELDS:
            text = _text(row.get(column))
            others = sorted({country for country in spec.COUNTRY_NAMES.values()
                             if country != own and re.search(rf'\b{re.escape(country)}\b', text)})
            if others:
                out.append((column, ', '.join(others), own,
                            "another country named than the dataset's", 'INFO',
                            f'{column} names {", ".join(others)}; the dataset is {own}. '
                            f'Fine if it is a stated proxy or source, otherwise a copied text.',
                            ROW_GEO_TEXT))
    return out


def facts_rows(labels: dict) -> list:
    """The review's Facts sheet: [dataset, activity, fact, value, where], one
    row per fact used, the provider's and the derived alike."""
    rows = []
    for (dataset, activity), facts in sorted(USED.items()):
        for fact, (value, where) in sorted(facts.items()):
            rows.append([labels.get(dataset, os.path.splitext(dataset)[0]), activity, fact,
                         value, 'provider (facts sheet)' if where == PROVIDED
                         else f'derived from {where}'])
    return rows
