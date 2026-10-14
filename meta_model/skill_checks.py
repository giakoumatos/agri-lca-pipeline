"""
The review checklist's per-exchange checks, from the team's two review skills.

Ported from the checkdataset skill (run_all_checks.py, check_md_conformance.py,
check_pedigree_uncertainty.py, parse_xlsx_dataset.py) and the
livestock-ecoinvent-check skill (check_dataset.py, references/methodology.md),
so every meta model run -- and so every pipeline run -- answers these rows of
the internal review checklist for every dataset, instead of a reviewer
running the skills by hand afterwards:

  Uncertainty + pedigree present, scores 1-5, variance > 0   76-78 (Checks) / 17 (agri)
  Own products carry the 6 mandatory properties              103-109 (Checks) / 24 (agri)
  ... and a price                                             26 (agri)
  wet mass = dry mass + water in wet mass, water content
    = water in wet mass / dry mass                            25 (agri)
  Unit is the one Master Data defines for the flow            96 (Checks) / 20 (agri)
  Property values within 1 % of the Master Data default       96 (Checks) / 24 (agri)
  The same flow is not entered twice                          69-71 (Checks)
  No template instruction text left in a data cell            69-71 (Checks)
  Every variable a formula uses is defined                    73-74 (Checks)
  Property rows join an exchange                              127 (Checks) / 24 (agri)
  Agricultural air emissions in non-urban air (a decision,
    answered once for every dataset)                          18 (agri)
  Livestock: nitrate to ground water, herd structure,
    product names the ecoinvent markets link to               livestock methodology
  The amount agrees with the figure its comment states
    (passes, trees, litres per hectare, ...)                  69 (Checks) / 8 (agri)

What the pipeline makes certain is not re-checked: the DQG pedigree formula
(Checks 76-78) is applied by spold_converter_new itself, from the scores
checked here, with the guideline's Table 10.5; flow names and compartments
against Master Data are check_exchange_names'; zero amounts are
drop_zero_exchanges'.

Every finding carries its checklist row, so the review workbook's
Checklist status shows these rows per dataset.
"""

import json
import re

import collections
import pandas as pd

ROW_UNCERTAINTY = '76-78 (Checks) / 17 (agri)'
ROW_MANDATORY = '103-109 (Checks) / 24 (agri)'
ROW_PRICE = '26 (agri)'
ROW_EQUATIONS = '25 (agri)'
ROW_UNIT = '96 (Checks) / 20 (agri)'
ROW_PROPERTY_VALUES = '96 (Checks) / 24 (agri)'
ROW_EXCHANGES = '69-71 (Checks)'
ROW_FORMULA = '73-74 (Checks)'
ROW_AIR = '18 (agri)'
ROW_ORPHAN = '127 (Checks) / 24 (agri)'
ROW_LIVESTOCK = ''          # the livestock methodology has no checklist row
# Not in CHECKED_ROWS: agri 8 also asks that the passes be a round number that
# makes sense agronomically, which a matching comment does not answer, and 69
# is already covered by ROW_EXCHANGES.
ROW_COMMENT_AMOUNTS = '69 (Checks) / 8 (agri)'

# The rows above, for the review workbook's Checklist status.
CHECKED_ROWS = (ROW_UNCERTAINTY, ROW_MANDATORY, ROW_PRICE, ROW_EQUATIONS, ROW_UNIT,
                ROW_PROPERTY_VALUES, ROW_EXCHANGES, ROW_FORMULA, ROW_AIR, ROW_ORPHAN)

MANDATORY_PROPERTIES = ('wet mass', 'dry mass', 'water content', 'water in wet mass',
                        'carbon content, fossil', 'carbon content, non-fossil')
PEDIGREE = ('reliability', 'completeness', 'temporalCorrelation',
            'geographicalCorrelation', 'furtherTechnologyCorrelation')
OWN_PRODUCTS = ('ReferenceProduct', 'ByProduct', 'Products')
ELEMENTARY = ('ToEnvironment', 'FromEnvironment')

# ── Basic uncertainty: Data Quality Guideline, Table 10.3 ───────────────────
# The variance (sigma^2 of the underlying normal) of a lognormal exchange
# before the pedigree adds to it -- the converter adds the pedigree's share.
# Where the table has an agricultural column it is used (these are farm
# datasets); otherwise its process column.  Every value below is also what
# the existing crop datasets carry for the same flows.
BASIC_UNCERTAINTY_TYPE = 'lognormal'
HEAVY_METALS = ('arsenic', 'antimony', 'cadmium', 'chromium', 'cobalt', 'copper', 'lead',
                'mercury', 'molybdenum', 'nickel', 'selenium', 'thallium', 'tin', 'vanadium',
                'zinc', 'barium', 'beryllium', 'silver')
PAH_WORDS = ('pah', 'polycyclic aromatic', 'benzo(a)pyrene', 'hydrocarbons, aromatic')
TRANSPORT_UNITS = ('tkm', 'metric ton*km', 'ton kilometer', 'person*km', 'personkm',
                   'vehicle-km', 'km')


def dqg_basic_variance(row, pesticides=frozenset()) -> tuple:
    """(variance, what it is per DQG Table 10.3) of a non-product exchange."""
    group = _text(row.get('group'))
    name = _text(row.get('name'))
    low = name.lower()
    compartment = _text(row.get('compartment')).lower()
    unit = _text(row.get('unitName')).lower()
    metal = any(low == m or low.startswith(m + ' ') or low.startswith(m + ',')
                or low.startswith(m + ' ion') for m in HEAVY_METALS)
    pesticide = name in pesticides or low in pesticides
    if group in ('FromTechnosphere', 'ToTechnosphere'):
        if unit in TRANSPORT_UNITS or low.startswith(('transport', 'market for transport')):
            return 0.12, 'transport services'
        return 0.0006, 'demand of energy, materials, services or waste treatment'
    if group == 'FromEnvironment':
        if low.startswith('occupation'):
            return 0.002, 'land use, occupation (agricultural)'
        if low.startswith('transformation'):
            return 0.008, 'land use, transformation (agricultural)'
        return 0.0006, 'resource (water, carbon dioxide, energy carrier, metal, salt)'
    if compartment == 'air':
        if low.startswith('carbon dioxide') or low.startswith('sulfur dioxide'):
            return 0.0006, 'emission to air: CO2 / SO2'
        if low.startswith(('ammonia', 'methane')):
            return 0.008, 'agricultural emission to air: NH3, CH4'
        if low.startswith(('dinitrogen monoxide', 'nitrogen oxides')):
            return 0.03, 'agricultural emission to air: N2O, NOx'
        if low.startswith('nmvoc'):
            return 0.2, 'emission to air: NMVOC'
        if low.startswith('particulate matter, > 10'):
            return 0.04, 'emission to air: PM > 10'
        if low.startswith('particulate matter, > 2.5'):
            return 0.08, 'emission to air: PM 2.5-10'
        if low.startswith('particulate matter'):
            return 0.12, 'emission to air: PM < 2.5'
        if low.startswith('carbon monoxide') or metal:
            return 0.65, 'emission to air: CO, heavy metals'
        if any(w in low for w in PAH_WORDS):
            return 0.3, 'emission to air: PAH'
        return 0.04, 'emission to air: other (pesticides, inorganic)'
    if compartment == 'water':
        if metal:
            return 0.09, 'agricultural emission to water: heavy metals'
        if any(w in low for w in PAH_WORDS) or 'hydrocarbons' in low:
            return 0.3, 'emission to water: hydrocarbons, PAH'
        return 0.04, 'emission to water: NO3, PO4, pesticides, inorganic, BOD/COD'
    if compartment == 'soil':
        if pesticide:
            return 0.033, 'agricultural emission to soil: pesticides'
        return 0.04, 'emission to soil: heavy metals, oil, other'
    return 0.04, 'emission: other'


def _pesticide_set() -> frozenset:
    try:
        import sys
        from config_new_datasets import PIPELINE_DIR
        if PIPELINE_DIR not in sys.path:
            sys.path.insert(0, PIPELINE_DIR)
        import verify_output
        return frozenset(verify_output.pesticide_names())
    except Exception:
        return frozenset()


def fill_dqg_uncertainty(exchanges, dataset: str, project: str, applied: dict,
                         rejected, report) -> None:
    """
    Every exchange but the reference product and by-products gets an
    uncertainty type and a basic variance (DQG Table 10.3) where it has
    none, and -- where the dataset makes it possible -- the pedigree scores
    the activity's other exchanges carry.  A value already there is never
    changed; what is filled is listed, row by row, in the review.
    """
    from config_new_datasets import describe_row
    for column in ('uncertainty type', 'variance') + PEDIGREE:
        if column not in exchanges.columns:
            exchanges[column] = None
        if exchanges[column].dtype != object:
            exchanges[column] = exchanges[column].astype(object)
    named = exchanges['name'].map(lambda v: bool(_text(v)))
    groups = exchanges['group'].map(_text) if 'group' in exchanges.columns else ''
    rows = exchanges[named & ~groups.isin(OWN_PRODUCTS)]
    pesticides = _pesticide_set()

    # ── uncertainty type and basic variance
    key = f'{project}||{dataset}|exchanges.variance|basic uncertainty added (DQG Table 10.3)'
    filled = []
    if not rejected(key):
        for idx, row in rows.iterrows():
            kind = _text(row.get('uncertainty type')).lower()
            if kind and kind != BASIC_UNCERTAINTY_TYPE:
                continue                      # another distribution: its own parameters
            if kind and _number(row.get('variance')) is not None:
                continue
            variance, what = dqg_basic_variance(row, pesticides)
            if not kind:
                exchanges.at[idx, 'uncertainty type'] = BASIC_UNCERTAINTY_TYPE
            if _number(row.get('variance')) is None:
                exchanges.at[idx, 'variance'] = variance
            filled.append(f'{describe_row(row)}: {BASIC_UNCERTAINTY_TYPE}, variance {variance} ({what})')
    if filled:
        applied[key] = (f'{len(filled)} exchange(s): ' + '; '.join(filled), dataset)
        report('exchanges.variance', f'{len(filled)} exchange(s) without uncertainty',
               'lognormal with the DQG Table 10.3 basic variance',
               'basic uncertainty added (DQG Table 10.3)', 'WARNING',
               '; '.join(filled), ROW_UNCERTAINTY, fixed=True)

    # ── pedigree: from the activity's other exchanges, where they agree on one
    key = f'{project}||{dataset}|exchanges.pedigree|pedigree taken from the dataset'
    if rejected(key):
        return
    complete = rows[rows[list(PEDIGREE)].apply(
        lambda r: all(_number(r[p]) in (1, 2, 3, 4, 5) for p in PEDIGREE), axis=1)]
    taken = []
    for idx, row in rows.iterrows():
        if all(_text(row.get(p)) for p in PEDIGREE):
            continue
        same = complete
        if 'activityName' in exchanges.columns:
            same = complete[complete['activityName'].map(_text) == _text(row.get('activityName'))]
        # The same kind of exchange first -- a technosphere input is scored
        # like the other inputs, an emission like the other emissions.
        elementary = _text(row.get('group')) in ELEMENTARY
        alike = same[same['group'].map(_text).isin(ELEMENTARY) == elementary] \
            if 'group' in same.columns else same
        source = alike if len(alike) else same
        if source.empty:
            continue
        sets = source[list(PEDIGREE)].apply(
            lambda r: tuple(int(_number(r[p])) for p in PEDIGREE), axis=1)
        scores, count = collections.Counter(sets).most_common(1)[0]
        for p, value in zip(PEDIGREE, scores):
            if not _text(row.get(p)):
                exchanges.at[idx, p] = value
        taken.append(f'{describe_row(row)}: pedigree {scores}, as on {count} other '
                     f'{"emission/resource" if elementary and len(alike) else "exchange"}(s) '
                     f'of the activity')
    if taken:
        applied[key] = (f'{len(taken)} exchange(s): ' + '; '.join(taken), dataset)
        report('exchanges.pedigree', f'{len(taken)} exchange(s) without pedigree',
               'the pedigree the activity\'s other exchanges carry',
               'pedigree taken from the dataset', 'WARNING',
               '; '.join(taken), ROW_UNCERTAINTY, fixed=True)


# Agricultural air emissions: ecoinvent's own crop and cattle datasets put
# them in non-urban air (checklist agri 18; livestock methodology §6).
AIR_AGRI = ('Ammonia', 'Dinitrogen monoxide', 'Nitrogen oxides', 'Methane, non-fossil',
            'NMVOC, non-methane volatile organic compounds, unspecified origin')
AIR_AGRI_SUB = 'non-urban air or from high stacks'

TEMPLATE_RESIDUE = ('leave empty',)

# Names in a mathematicalRelation that are functions or constants, not variables.
FORMULA_WORDS = frozenset({
    'abs', 'min', 'max', 'if', 'sqrt', 'ln', 'log', 'log10', 'exp', 'power', 'pow',
    'round', 'and', 'or', 'not', 'true', 'false', 'pi', 'e', 'sin', 'cos', 'tan',
    'sum', 'avg', 'mod', 'int', 'floor', 'ceil', 'ceiling', 'ref', 'yield'})

LIVESTOCK_WORDS = ('cattle', 'cow', 'dairy', 'calf', 'calves', 'heifer', 'bull', 'suckler',
                   'goat', 'sheep', 'lamb', 'pig', 'swine', 'sow', 'piglet', 'poultry',
                   'chicken', 'broiler', 'hen', 'laying', 'turkey', 'duck', 'herd',
                   'cashmere', 'livestock', 'buffalo', 'horse')

_INDEX = None


def master_data() -> dict:
    """The Master Data flow index (build_master_data_index.py), or {}."""
    global _INDEX
    if _INDEX is None:
        from config_new_datasets import MASTER_DATA_FLOW_INDEX_FILE
        try:
            with open(MASTER_DATA_FLOW_INDEX_FILE, encoding='utf-8') as fh:
                _INDEX = json.load(fh)
        except Exception:
            _INDEX = {}
    return _INDEX


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    text = str(value).strip()
    return '' if text.lower() == 'nan' else text


def _number(value):
    try:
        number = float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def is_livestock(sheets: dict) -> bool:
    """A livestock dataset: ISIC division 014x, or an animal in the name."""
    meta = sheets.get('meta')
    if meta is None or meta.empty:
        return False
    isic = ' '.join(_text(v) for v in meta.get('ISIC', pd.Series(dtype=object)))
    if re.search(r'\b014\d', isic):
        return True
    names = ' '.join(_text(v).lower() for v in meta.get('activityName', pd.Series(dtype=object)))
    return any(re.search(rf'\b{w}\b', names) for w in LIVESTOCK_WORDS)


def run(sheets: dict, dataset: str, activity: str, project: str, applied: dict,
        Finding, exchange_prefix: str = 'exchange:', placement_column=None,
        remembered=None, rejected=None, yield_of=None) -> list:
    """
    Run every check on one dataset's sheets.  Returns Findings.

    Corrects in place only what has exactly one right answer (template
    residue blanked), recording it in `applied`; everything else is
    reported.  `placement_column(name, compartment, subcompartment)` keys a
    placement decision the way the model's apply_fix moves rows;
    `remembered(column, kind, found)` returns an answer already given for
    the same question, so it is not asked again.  `yield_of(activityName)`
    is the activity's yield in kg/ha, or None -- what turns a comment's
    per-hectare figure into the per-kg amount it should match.
    """
    remembered = remembered or (lambda column, kind, found: None)
    rejected = rejected or (lambda key: False)    # apply = no in the Corrections sheet
    exchanges = sheets.get('exchanges')
    if exchanges is None or exchanges.empty or 'name' not in exchanges.columns:
        return []
    properties = sheets.get('properties')
    if properties is None:
        properties = pd.DataFrame()
    findings: list = []

    def report(column, found, expected, kind, severity, note, row=ROW_EXCHANGES,
               fixed=False, proposal='', options=()):
        findings.append(Finding(dataset, activity, column, found, expected, kind, fixed,
                                severity, note, row, project, proposal, options))

    def group_of(row):
        return _text(row.get('group'))

    # ── Uncertainty filled where the Data Quality Guideline decides it: the
    # type and basic variance always, the pedigree where the dataset has it.
    fill_dqg_uncertainty(exchanges, dataset, project, applied, rejected, report)

    rows = [(idx, row) for idx, row in exchanges.iterrows() if _text(row.get('name'))]

    # ── Uncertainty: present and valid on every exchange but the reference
    # product and by-products (checklist 76-78 / agri 17).  The DQG formula itself is applied
    # by the converter from these scores, so presence and validity are what
    # decide whether the .spold carries the right uncertainty.
    missing_unc, bad_scores, bad_variance = {}, {}, {}
    for idx, row in rows:
        if group_of(row) in OWN_PRODUCTS:
            continue
        name = _text(row['name'])
        kind = _text(row.get('uncertainty type')).lower()
        variance = _number(row.get('variance'))
        scores = {p: _text(row.get(p)) for p in PEDIGREE}
        absent = [p for p, v in scores.items() if not v]
        if not kind or (kind == 'lognormal' and variance is None) or absent:
            what = []
            if not kind:
                what.append('uncertainty type')
            elif kind == 'lognormal' and variance is None:
                what.append('variance')
            if absent:
                what.append('pedigree scores' if len(absent) == len(PEDIGREE)
                            else 'pedigree ' + ', '.join(absent))
            missing_unc.setdefault(name, set()).update(what)
            continue
        wrong = [f'{p}={v}' for p, v in scores.items()
                 if _number(v) is None or _number(v) not in (1, 2, 3, 4, 5)]
        if wrong:
            bad_scores.setdefault(name, set()).update(wrong)
        if kind == 'lognormal' and variance is not None and variance <= 0:
            bad_variance.setdefault(name, set()).add(str(row.get('variance')))
    if missing_unc:
        # One error, however many flows: the answer is the same -- score the
        # uncertainty -- so it is one row, grouped across datasets, with the
        # flows listed in the note rather than one row per flow.
        listed = '; '.join(f'{name} ({" and ".join(sorted(what))})'
                           for name, what in sorted(missing_unc.items()))
        report('exchanges (uncertainty)', 'uncertainty or pedigree scores missing',
               'uncertainty type, variance and the 5 pedigree scores on every exchange '
               'but the reference product and by-products', 'uncertainty or pedigree missing',
               'ACCEPTABLE_FOR_NOW',
               f'{len(missing_unc)} exchange(s) without them: {listed}. The type and basic '
               f'variance are filled from the Data Quality Guideline wherever the exchange '
               f'is lognormal; the pedigree only where the activity\'s other exchanges '
               f'carry one to take -- score the rest (DQG pedigree matrix).', ROW_UNCERTAINTY)
    for name, wrong in bad_scores.items():
        report(exchange_prefix + name, ', '.join(sorted(wrong)), 'scores 1 to 5',
               'pedigree score outside 1-5', 'BLOCKING',
               'A pedigree score must be a whole number from 1 to 5; anything else adds '
               'nothing to the variance in the .spold, so its uncertainty is understated.',
               ROW_UNCERTAINTY)
    for name, values in bad_variance.items():
        report(exchange_prefix + name, ', '.join(sorted(values)), 'a variance above 0',
               'lognormal variance not positive', 'BLOCKING',
               'A lognormal basic variance must be greater than 0.', ROW_UNCERTAINTY)

    # ── Own products: the 6 mandatory properties and a price (103-109 / agri
    # 24 / agri 26), and their equations (agri 25).  Purchased inputs get
    # theirs from ecoinvent (fill_properties_from_ecoinvent); a dataset's own
    # products are new, so only the data provider can give them.
    if not properties.empty and {'name', 'propertyName'} <= set(properties.columns):
        props = {}
        for _, p in properties.iterrows():
            key = (_text(p.get('name')), _text(p.get('group')))
            props.setdefault(key, {})[_text(p.get('propertyName')).lower()] = p
    else:
        props = {}
    seen_products = set()
    for idx, row in rows:
        group = group_of(row)
        name = _text(row['name'])
        if group not in OWN_PRODUCTS or (name, group) in seen_products:
            continue
        seen_products.add((name, group))
        mine = props.get((name, group), {})
        valued = {k for k, p in mine.items()
                  if _number(p.get('amount')) is not None or _text(p.get('mathematicalRelation'))}
        missing = [m for m in MANDATORY_PROPERTIES if m not in valued]
        if missing:
            report(exchange_prefix + name, 'missing: ' + ', '.join(missing),
                   'all 6 mandatory properties with a value', 'mandatory properties missing',
                   'BLOCKING',
                   f'{group} "{name}" needs wet mass, dry mass, water content, water in wet '
                   f'mass and both carbon contents; without them the mass and carbon '
                   f'balances of every dataset using it break.', ROW_MANDATORY)
        if 'price' not in valued:
            report(exchange_prefix + name, 'no price', 'a price in EUR2005',
                   'price missing', 'BLOCKING',
                   f'{group} "{name}" has no price: economic allocation (cut-off, APOS) '
                   f'cannot run. Give the price, currency and year; the model converts '
                   f'it to EUR2005.', ROW_PRICE)

    # Property rows that join no exchange (127 / agri 24): the product is
    # usually named differently on the two sheets, and it would import with
    # no properties at all.
    exchange_keys = {(_text(r['name']), group_of(r)) for _, r in rows}
    for (name, group), mine in props.items():
        if name and (name, group) not in exchange_keys:
            near = sorted(n for n, g in exchange_keys if g == group)
            report(exchange_prefix + name, f'{len(mine)} property row(s) for "{name}" ({group})',
                   'a property name + group identical to an exchange row',
                   'properties match no exchange', 'BLOCKING',
                   f'The properties sheet lists "{name}" under {group}, but no exchange has that '
                   f'name and group, so the product imports with none of its properties. '
                   + (f'The {group} exchange is "{near[0]}" -- rename one to match the other.'
                      if len(near) == 1 else 'Rename one sheet to match the other.'),
                   ROW_ORPHAN, proposal=near[0] if len(near) == 1 else '')

    # Property equations, on every exchange that states all four.
    for (name, group), mine in props.items():
        values = {k: _number(mine[k].get('amount')) for k in
                  ('wet mass', 'dry mass', 'water in wet mass', 'water content') if k in mine}
        if len(values) < 4 or None in values.values():
            continue
        wet, dry, water, content = (values['wet mass'], values['dry mass'],
                                    values['water in wet mass'], values['water content'])
        problems = []
        if abs(wet - (dry + water)) > 0.01 * max(abs(wet), 1e-9):
            problems.append(f'wet mass {wet:g} != dry mass {dry:g} + water in wet mass {water:g}')
        if dry and abs(content - water / dry) > 0.01 * max(abs(content), 1e-9):
            problems.append(f'water content {content:g} != water in wet mass / dry mass '
                            f'= {water / dry:g}')
        if problems:
            report(exchange_prefix + name, '; '.join(problems), 'consistent properties',
                   'property equations do not hold', 'BLOCKING',
                   'wet mass = dry mass + water in wet mass, and water content = water in '
                   'wet mass / dry mass. Which value is wrong is not derivable, so none is '
                   'changed.', ROW_EQUATIONS)

    # ── Against Master Data: unit, and property values (96 / agri 20, 24).
    index = master_data()
    md_inter = index.get('intermediate', {})
    md_elem = index.get('elementary', {})
    md_props = index.get('properties', {})
    # A blank unit, where the flow settles it: Master Data defines the unit of
    # every flow, so it is filled from there.  Left blank, the converter made a
    # unit id up and the database import rejected the dataset.
    for idx, row in rows:
        name, group = _text(row['name']), group_of(row)
        if _text(row.get('unitName')) or 'unitName' not in exchanges.columns:
            continue
        if group in ELEMENTARY:
            unit = md_elem.get(f"{name}|{_text(row.get('compartment'))}|{_text(row.get('subcompartment'))}")
            if not unit:                    # one unit for the flow wherever it sits
                units = {u for k, u in md_elem.items() if k.split('|', 1)[0] == name}
                unit = next(iter(units)) if len(units) == 1 else None
        else:
            unit = (md_inter.get(name) or {}).get('unitName')
        key = f'{project}||{dataset}|{exchange_prefix}{name}|unit missing'
        if unit and not rejected(key):
            if exchanges['unitName'].dtype != object:
                exchanges['unitName'] = exchanges['unitName'].astype(object)
            exchanges.at[idx, 'unitName'] = unit
            row['unitName'] = unit
            applied[key] = (f'{name!r}: no unit -> {unit!r}, as Master Data defines it', dataset)
            report(exchange_prefix + name, '(no unit)', unit, 'unit missing', 'WARNING',
                   f'The unit was blank; Master Data defines {name!r} in {unit}, so it is '
                   f'filled in.', ROW_UNIT, fixed=True, proposal=unit)
        else:
            report(exchange_prefix + name, '(no unit)', 'a unit', 'unit missing', 'BLOCKING',
                   'The unit is blank and Master Data does not settle it; without one the '
                   '.spold carries a made-up unit id that the database import rejects.',
                   ROW_UNIT)

    unit_seen = set()
    for idx, row in rows:
        name, group, unit = _text(row['name']), group_of(row), _text(row.get('unitName'))
        if not unit or (name, group) in unit_seen:
            continue
        unit_seen.add((name, group))
        if group in ELEMENTARY:
            key = f"{name}|{_text(row.get('compartment'))}|{_text(row.get('subcompartment'))}"
            expected = md_elem.get(key)
        elif group not in OWN_PRODUCTS:
            expected = (md_inter.get(name) or {}).get('unitName')
        else:
            expected = None             # the dataset's own product: new, its own unit
        if expected and expected != unit:
            report(exchange_prefix + name, unit, expected, 'unit differs from Master Data',
                   'BLOCKING',
                   f'Master Data defines "{name}" in {expected}, the dataset gives {unit}. '
                   f'Either the wrong flow was chosen or the amount needs converting '
                   f'(e.g. MJ to kWh, m2a to m2*year) -- not changed automatically, since '
                   f'the amount would change with it.', ROW_UNIT, proposal=expected)

    if props and md_props:
        for (name, group), mine in props.items():
            if group in OWN_PRODUCTS or group in ELEMENTARY or name not in md_props:
                continue
            deviations = []
            for prop, p in mine.items():
                if prop == 'price' or prop not in md_props[name]:
                    continue
                actual, default = _number(p.get('amount')), md_props[name][prop]['amount']
                if actual is None:
                    continue
                off = (abs(actual) > 1e-9) if default == 0 else \
                    abs(actual - default) / abs(default) > 0.01
                if off and not _text(p.get('comment')):
                    deviations.append(f'{prop}: {actual:g} (Master Data {default:g})')
            if deviations:
                report(exchange_prefix + name, '; '.join(deviations),
                       'the Master Data default, or a comment saying why not',
                       'property deviates from Master Data', 'ACCEPTABLE_FOR_NOW',
                       'A property more than 1 % off its Master Data default needs a comment '
                       'explaining the deviation (a measured value is legitimate), or the '
                       'default. Not overwritten: it may be real data.', ROW_PROPERTY_VALUES)

    # ── The same flow entered twice (69-71).
    keys = {}
    for idx, row in rows:
        key = tuple(_text(row.get(c)) for c in (
            'activityName', 'group', 'name', 'compartment', 'subcompartment',
            'activityLink_activityName', 'activityLink_geography'))
        keys.setdefault(key, []).append(row)
    for key, items in keys.items():
        if len(items) > 1:
            where = '/'.join(k for k in key[3:5] if k) or key[1]
            amounts = ', '.join(_text(r.get('amount')) for r in items)
            report(exchange_prefix + key[2], f'{len(items)} rows in {where} (amounts {amounts})',
                   'one row per flow', 'flow entered more than once', 'ACCEPTABLE_FOR_NOW',
                   'The same flow in the same place more than once -- usually an '
                   'undocumented split (enteric and manure methane, say). Document the split '
                   'in the comment, then merge; not merged automatically, since merging '
                   'loses the split.', ROW_EXCHANGES)

    # ── Template instruction text left in a data cell: blanked (one answer).
    for sheet_name in ('exchanges', 'properties', 'parameters', 'meta'):
        frame = sheets.get(sheet_name)
        if frame is None or frame.empty:
            continue
        for column in frame.columns:
            # Text comes back as object or, in newer pandas, as a str dtype.
            if not (pd.api.types.is_object_dtype(frame[column])
                    or pd.api.types.is_string_dtype(frame[column])):
                continue
            mask = frame[column].map(lambda v: isinstance(v, str)
                                     and v.strip().lower() in TEMPLATE_RESIDUE)
            key = f'{project}||{dataset}|{sheet_name}.{column}|template text removed'
            if mask.any() and not rejected(key):
                from config_new_datasets import describe_cell, deleted_note
                cells = [describe_cell(r, column) for _, r in frame.loc[mask].iterrows()]
                frame.loc[mask, column] = None
                applied[key] = (
                    f'{int(mask.sum())} cell(s) held "leave empty" -- emptied. '
                    + deleted_note(cells), dataset)
                report(f'{sheet_name}.{column}', 'leave empty', '(empty)',
                       'template instruction text left in a data cell', 'WARNING',
                       f'{int(mask.sum())} cell(s) of {sheet_name}.{column} still said '
                       f'"leave empty" -- emptied.', ROW_EXCHANGES, fixed=True)

    # ── Every variable a formula uses is defined (73-74).
    defined = set()
    for sheet_name in ('exchanges', 'properties', 'parameters'):
        frame = sheets.get(sheet_name)
        if frame is not None and not frame.empty:
            for column in ('variableName', 'PV_variableName'):
                if column in frame.columns:
                    defined |= {_text(v).lower() for v in frame[column] if _text(v)}
    undefined = {}
    for sheet_name in ('exchanges', 'properties', 'parameters'):
        frame = sheets.get(sheet_name)
        if frame is None or frame.empty or 'mathematicalRelation' not in frame.columns:
            continue
        for _, row in frame.iterrows():
            formula = _text(row.get('mathematicalRelation'))
            if not formula:
                continue
            stripped = re.sub(r'"[^"]*"|\'[^\']*\'', ' ', formula)
            for token in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', stripped):
                if token.lower() not in defined and token.lower() not in FORMULA_WORDS:
                    undefined.setdefault(token, set()).add(_text(row.get('name')) or sheet_name)
    for token, where in undefined.items():
        report(f'formula variable:{token}', token, 'a defined variableName',
               'formula uses an undefined variable', 'BLOCKING',
               f'"{token}" is used in the mathematicalRelation of {", ".join(sorted(where)[:5])} '
               f'but is no variableName or parameter of this dataset, so the formula cannot '
               f'be evaluated (a shifted cell often lands text here).', ROW_FORMULA)

    # ── Agricultural air emissions in non-urban air (agri 18), always: as in
    # ecoinvent's own crop and livestock datasets.  Moved like a placement
    # answer (apply_fix), so a flow already in non-urban air takes the amount
    # into its row, and the move is flagged in the comment; apply = no in
    # Corrections keeps a dataset's placement.
    if placement_column and {'compartment', 'subcompartment', 'group'} <= set(exchanges.columns):
        import meta_model_new as model
        target = f'air / {AIR_AGRI_SUB}'
        kind = 'agricultural air emission not in non-urban air'
        pairs = {(_text(r['name']), _text(r.get('subcompartment'))) for _, r in rows
                 if _text(r['name']) in AIR_AGRI and _text(r.get('compartment')) == 'air'}
        for name, sub in sorted(pairs):
            if sub == AIR_AGRI_SUB or f'{name}|air|{AIR_AGRI_SUB}' not in md_elem:
                continue
            column = placement_column(name, 'air', sub)
            found = f'{name} in air / {sub}'
            key = f'{project}||{dataset}|{column}|{kind}'
            if rejected(key):
                continue                    # apply = no: this dataset keeps it
            ok, description = model.apply_fix(sheets, column, kind, target)
            if ok:
                applied[key] = (description, dataset)
                report(column, found, target, kind, 'WARNING',
                       f'{description}. Agricultural air emissions (NH3, N2O, NOx, CH4, NMVOC) '
                       f'go to non-urban air, as in ecoinvent\'s own crop and livestock datasets '
                       f'(checklist agri 18; livestock methodology section 6).',
                       ROW_AIR, fixed=True, proposal=target)
            else:
                report(column, found, target, kind, 'BLOCKING',
                       f'Could not be moved to {target}: {description}.', ROW_AIR,
                       proposal=target)
        exchanges = sheets['exchanges']     # a merge replaces the sheet

    # ── The amount agrees with the figure its comment states (69 / agri 8).
    _comment_amounts(exchanges, yield_of, exchange_prefix, report)

    if is_livestock(sheets):
        findings.extend(_livestock(sheets, rows, dataset, activity, project, Finding,
                                   exchange_prefix, md_inter))
    return findings


# ── Comment figures against amounts ─────────────────────────────────────────
# Each family is a way the data providers' comments state the figure an
# amount was computed from.  Only these are read: a number in a comment is
# as often a source year, a distance or a reference value as the amount, so a
# number is compared only where the wording says what it is.
_NUM = r'(\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)'
_PASSES_RE = re.compile(r'corresponds to\s+' + _NUM + r'\s+applications?\b', re.I)
_TREES_RE = re.compile(_NUM + r'\s+of trees per ha divided by the lifetime years of the '
                       r'orchard\s*\(\s*' + _NUM, re.I)
_PLANTING_RE = re.compile(r'planting (?:of )?' + _NUM + r'\s+trees per hectare.*?lifespan of\s+'
                          + _NUM + r'\s+years(?:,? with the first (\w+) years? unproductive)?',
                          re.I | re.S)
_DIESEL_PASS_RE = re.compile(r'diesel consumption of this field operation is\s+' + _NUM
                             + r'\s*kg per hectare', re.I)
_DIESEL_MJ_RE = re.compile(_NUM + r'\s*kg of diesel per hectare', re.I)
_LCV_RE = re.compile(_NUM + r'\s*MJ/kg', re.I)
_WATER_RE = re.compile(_NUM + r'\s*m(?:3|³)\s+of water', re.I)
_KWH_RE = re.compile(_NUM + r'\s*kWh\b', re.I)
# 'Deltamethrin (6,64E-07 kg); Mineral Oil (3,93E-04)': the parts of the amount.
_PART_RE = re.compile(r'[A-Za-z][\w\- ]*?\(\s*' + _NUM + r'\s*(?:kg)?\s*\)')
_SPLIT_RE = re.compile(r'air \((\d+(?:\.\d+)?)%\), water \((\d+(?:\.\d+)?)%\) and soil '
                       r'\((\d+(?:\.\d+)?)%\)', re.I)
_WORD_NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
                 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
_COMMENT_TOLERANCE = 0.02           # beyond the rounding of the stated figure
_APPROXIMATE_TOLERANCE = 0.10       # 'approximately 30 m3'


def _figure(text: str) -> float:
    return float(text.replace(',', '.'))


def _tolerance(text: str, comment: str) -> float:
    """The relative difference a figure written as `text` allows: its own
    rounding (half its last decimal), at least _COMMENT_TOLERANCE.  A whole
    number -- 2 applications, 400 trees -- is a count, not a rounding."""
    mantissa, _, exponent = text.replace(',', '.').lower().partition('e')
    rounding = 0.0
    if '.' in mantissa or exponent:
        decimals = len(mantissa.partition('.')[2])
        half = 0.5 * 10 ** (-decimals) * 10 ** int(exponent or 0)
        value = abs(_figure(text))
        rounding = half / value if value else 0.0
    floor = _APPROXIMATE_TOLERANCE if re.search(r'approx|around|about|~', comment, re.I) \
        else _COMMENT_TOLERANCE
    return max(floor, rounding)


def _is_year(text: str) -> bool:
    return re.fullmatch(r'(?:19|20)\d\d', text) is not None


def _explain_ratio(ratio: float, comment: str) -> str:
    """When the amount is the stated figure times another number the comment
    names (55 interventions, 2 passes), say so: it is then usually the
    comment's 'total' that is per intervention."""
    for text in re.findall(_NUM, comment):
        number = _figure(text)
        if number > 1 and not _is_year(text) and abs(ratio / number - 1) < 0.02:
            return (f' The amount is {number:g} times the stated figure, and {number:g} is '
                    f'another number in the comment -- the figure may be per '
                    f'operation rather than the total.')
    return ''


def _comment_amounts(exchanges, yield_of, prefix, report) -> None:
    """Report every exchange whose amount contradicts a figure its comment states."""
    if 'comment' not in exchanges.columns or 'amount' not in exchanges.columns:
        return
    if 'activityName' in exchanges.columns:
        activities = exchanges.groupby(exchanges['activityName'].map(_text), sort=False)
    else:
        activities = [('', exchanges)]
    unchecked = set()
    for activity_name, rows in activities:
        yield_kg_ha = yield_of(activity_name) if yield_of else None
        reference = rows[rows['group'].map(_text) == 'ReferenceProduct'] \
            if 'group' in rows.columns else rows.iloc[0:0]
        product = next((a for a in map(_number, reference['amount']) if a), 1.0)
        # Per hectare = amount x yield / the reference amount it is given for.
        per_ha = (lambda amount: amount * yield_kg_ha / product) if yield_kg_ha else None
        operations = {_text(r['name']).lower(): _number(r['amount'])
                      for _, r in rows.iterrows() if _text(r.get('unitName')) == 'ha'}

        mismatches, per_ha_claims = [], 0
        for _, row in rows.iterrows():
            comment = _text(row.get('comment'))
            amount = _number(row.get('amount'))
            if not comment or amount is None or not re.search(r'\d', comment):
                continue
            name, unit = _text(row.get('name')), _text(row.get('unitName'))
            claims = _claims(name, unit, amount, comment, per_ha, product, operations)
            if claims is None:
                unchecked.add(name)
                continue
            per_ha_claims += sum(claim[4] == 'per ha' for claim in claims)
            mismatches += [(name, unit, amount, comment) + claim for claim in claims
                           if claim[0] and abs(claim[1] / claim[0] - 1) > claim[2]]

        # Every per-hectare figure off by one factor: the amounts were
        # computed with another yield than the land occupation gives.
        factors = [actual / stated for *_, stated, actual, _t, _w, basis in mismatches
                   if basis == 'per ha']
        common = ''
        if 1 < len(factors) == per_ha_claims and max(factors) / min(factors) < 1.01:
            factor = sum(factors) / len(factors)
            every = 'Both' if len(factors) == 2 else f'All {len(factors)}'
            common = (f' {every} per-hectare figures of this activity are off by the '
                      f'same factor {factor:.3g}: the amounts look computed with a yield of '
                      f'{yield_kg_ha / factor:.6g} kg/ha, not the {yield_kg_ha:.6g} kg/ha the land '
                      f'occupation gives -- check the yield and the occupation first.')
        for name, unit, amount, comment, stated, actual, _t, what, basis in mismatches:
            ratio = actual / stated
            off = f'{ratio:.3g} times the stated figure' if ratio > 2 or ratio < 0.5 \
                else f'{abs(ratio - 1):.0%} off'
            report(prefix + name, f'{amount:g} {unit}: {basis} {actual:.4g}',
                   f'{stated:.4g}, as the comment states ({what})',
                   'amount contradicts its comment', 'BLOCKING',
                   f'The comment says {what}, but the amount gives {actual:.4g} '
                   f'({basis}{f" at a yield of {yield_kg_ha:.6g} kg/ha" if basis == "per ha" else ""}), '
                   f'{off}.'
                   + (common if common and basis == 'per ha' else _explain_ratio(ratio, comment))
                   + ' Which of the two is wrong is not derivable, so neither is changed: '
                     'correct the amount or the comment.', ROW_COMMENT_AMOUNTS)
        _check_splits(rows, prefix, report)
    if unchecked:
        report('exchanges (comment figures)', ', '.join(sorted(unchecked)),
               'a yield, to compare per-hectare figures with', 'comment figures not checked',
               'INFO',
               f'The comments of {len(unchecked)} exchange(s) state a per-hectare figure, but '
               f'no plausible yield follows from the land occupation (there is none, or the '
               f'one it gives is implausible), so they were not compared with '
               f'the amounts: '
               f'{", ".join(sorted(unchecked))}.', ROW_COMMENT_AMOUNTS)


def _claims(name, unit, amount, comment, per_ha, product, operations):
    """[(stated, actual, tolerance, what the comment says, basis)] for one
    exchange; None when its comment states a per-hectare figure and there is
    no yield to compare it with."""
    claims = []

    def per_hectare(stated, text, what):
        if per_ha is None:
            raise LookupError
        claims.append((stated, per_ha(amount), _tolerance(text, comment), what, 'per ha'))

    try:
        match = _DIESEL_PASS_RE.search(comment)
        if match and unit == 'kg':
            # The diesel of one pass, times the passes of its field operation:
            # no yield needed, the operation's amount is per kg already.
            operation = re.sub(r'^diesel used in\s+', '', name, flags=re.I).lower()
            passes = operations.get(operation)
            if passes:
                stated = _figure(match.group(1)) * passes
                claims.append((stated, amount, _tolerance(match.group(1), comment),
                               f'{match.group(1)} kg of diesel per hectare and pass, '
                               f'times the {passes:g} ha/kg of "{operation}"', 'per kg'))
            return claims

        match = _PASSES_RE.search(comment)
        if match and unit == 'ha':
            per_hectare(_figure(match.group(1)), match.group(1),
                        f'{match.group(1)} application(s)')
            return claims

        match = _TREES_RE.search(comment)
        if match and unit == 'unit':
            trees, years = _figure(match.group(1)), _figure(match.group(2))
            per_hectare(trees / years, match.group(1),
                        f'{match.group(1)} trees per ha over {match.group(2)} years')
            return claims

        match = _PLANTING_RE.search(comment)
        if match and unit == 'unit':
            trees, years = _figure(match.group(1)), _figure(match.group(2))
            idle = match.group(3)
            idle = _WORD_NUMBERS.get(idle.lower()) if idle and not idle.isdigit() else \
                (int(idle) if idle else 0)
            if per_ha is None:
                raise LookupError
            actual = per_ha(amount)
            tolerance = _tolerance(match.group(1), comment)
            # The FO sheet divides by the lifespan; most comments mean the
            # productive years.  Either reading of the comment is consistent.
            readings = [trees / years] + ([trees / (years - idle)] if idle and years > idle else [])
            if all(abs(actual / r - 1) > tolerance for r in readings):
                claims.append((readings[-1], actual, tolerance,
                               f'{match.group(1)} trees per hectare over a lifespan of '
                               f'{match.group(2)} years' + (f', {idle} of them unproductive'
                                                            if idle else ''), 'per ha'))
            return claims

        match = _DIESEL_MJ_RE.search(comment)
        lcv = _LCV_RE.search(comment)
        if match and lcv and unit == 'MJ':
            per_hectare(_figure(match.group(1)) * _figure(lcv.group(1)), match.group(1),
                        f'{match.group(1)} kg of diesel per hectare at {lcv.group(1)} MJ/kg')
            return claims

        water, kwh = _WATER_RE.search(comment), _KWH_RE.search(comment)
        if unit == 'm3' and water:
            per_hectare(_figure(water.group(1)), water.group(1),
                        f'{water.group(1)} m3 of water per hectare')
            return claims
        if unit == 'kWh' and kwh and water:
            per_hectare(_figure(kwh.group(1)), kwh.group(1),
                        f'{kwh.group(1)} kWh per hectare')
            return claims
    except LookupError:
        return None

    # The amount listed by its parts -- 'Abamectine (1,5E-07); Mineral Oil
    # (8,9E-04)' -- in the exchange's own unit, so the parts sum to it.
    if unit == 'kg' and not re.search(r'%|per (?:ha|hectare)|/ha\b', comment, re.I):
        parts = [p for p in _PART_RE.findall(comment) if not _is_year(p)]
        if parts:
            stated = sum(_figure(p) for p in parts)
            tolerance = max(_tolerance(p, comment) for p in parts)
            claims.append((stated, amount, tolerance,
                           ('parts summing to ' if len(parts) > 1 else '') + f'{stated:.4g} kg',
                           'per kg'))
    return claims


def _check_splits(rows, prefix, report) -> None:
    """'emissions of a.i. in air (9%), water (1%) and soil (90%)': each
    compartment's row is that share of the substance's emissions."""
    if 'compartment' not in rows.columns:
        return
    groups = {}
    for _, row in rows.iterrows():
        comment = _text(row.get('comment'))
        match = _SPLIT_RE.search(comment)
        # A row the model moved between compartments says so ('Includes ...
        # moved from'), and its share no longer is the stated one.
        if not match or 'moved from' in comment:
            continue
        key = (_text(row.get('name')), match.groups())
        groups.setdefault(key, []).append(row)
    for (name, shares), members in groups.items():
        stated = dict(zip(('air', 'water', 'soil'), map(float, shares)))
        amounts = {}
        for row in members:
            compartment = _text(row.get('compartment')).lower()
            amounts[compartment] = amounts.get(compartment, 0.0) + (_number(row.get('amount')) or 0)
        expected = {c for c, s in stated.items() if s > 0}
        if set(amounts) != expected:
            continue                    # a compartment absent: the shares cannot be read
        total = sum(amounts.values())
        if not total:
            continue
        actual = {c: 100 * a / total for c, a in amounts.items()}
        off = [f'{c} {actual[c]:.1f}% (stated {stated[c]:g}%)' for c in sorted(actual)
               if abs(actual[c] - stated[c]) > 1.0]
        if off:
            report(prefix + name, '; '.join(off),
                   ', '.join(f'{c} {s:g}%' for c, s in stated.items()),
                   'amount contradicts its comment', 'BLOCKING',
                   f'The comment splits the emissions of {name} into air, water and soil '
                   f'shares that the amounts do not follow. Which is wrong is not derivable, '
                   f'so neither is changed: correct the amounts or the comment.',
                   ROW_COMMENT_AMOUNTS)


def _livestock(sheets, rows, dataset, activity, project, Finding, prefix, md_inter) -> list:
    """The livestock skill's structure and naming checks (methodology §1-§6)."""
    findings = []

    def report(column, found, expected, kind, severity, note, proposal='', options=()):
        findings.append(Finding(dataset, activity, column, found, expected, kind, False,
                                severity, note, ROW_LIVESTOCK, project, proposal, options))

    outputs = [(_text(r['name']), _text(r.get('group'))) for _, r in rows
               if _text(r.get('group')) in OWN_PRODUCTS]
    names = [_text(r['name']).lower() for _, r in rows]
    meta = sheets.get('meta')
    act = ' '.join(_text(v).lower() for v in meta.get('activityName', [])) if meta is not None else ''

    # Nitrate leaches to ground water (§6).
    for _, r in rows:
        if _text(r['name']) == 'Nitrate' and (
                _text(r.get('compartment')), _text(r.get('subcompartment'))) != ('water', 'ground-'):
            report(prefix + 'Nitrate', f"{_text(r.get('compartment'))}/{_text(r.get('subcompartment'))}",
                   'water/ground-', 'nitrate not to ground water', 'BLOCKING',
                   'In a livestock dataset nitrate leaching goes to water / ground- '
                   '(livestock methodology §6).', proposal='water/ground-')
            break

    # Herd structure (§1-§2) -- cattle.
    cattle = any(w in act for w in ('cattle', 'cow', 'suckler', 'calf', 'calves', 'heifer', 'bull'))
    if cattle:
        if 'bull' in act and 'production' in act and 'suckler' not in act:
            report('activityName', act, 'the bull embedded in the suckler herd',
                   'standalone bull activity', 'WARNING',
                   'ecoinvent has no bull product to link to; embed the bull in the herd '
                   '(livestock methodology §2).')
        if any('bull for breeding' in n for n in names):
            report(prefix + 'bull for breeding', 'standalone bull input',
                   'the bull embedded in the herd', 'standalone bull input retained', 'INFO',
                   'Check the mature bull\'s feed, methane and manure are in the herd once, '
                   'and the amount is per kg of calf, not per calf (off by ~180-220x) '
                   '(methodology §2).')
        if 'suckler' in act or any('suckler' in n for n in names):
            if not any('cull' in n and 'cow' in n for n in names):
                report('exchanges', 'no culled cow', 'a culled-cow by-product',
                       'culled cow missing from a suckler herd', 'WARNING',
                       'The breeding herd outputs a culled cow (methodology §1).')
            if not any('cull' in n and 'bull' in n for n in names) and \
                    not any('cattle for slaughtering' in n for n in names):
                report('exchanges', 'no culled bull', 'a culled bull, or "cattle for '
                       'slaughtering, live weight"', 'culled bull missing from a suckler herd',
                       'WARNING', 'Without a culled bull the sire is understated '
                       '(methodology §2).')

    # Manure as a by-product in a grazing system (§4).
    if any(w in act for w in ('grazing', 'grassland', 'pasture', 'extensive', 'nomadic')):
        for name, group in outputs:
            if 'manure' in name.lower():
                report(prefix + name, f'{name} ({group})', 'field emissions instead',
                       'manure as a by-product in a grazing system', 'ACCEPTABLE_FOR_NOW',
                       'In continuous grazing manure is deposited in situ: capture it as '
                       'field emissions, not as a by-product (methodology §4).')

    # Product names the ecoinvent markets link to (§5).
    exact = {k.lower(): k for k in md_inter}
    squash = {re.sub(r'[\s,]+', ' ', k.lower()).strip(): k for k in md_inter}
    for name, group in outputs:
        if name in md_inter:
            continue
        near = exact.get(name.lower()) or squash.get(re.sub(r'[\s,]+', ' ', name.lower()).strip())
        report(prefix + name, name, near or 'an ecoinvent product name',
               'product name not an ecoinvent product', 'ACCEPTABLE_FOR_NOW' if near else 'INFO',
               (f'Near-match to ecoinvent "{near}" -- likely a format slip; the exact string '
                f'lets the product link into the existing markets (methodology §5).' if near else
                'Not an ecoinvent product: a new product, or it should use an existing name '
                '(e.g. "cow milk", "cattle for slaughtering, live weight") so it links into '
                'the markets (methodology §5).'),
               proposal=near or '')
    return findings
