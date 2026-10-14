"""
Tests for the review-checklist checks ported from the two review skills
(meta_model/skill_checks.py) and for the one-workbook-per-project
grouping (review_workbook.py).

Every check is tested both ways, as the checkdataset skill asks: it fires on a
dataset carrying the defect, and stays quiet on the clean control.

    python tests/test_skill_checks.py
"""

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'meta_model'))
sys.path.insert(0, os.path.join(HERE, '..', 'pipeline'))

import meta_model_new as M          # noqa: E402
import review_workbook as R         # noqa: E402
import skill_checks as K            # noqa: E402

ACT = 'olive production, integrated, irrigated'
PEDIGREE = dict(reliability=2, completeness=2, temporalCorrelation=2,
                geographicalCorrelation=1, furtherTechnologyCorrelation=1)


def exchange(group, name, unit, amount, compartment=None, subcompartment=None, **extra):
    row = {'activityName': ACT, 'geography': 'IT', 'group': group, 'name': name,
           'compartment': compartment, 'subcompartment': subcompartment,
           'unitName': unit, 'amount': amount}
    if group != 'ReferenceProduct':
        row.update({'uncertainty type': 'lognormal', 'variance': 0.0006, **PEDIGREE})
    row.update(extra)
    return row


def prop(name, group, prop_name, amount, unit):
    return {'activityName': ACT, 'geography': 'IT', 'group': group, 'name': name,
            'propertyName': prop_name, 'amount': amount, 'unitName': unit}


def clean():
    """A dataset every check passes."""
    olive = [prop('olive', 'ReferenceProduct', p, a, u) for p, a, u in (
        ('wet mass', 1.0, 'kg'), ('dry mass', 0.5, 'kg'), ('water in wet mass', 0.5, 'kg'),
        ('water content', 1.0, 'dimensionless'), ('carbon content, fossil', 0.0, 'dimensionless'),
        ('carbon content, non-fossil', 0.45, 'dimensionless'), ('price', 0.6, 'EUR2005'))]
    return {
        'meta': pd.DataFrame([{'activityName': ACT, 'geography': 'IT', 'ISIC': '0126:Growing of oleaginous fruits'}]),
        'exchanges': pd.DataFrame([
            exchange('ReferenceProduct', 'olive', 'kg', 1.0),
            exchange('FromTechnosphere', 'urea', 'kg', 0.01),
            exchange('ToEnvironment', 'Ammonia', 'kg', 0.001, 'air', K.AIR_AGRI_SUB),
        ]),
        'properties': pd.DataFrame(olive),
        'parameters': pd.DataFrame(columns=['activityName', 'name', 'variableName', 'mathematicalRelation']),
    }


def run(sheets, remembered=None):
    applied = {}
    findings = K.run(sheets, 'test.xlsx', ACT, 'tests', applied, M.Finding, M.EXCHANGE_PREFIX,
                     M.placement_column, remembered)
    return findings, applied


def kinds(findings):
    return {f.kind for f in findings if not (f.fixed and f.severity == 'PASS')}


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


@check
def clean_dataset_raises_nothing():
    findings, applied = run(clean())
    assert not findings, [f'{f.kind}: {f.found}' for f in findings]
    assert not applied


@check
def missing_uncertainty_is_one_finding():
    s = clean()
    s['exchanges'].loc[1, ['uncertainty type', 'variance'] + list(PEDIGREE)] = None
    s['exchanges'].loc[2, list(PEDIGREE)] = None
    found = [f for f in run(s)[0] if f.kind == 'uncertainty or pedigree missing']
    assert len(found) == 1 and 'urea' in found[0].note and 'Ammonia' in found[0].note


@check
def pedigree_score_outside_range():
    s = clean()
    s['exchanges'].loc[1, 'reliability'] = 7
    assert 'pedigree score outside 1-5' in kinds(run(s)[0])


@check
def own_product_without_mandatory_properties_or_price():
    s = clean()
    s['properties'] = s['properties'][~s['properties']['propertyName'].isin(['dry mass', 'price'])]
    k = kinds(run(s)[0])
    assert {'mandatory properties missing', 'price missing'} <= k


@check
def property_equations():
    s = clean()
    p = s['properties']
    p.loc[p['propertyName'] == 'wet mass', 'amount'] = 2.0
    assert 'property equations do not hold' in kinds(run(s)[0])


@check
def unit_against_master_data():
    s = clean()
    s['exchanges'].loc[1, 'unitName'] = 'MJ'
    hits = [f for f in run(s)[0] if f.kind == 'unit differs from Master Data']
    assert hits and hits[0].expected == 'kg'


@check
def duplicated_flow():
    s = clean()
    s['exchanges'] = pd.concat([s['exchanges'], s['exchanges'].iloc[[1]]], ignore_index=True)
    assert 'flow entered more than once' in kinds(run(s)[0])


@check
def template_residue_is_emptied():
    s = clean()
    s['exchanges']['PV_comment'] = pd.Series([None, None, 'leave empty'], dtype=object)
    findings, applied = run(s)
    assert 'template instruction text left in a data cell' in kinds(findings)
    assert s['exchanges']['PV_comment'].isna().all() and applied


@check
def undefined_formula_variable():
    s = clean()
    s['exchanges']['mathematicalRelation'] = [None, 'yield_kg * 0.02', None]
    hits = [f for f in run(s)[0] if f.kind == 'formula uses an undefined variable']
    assert hits and hits[0].found == 'yield_kg'
    s['exchanges']['variableName'] = ['yield_kg', None, None]
    assert 'formula uses an undefined variable' not in kinds(run(s)[0])


@check
def orphan_properties():
    s = clean()
    s['properties'] = pd.concat([s['properties'], pd.DataFrame(
        [prop('olive fruit', 'ReferenceProduct', 'dry mass', 0.5, 'kg')])], ignore_index=True)
    assert 'properties match no exchange' in kinds(run(s)[0])


@check
def agricultural_air_emission_always_goes_to_non_urban_air():
    kind = 'agricultural air emission not in non-urban air'
    s = clean()
    s['exchanges'].loc[2, 'subcompartment'] = 'low population density, long-term'
    findings, applied = run(s)
    hits = [f for f in findings if f.kind == kind]
    assert len(hits) == 1 and hits[0].fixed and not hits[0].options        # no decision asked
    assert s['exchanges'].loc[2, 'subcompartment'] == K.AIR_AGRI_SUB and applied
    # Already in non-urban air too: the amount joins that row, no duplicate.
    s = clean()
    s['exchanges'] = pd.concat([s['exchanges'], pd.DataFrame([exchange(
        'ToEnvironment', 'Ammonia', 'kg', 0.002, 'air', 'low population density, long-term')])],
        ignore_index=True)
    run(s)
    ammonia = s['exchanges'][s['exchanges']['name'] == 'Ammonia']
    assert len(ammonia) == 1 and abs(ammonia['amount'].iloc[0] - 0.003) < 1e-12
    # apply = no in Corrections keeps the dataset's placement.
    s = clean()
    s['exchanges'].loc[2, 'subcompartment'] = 'low population density, long-term'
    K.run(s, 'test.xlsx', ACT, 'tests', {}, M.Finding, M.EXCHANGE_PREFIX, M.placement_column,
          rejected=lambda key: kind in key)
    assert s['exchanges'].loc[2, 'subcompartment'] == 'low population density, long-term'


@check
def livestock_checks_only_on_livestock():
    s = clean()
    s['exchanges'] = pd.concat([s['exchanges'], pd.DataFrame([
        exchange('ToEnvironment', 'Nitrate', 'kg', 0.01, 'water', 'surface water')])],
        ignore_index=True)
    assert 'nitrate not to ground water' not in kinds(run(s)[0])        # a crop
    s['meta'].loc[0, 'activityName'] = 'cow milk production, suckler herd'
    k = kinds(run(s)[0])
    assert {'nitrate not to ground water', 'culled cow missing from a suckler herd'} <= k


@check
def one_answer_for_every_dataset_except_figures():
    def finding(dataset, column, kind, found):
        return M.Finding(dataset, ACT, column, found, '', kind, False, 'BLOCKING', 'n', '',
                         'tests', '', ())
    fs = [finding(f'd{i}.xlsx', 'exchange:Pyriproxifen', 'exchange name is not an ecoinvent '
                  'elementary flow', 'Pyriproxifen') for i in range(3)]
    fs += [finding(f'd{i}.xlsx', 'generalComment', 'yield line missing', '(no yield line)')
           for i in range(3)]
    groups = {g['kind']: g for g in R.group_findings(fs)}
    assert groups['exchange name is not an ecoinvent elementary flow']['shared']
    assert len(groups['exchange name is not an ecoinvent elementary flow']['findings']) == 3
    assert not groups['yield line missing']['shared']


@check
def checklist_rows_of_a_field():
    f = M.Finding('d.xlsx', ACT, 'geographyComment', '', '', 'x', False, 'BLOCKING', '',
                  '28-59 (Checks)', 'tests')
    assert R.finding_rows(f) == [('Checks', 48)]
    y = M.Finding('d.xlsx', ACT, 'generalComment', '', '', 'yield line missing', False,
                  'BLOCKING', '', '28-34 (Checks) / 3 (agri)', 'tests')
    assert R.finding_rows(y) == [('agri', 3)]


@check
def period_in_agricultural_seasons():
    import meta_spec as S
    import generate_metadata as G
    assert S.agricultural_seasons('2021-08-01', '2022-08-01') == 1
    assert S.agricultural_seasons('2023-05-15', '2026-05-15') == 3
    assert S.agricultural_seasons('2015-01-01', '2020-12-31') == 6
    assert S.stated_average_years('over the entire 5-year production period') == 5
    assert S.stated_average_years('[Provide ... a five-year average ...]') is None
    one = G.generate_time_period_comment(2021, 2022, n_years=1)
    assert 'one agricultural season (2021 - 2022)' in one
    stand = G.generate_time_period_comment(2021, 2022, n_years=1, average_years=5)
    assert 'five-year production period' in stand and 'one agricultural season' not in stand
    row = pd.Series({'startDate': '2021-08-01', 'endDate': '2022-08-01', 'timePeriodComment': '',
                     'generalComment': 'The yield is calculated over the entire 5-year production period.'})
    kinds = [item[3] for item in M.review_period(row, False)]
    assert 'the dates do not cover the generalComment period' in kinds


def _exchanges(*rows):
    return {'exchanges': pd.DataFrame([dict(zip(('group', 'name', 'compartment', 'subcompartment',
                                                 'unitName', 'amount'), r)) for r in rows])}


@check
def product_entered_as_resource_goes_to_technosphere():
    sheets = _exchanges(('FromEnvironment', 'pesticide, unspecified', None, None, 'kg', 0.000278),
                        ('FromEnvironment', 'Water, well, in ground', 'natural resource', 'in water', 'm3', 1.0))
    applied = {}
    found = M.fix_elementary_groups(sheets, 'd.xlsx', ACT, 'tests', applied)
    e = sheets['exchanges']
    assert e.loc[0, 'group'] == 'FromTechnosphere' and e.loc[0, 'name'] == 'pesticide, unspecified'
    assert e.loc[1, 'group'] == 'FromEnvironment'               # a real resource stays
    assert any(f.kind == 'product moved to its group' for f in found)


@check
def missing_group_read_from_compartment():
    sheets = _exchanges((None, 'Abamectine', 'water', 'ground-', 'kg', 6.48e-07),
                        (None, 'Water, river', 'natural resource', 'in water', 'm3', 1.0),
                        (None, 'something', None, None, 'kg', 1.0))
    applied = {}
    found = M.normalise_exchange_vocabulary(sheets, 'd.xlsx', ACT, 'tests', applied)
    e = sheets['exchanges']
    assert e.loc[0, 'group'] == 'ToEnvironment' and e.loc[1, 'group'] == 'FromEnvironment'
    assert pd.isna(e.loc[2, 'group'])                          # nothing to read it from
    left = [f for f in found if f.kind == 'exchange group missing' and not f.fixed]
    assert len(left) == 1 and left[0].severity == 'BLOCKING'


@check
def blank_unit_filled_from_master_data():
    s = clean()
    s['exchanges'] = pd.concat([s['exchanges'], pd.DataFrame([
        exchange('ToEnvironment', 'Mineral oil', None, 0.00003, 'water', 'unspecified')])],
        ignore_index=True)
    findings, applied = run(s)
    assert s['exchanges'].iloc[-1]['unitName'] == 'kg'
    assert any(f.kind == 'unit missing' and f.fixed for f in findings) and applied


@check
def product_as_resource_with_a_compartment_is_a_decision():
    sheets = _exchanges(('FromEnvironment', 'pesticide, unspecified', 'natural resource', 'in ground', 'kg', 1.0))
    found = M.fix_elementary_groups(sheets, 'd.xlsx', ACT, 'tests', {})
    assert sheets['exchanges'].loc[0, 'group'] == 'FromEnvironment'     # not moved
    decision = [f for f in found if f.kind == 'product name entered as a resource']
    assert decision and decision[0].options[0][1] == 'FromTechnosphere'
    ok, _ = M.apply_fix(sheets | {'meta': pd.DataFrame([{'activityName': ACT}])},
                        decision[0].column, decision[0].kind, 'FromTechnosphere')
    assert ok and sheets['exchanges'].loc[0, 'group'] == 'FromTechnosphere'


@check
def thousands_comma_in_yields():
    import meta_spec as S
    import generate_metadata as G
    sys.path.insert(0, os.path.join(HERE, '..', 'calculators', 'agri_diesel'))
    import agri_diesel_calculator_new as D
    for text, value in (('12,582', 12582.0), ('1,900', 1900.0), ('1,234,567', 1234567.0),
                        ('8604,16', 8604.16), ('0,125', 0.125), ('26,299.94', 26299.94),
                        ('17.089,84', 17089.84), ('12582', 12582.0)):
        assert S.parse_number_loose(text) == value, (text, S.parse_number_loose(text))
        assert D._parse_number(text) == value, (text, D._parse_number(text))
    assert G.format_yield('12,582') == '12582.00' and G.format_yield('0,125') == '0.12'
    assert D.extract_yield('Yield: 12,582 kg/ha') == 12582.0


@check
def by_product_everywhere():
    import meta_spec as S
    assert S.apply_house_wording('Milk is a co-product; Co-products are allocated.') ==         'Milk is a by-product; By-products are allocated.'
    sheets = {'exchanges': pd.DataFrame([{'name': 'milk', 'comment': 'sold as a co-product'}])}
    M.apply_wording_to_comments(sheets, 'd.xlsx', ACT, 'tests', {})
    assert sheets['exchanges'].loc[0, 'comment'] == 'sold as a by-product'


@check
def yield_line_carries_the_yield_in_use():
    import meta_spec as S
    text = ('The dry matter yield is 12,582 kg/ha. Applying a 60% utilization factor, the '
            'effective yield considered in the dataset is 7,549 kg/ha.')
    assert '[7549.00]' in S.apply_yield_format(text)[0]
    assert '[12582.00]' in S.apply_yield_format('Yield: 12,582 kg/ha. In year 2, 13,500 kg/ha.')[0]


@check
def placement_answer_merges_into_an_existing_row_and_flags_it():
    rows = [('ToEnvironment', 'Mineral oil', 'air', 'non-urban air or from high stacks', 'kg', 0.0001),
            ('ToEnvironment', 'Mineral oil', 'soil', 'agricultural', 'kg', 0.001)]
    sheets = _exchanges(*rows)
    sheets['exchanges']['comment'] = ['air share', 'soil share']
    sheets['meta'] = pd.DataFrame([{'activityName': ACT}])
    column = M.placement_column('Mineral oil', 'air', 'non-urban air or from high stacks')
    ok, _ = M.apply_fix(sheets, column, 'exchange is in a compartment the flow does not have',
                        'soil / agricultural')
    e = sheets['exchanges']
    assert ok and len(e) == 1 and abs(e.loc[0, 'amount'] - 0.0011) < 1e-12
    assert 'moved from air / non-urban air or from high stacks' in e.loc[0, 'comment']


@check
def production_volume_kept_only_on_own_products():
    sheets = {'exchanges': pd.DataFrame([
        {'group': 'ReferenceProduct', 'name': 'lemon', 'PV_comment': 'FAOSTAT 2020'},
        {'group': 'ByProduct', 'name': 'straw', 'PV_comment': 'estimated'},
        {'group': 'FromEnvironment', 'name': 'Occupation, permanent crop', 'PV_comment': '7.33'},
        {'group': 'FromTechnosphere', 'name': 'zinc monosulfate', 'PV_comment': 'leave empty'}])}
    M.clear_production_volume_off_products(sheets, 'd.xlsx', ACT, 'tests', {})
    pv = sheets['exchanges']['PV_comment'].tolist()
    assert pv[:2] == ['FAOSTAT 2020', 'estimated'] and pv[2] is None and pv[3] is None


@check
def production_word_options_put_the_product_first():
    import meta_spec as S
    assert not S.lacks_production_word('wheat grain production, spring, conventional, hill region')
    assert not S.lacks_production_word('market for grass')
    first = lambda name, product=None: S.production_name_options(name, product)[0][1]
    assert first('alfalfa-grass silage', 'alfalfa-grass silage') == 'alfalfa-grass silage production'
    assert first('fescue grass cattle grazing', 'hay') == 'fescue grass production, cattle grazing'
    assert first('alfalfa-grass cattle grazing', 'alfalfa-grass cattle grazing') \
        == 'alfalfa-grass production, cattle grazing'
    assert first('grass, organic') == 'grass production, organic'
    assert S.production_name_options('grass', 'grass')[-1] == (S.PRODUCTION_KEEP_LABEL, 'grass')


@check
def rename_reaches_every_sheet_whatever_the_spacing():
    sheets = {
        'meta': pd.DataFrame([{'activityName': 'grass'}]),
        'exchanges': pd.DataFrame([{'activityName': 'Grass ', 'activityLink_activityName': None},
                                   {'activityName': 'hay making', 'activityLink_activityName': 'grass'}]),
    }
    ok, _ = M.apply_fix(sheets, 'activityName', M.PRODUCTION_NAME_KIND, 'grass production')
    assert ok and sheets['meta'].loc[0, 'activityName'] == 'grass production'
    assert sheets['exchanges']['activityName'].tolist() == ['grass production', 'hay making']
    assert sheets['exchanges'].loc[1, 'activityLink_activityName'] == 'grass production'


@check
def multinutrient_split_rows_always_carry_uncertainty():
    sys.path.insert(0, os.path.join(HERE, '..', 'calculators', 'multinutrient'))
    import multinutrient_calculator_new as MN
    fert = next(iter(MN._STRICT_LOOKUP))
    rows = [{'activityName': 'a', 'group': 'FromTechnosphere', 'name': fert, 'amount': 10.0},
            {'activityName': 'b', 'group': 'FromTechnosphere', 'name': fert, 'amount': 10.0,
             'uncertainty type': 'normal', 'variance': 0.5, 'reliability': 5}]
    out = MN.split_multinutrient_exchanges(pd.DataFrame(rows))
    split = out[out['name'].str.contains(', from ')]
    assert len(split) >= 2
    for column, value in MN.MULTINUTRIENT_DQS.items():
        assert (split[column] == value).all(), column


@check
def dqg_basic_uncertainty_and_pedigree_filled_except_own_products():
    full = dict(zip(K.PEDIGREE, (2, 3, 1, 2, 1)))
    e = pd.DataFrame([
        {'activityName': ACT, 'group': 'ReferenceProduct', 'name': 'grass', 'unitName': 'kg'},
        {'activityName': ACT, 'group': 'ByProduct', 'name': 'straw', 'unitName': 'kg'},
        {'activityName': ACT, 'group': 'FromTechnosphere', 'name': 'urea', 'unitName': 'kg',
         'uncertainty type': 'lognormal', 'variance': 0.0006, **full},
        {'activityName': ACT, 'group': 'FromTechnosphere', 'name': 'transport, lorry',
         'unitName': 'tkm'},
        {'activityName': ACT, 'group': 'ToEnvironment', 'name': 'Ammonia', 'unitName': 'kg',
         'compartment': 'air', 'subcompartment': 'non-urban air or from high stacks'},
        {'activityName': ACT, 'group': 'FromEnvironment', 'name': 'Occupation, annual crop',
         'unitName': 'm2*year', 'compartment': 'natural resource', 'subcompartment': 'land',
         'uncertainty type': 'normal'}])
    applied = {}
    K.fill_dqg_uncertainty(e, 'd.xlsx', 'tests', applied, lambda k: False,
                           lambda *a, **k: None)
    assert pd.isna(e.loc[0, 'variance']) and pd.isna(e.loc[1, 'variance'])     # own products
    assert e.loc[3, 'variance'] == 0.12 and e.loc[4, 'variance'] == 0.008
    assert e.loc[4, 'uncertainty type'] == 'lognormal'
    assert pd.isna(e.loc[5, 'variance'])                  # a normal keeps its own parameters
    assert e.loc[3, 'reliability'] == 2                   # pedigree of the other inputs
    assert any('transport, lorry' in d for d, _ in applied.values())


@check
def deletions_are_logged_with_their_content():
    import tempfile
    import config_new_datasets as C
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, 'd.xlsx')
    rows = pd.DataFrame([{'group': 'FromTechnosphere', 'name': 'urea', 'amount': 0,
                          'unitName': 'kg', 'activityName': ACT}])
    C.record_deletions(path, 'meta model', 'exchanges', rows, 'zero amount')
    C.record_deletions(path, 'meta model', 'exchanges', rows, 'zero amount')   # again: one entry
    entries = C.read_deletions(folder)
    assert len(entries) == 1 and entries[0]['deleted'].startswith('urea [FromTechnosphere]: 0 kg')
    note = C.deleted_note(['a', 'b'])
    assert C.deleted_items('3 cells emptied. ' + note) == ['a', 'b']


@check
def comments_are_built_from_the_facts_sheet():
    import meta_facts as F
    facts = pd.DataFrame([
        {'activityName': '[explanation] one row per activity', 'data_source': 'x'},
        {'activityName': ACT, 'data_source': 'survey', 'data_source_reference': 'Farm survey (2021)',
         'farms_sampled': 12, 'extrapolations': 'Fertiliser rates taken from the centre'}])
    F.load('', 'd.xlsx', {'facts': facts})
    try:
        row = pd.Series({'activityName': ACT, 'geography': 'IT', 'startDate': '2021-01-01',
                         'endDate': '2021-12-31', 'technologyComment': 'Statistical data.'})
        sampling = M.generate_column('modellingAndValidation_samplingProcedure', row)
        assert 'measurements collected on field' in sampling       # the provider wins
        assert '12 farms' in sampling and 'Source: Farm survey (2021).' in sampling
        assert 'collected in the field' in M.generate_column('timePeriodComment', row)
        assert M.extrapolations_text(row, None) == 'Fertiliser rates taken from the centre.'
        assert F.facts_for(row)['data_source'] == ('field', F.PROVIDED)
    finally:
        F.load('', 'd.xlsx', {})


@check
def facts_are_derived_from_the_dataset_when_not_given():
    import meta_facts as F
    F.load('', 'd.xlsx', {})
    row = pd.Series({'activityName': ACT, 'technologyComment':
                     'Data were collected on field from 60 farmers in 2021 (Ribaudo, 2017; '
                     'Zampori and Pant (2019), August 2024).'})
    facts = F.facts_for(row)
    assert facts['data_source'] == ('field', 'technologyComment')
    assert facts['farms_sampled'] == (60, 'technologyComment')
    assert facts['data_source_reference'][0] == 'Ribaudo (2017); Zampori and Pant (2019)'
    assert F.citations('Ribaudo, 2017) and Ribaudo (2017)') == ['Ribaudo (2017)']


@check
def fields_are_checked_against_each_other():
    row = pd.Series({'activityName': 'grape production, organic, irrigated, south',
                     'geography': 'IT', 'startDate': '2021-01-01', 'endDate': '2023-12-31',
                     'geographyComment': 'Data refer to farms in Spain and Italy.'})
    e = pd.DataFrame([
        {'activityName': row['activityName'], 'group': 'ReferenceProduct', 'name': 'grape',
         'amount': 1, 'PV_comment': 'FAOSTAT, average 2009-2012'},
        {'activityName': row['activityName'], 'group': 'FromTechnosphere',
         'name': 'inorganic nitrogen fertiliser, as N', 'amount': 0.01, 'PV_comment': None},
        {'activityName': row['activityName'], 'group': 'FromEnvironment',
         'name': 'Occupation, permanent crop, non-irrigated, intensive', 'amount': 1.0,
         'PV_comment': None}])
    import meta_facts as F
    kinds = {k for _c, _f, _e, k, _s, _n, _r in F.cross_field_checks(row, e)}
    assert 'name says irrigated, the inventory has no irrigation' in kinds
    assert 'land-use flows contradict the water regime' in kinds
    assert 'organic production with synthetic nitrogen fertiliser' in kinds
    assert 'production volume source outside the dataset period' in kinds
    assert "another country named than the dataset's" in kinds
    # a rainfed crop's land-use flows are not irrigation
    assert not M.has_irrigation_exchange(row, e)



def _product_sheets():
    return {
        'exchanges': pd.DataFrame([
            {'activityName': ACT, 'group': 'ReferenceProduct', 'name': 'grass, tall fescue',
             'unitName': 'kg', 'amount': 1.0},
            {'activityName': ACT, 'group': 'FromTechnosphere', 'name': 'hay', 'unitName': 'kg',
             'amount': 0.2}]),
        'properties': pd.DataFrame([
            {'activityName': ACT, 'group': 'FromTechnosphere', 'name': 'hay',
             'propertyName': 'wet mass', 'amount': 1.5, 'unitName': 'kg', 'comment': None},
            {'activityName': ACT, 'group': 'ReferenceProduct', 'name': 'grass, tall fescue',
             'propertyName': 'price', 'amount': 0.09, 'unitName': 'EUR2005', 'comment': None}]),
        'meta': pd.DataFrame([{'activityName': ACT}]),
    }


@check
def a_typed_price_sets_the_price_never_the_name():
    sheets = _product_sheets()
    ok, _ = M.apply_fix(sheets, 'exchange:hay', 'price missing', '0.63')
    p = sheets['properties']
    assert ok and (sheets['exchanges']['name'] == 'hay').sum() == 1          # not renamed
    price = p[(p['name'] == 'hay') & (p['propertyName'] == 'price')]
    assert price['amount'].tolist() == [0.63] and price['unitName'].tolist() == ['EUR2005']
    ok, why = M.apply_fix(sheets, 'exchange:hay', 'product name not an ecoinvent product', '0.63')
    assert not ok and 'number' in why
    ok, _ = M.apply_fix(sheets, 'exchange:grass, tall fescue', 'mandatory properties missing',
                        'dry mass = 1; wet mass = 6.667')
    p = sheets['properties']
    assert ok and p[(p['name'] == 'grass, tall fescue') & (p['propertyName'] == 'wet mass')
                    ]['amount'].tolist() == [6.667]


@check
def master_data_properties_are_the_standard():
    import skill_checks
    saved = skill_checks._INDEX
    skill_checks._INDEX = {'properties': {
        'hay': {'wet mass': {'amount': 1.1236, 'unitName': 'kg'},
                'dry mass': {'amount': 1.0, 'unitName': 'kg'}},
        'grass, tall fescue': {'price': {'amount': 0.5, 'unitName': 'EUR2005'},
                               'dry mass': {'amount': 1.0, 'unitName': 'kg'}}}}
    try:
        sheets, applied = _product_sheets(), {}
        M.apply_master_data_properties(sheets, 'd.xlsx', ACT, 'tests', applied)
        p = sheets['properties']
        value = lambda n, q: p[(p['name'] == n) & (p['propertyName'] == q)]['amount'].tolist()
        assert value('hay', 'wet mass') == [1.1236]                     # replaced
        assert value('hay', 'dry mass') == [1.0]                        # added
        assert value('grass, tall fescue', 'price') == [0.09]           # own price kept
        assert 'hay: wet mass 1.5 kg -> 1.1236 kg' in next(iter(applied.values()))[0]
    finally:
        skill_checks._INDEX = saved


@check
def the_calculation_fingerprint_follows_only_what_is_calculated_from():
    import tempfile
    import config_new_datasets as C
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, 'd.xlsx')

    def save(comment, amount):
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame([{'activityName': ACT, 'generalComment': comment}]).to_excel(
                writer, sheet_name='meta', index=False)
            pd.DataFrame([{'activityName': ACT, 'group': 'FromTechnosphere', 'name': 'urea',
                           'amount': amount}]).to_excel(writer, sheet_name='exchanges', index=False)
        return C.calculation_fingerprint(path)

    base = save('Grapes. Yield: [9000.00] kg/ha.', 0.1)
    assert save('Wine grapes. Yield: [9000.00] kg/ha.', 0.1) == base     # wording only
    assert save('Grapes. Yield: [9500.00] kg/ha.', 0.1) != base          # the yield
    assert save('Grapes. Yield: [9000.00] kg/ha.', 0.2) != base          # an input


@check
def edits_in_a_split_excel_are_told_apart():
    import shutil
    import tempfile
    import split_edits as S
    folder = tempfile.mkdtemp()
    part = os.path.join(folder, 'part_1.xlsx')
    rows = [{'activityName': 'a', 'group': 'FromTechnosphere', 'name': f'input {i}',
             'amount': float(i), 'comment': 'old'} for i in range(5)]

    def save(frame):
        with pd.ExcelWriter(part) as writer:
            frame.to_excel(writer, sheet_name='exchanges', index=False)

    save(pd.DataFrame(rows))
    built = os.path.join(folder, 'built.xlsx')
    shutil.copy2(part, built)
    edited = pd.DataFrame(rows)
    edited['comment'] = 'new'                                 # every row changed
    edited = edited.drop(index=1)                             # one deleted
    edited = pd.concat([edited, pd.DataFrame([{**rows[0], 'name': 'added'}])])
    save(edited.reset_index(drop=True))
    edits = S.edits_in(part, built)
    changed = [(b['name'], a['comment']) for _s, b, a in edits if b is not None and a is not None]
    assert sorted(changed) == [(f'input {i}', 'new') for i in (0, 2, 3, 4)]
    assert [b['name'] for _s, b, a in edits if a is None] == ['input 1']
    assert [a['name'] for _s, b, a in edits if b is None] == ['added']


@check
def a_split_excel_with_unsaved_changes_stops_the_recheck():
    import tempfile
    import split_edits as S
    root = tempfile.mkdtemp()
    part = os.path.join(root, 'part_1.xlsx')
    pd.DataFrame([{'name': 'x'}]).to_excel(part, index=False)
    saved_dir, saved_parts = os.environ.get('APPDATA'), S._parts
    os.environ['APPDATA'] = root
    S._parts = lambda project: [part]
    try:
        assert S.unsaved_in_excel('p') == []
        excel = os.path.join(root, 'Microsoft', 'Excel', 'part_1312831781979098421')
        os.makedirs(excel)
        older = os.path.join(excel, 'part_1((Unsaved-1)).xlsb')
        open(older, 'w').close()
        os.utime(older, (os.path.getmtime(part) - 60,) * 2)       # saved since: fine
        assert S.unsaved_in_excel('p') == []
        os.utime(older, (os.path.getmtime(part) + 60,) * 2)       # newer than the part
        assert len(S.unsaved_in_excel('p')) == 1
        os.remove(older)
        newer = os.path.join(excel, 'part_10((Unsaved-1)).xlsb')  # another part's
        open(newer, 'w').close()
        os.utime(newer, (os.path.getmtime(part) + 60,) * 2)
        assert S.unsaved_in_excel('p') == []
        os.remove(newer)
        # A name with spaces: Excel keeps its copy under the name URL-encoded.
        spaced = os.path.join(root, 'grass, AR - part 1.xlsx')
        os.rename(part, spaced)
        S._parts = lambda project: [spaced]
        encoded = os.path.join(excel, 'grass,%20AR%20-%20part%201((Unsaved-2)).xlsb')
        open(encoded, 'w').close()
        os.utime(encoded, (os.path.getmtime(spaced) + 60,) * 2)
        assert len(S.unsaved_in_excel('p')) == 1
        os.remove(encoded)
        open(os.path.join(root, '~$grass, AR - part 1.xlsx'), 'w').close()    # open in Excel
        assert any('open in Excel' in p for p in S.unsaved_in_excel('p'))
    finally:
        S._parts = saved_parts
        if saved_dir is None:
            os.environ.pop('APPDATA')
        else:
            os.environ['APPDATA'] = saved_dir


@check
def the_message_to_the_provider_says_what_was_done_and_what_is_needed():
    import provider_message as P

    def action(kind, datasets, column, found='', expected='', note=''):
        finding = M.Finding('x.xlsx', datasets[0], column, found, expected, kind, False,
                            'BLOCKING', note, '', 'p')
        return {'issue': kind, 'severity': 'Critical', 'datasets': datasets, 'finding': finding}

    actions = [
        action('includedActivitiesStart opening not standard', ['A', 'B'], 'includedActivitiesStart'),
        action('includedActivitiesStart opening not standard', ['C'], 'includedActivitiesStart'),
        action('price missing', ['C'], 'exchange:fescue grazing'),
        action('nomenclature', ['A'], 'activityName'),                      # internal
        action('something new', ['B'], 'exchange:urea', note='Urea is odd. More text.'),
    ]
    history = pd.DataFrame([
        {'dataset': 'a.xlsx', 'field': 'exchange:hay', 'issue': 'price missing',
         'after': "1 exchange row(s) renamed to '0.63'"},                   # misread answer
        {'dataset': 'a.xlsx', 'field': 'split Excel', 'issue': 'edited in the split Excel',
         'after': '[exchanges] added: alfalfa grazing [ReferenceProduct]: 1.0 kg; '
                  '[properties] added: alfalfa grazing [ReferenceProduct], property'},
        {'dataset': 'a.xlsx', 'field': 'properties', 'issue': 'Master Data properties applied',
         'after': '9 propert(ies)'},
    ])
    products = {'a.xlsx': {'alfalfa grazing': {**{p: '1' for p in P.MANDATORY},
                                               'price': '0.13 EUR2005'}}}
    text = '\n'.join(P.lines('grass, AR', actions, history,
                             {'a.xlsx': 'A', 'b.xlsx': 'B', 'c.xlsx': 'C'}, products))
    assert text.count('Included activities start') == 1 and '(all three datasets)' in text, text
    assert '"fescue grazing" price (C)' in text
    assert 'cannot be checked' not in text and 'nomenclature' not in text
    assert '"urea" (B): Urea is odd.' in text
    assert "'0.63'" not in text and '"0.63"' not in text
    assert 'The product is now named "alfalfa grazing".' in text
    assert '"alfalfa grazing" now has its full set of properties and a price of 0.13 EUR2005.' in text
    assert 'aligned with the ecoinvent Master Data' in text
    done = P.lines('p', [], pd.DataFrame(columns=history.columns), {'a.xlsx': 'A'}, {})
    assert any('Nothing further is needed' in line for line in done)


@check
def the_name_follows_the_inventory_and_the_text():
    import meta_spec as spec

    def name_of(name, comment, irrigation):
        row = pd.Series({'activityName': name, 'geography': 'IT', 'generalComment': comment})
        rows = [exchange('ReferenceProduct', name.split(',')[0].replace(' production', ''),
                         'kg', 1.0)]
        if irrigation:
            rows.append(exchange('FromTechnosphere', 'irrigation', 'm3', 0.1))
        return M.evidence_name(row, pd.DataFrame(rows))

    # Missing axes: water regime from the inventory, system from the text.
    name, reasons, _ = name_of('wheat grain production', 'Organic farms, organic methods.', False)
    assert name == 'wheat grain production, organic, rainfed', name
    # The name says irrigated, the inventory buys no irrigation: it follows the inventory,
    # keeping the dry wording it would use; the text saying otherwise is noted.
    name, _, notes = name_of('olive production, integrated, irrigated', 'Irrigated orchards.', False)
    assert name == 'olive production, integrated, rainfed' and notes, (name, notes)
    name, _, _ = name_of('olive production, integrated, non irrigated', '', True)
    assert name == 'olive production, integrated, irrigated'
    # One passing mention does not outvote the text: organic stays organic.
    text = 'Organic soybean on organic farms; compared with conventional standards. Organic seed.'
    name, _, _ = name_of('soybean production, organic, rainfed', text, False)
    assert name == 'soybean production, organic, rainfed'
    # "non‑irrigated" with a non-breaking hyphen is not read as "irrigated".
    assert spec.axis_value_in_text('cultivated in non‑irrigated orchards', 'water_regime') \
        == 'non-irrigated'
    # Nothing to change: the same name, no reasons.
    name, reasons, _ = name_of('olive production, integrated, non irrigated', 'Integrated.', False)
    assert name == 'olive production, integrated, non irrigated' and not reasons


@check
def a_nonstandard_included_start_gets_the_standard_opening():
    import meta_spec as spec
    said = ('This activity assumes the production of alfalfa for five years. It begins with '
            'soil cultivation and sowing after harvesting the previous crop. Seeds are included.')
    assert spec.standard_included_start(said, 'This activity starts with X.') == (
        'This activity starts with soil cultivation and sowing after harvesting the previous '
        'crop. This activity assumes the production of alfalfa for five years. Seeds are included.')
    # No sentence says where it starts: the opening built from the exchanges goes first.
    plain = 'This activity represents a pasture. Seeds are included.'
    built = 'This activity starts with the first field operation for the grass.'
    fixed = spec.standard_included_start(plain, built)
    assert fixed == f'{built} {plain}' and spec.included_start_status(fixed)[0] == 'ok'


def with_comments(*rows, yield_kg_ha=10000.0):
    """clean() plus exchanges carrying comments; run at the given yield."""
    s = clean()
    s['exchanges'] = pd.concat([s['exchanges'], pd.DataFrame(list(rows))], ignore_index=True)
    applied = {}
    findings = K.run(s, 'test.xlsx', ACT, 'tests', applied, M.Finding, M.EXCHANGE_PREFIX,
                     M.placement_column, yield_of=lambda activity: yield_kg_ha)
    return [f for f in findings if f.checklist_row == K.ROW_COMMENT_AMOUNTS]


@check
def passes_stated_in_the_comment():
    # 2.5 passes at 10 t/ha are 0.00025 ha per kg.
    said = 'Data based on Ribaudo (2017). The quantity corresponds to 2.5 applications.'
    assert not with_comments(exchange('FromTechnosphere', 'mulching', 'ha', 0.00025, comment=said))
    hits = with_comments(exchange('FromTechnosphere', 'mulching', 'ha', 0.0003, comment=said))
    assert [f.kind for f in hits] == ['amount contradicts its comment'], hits


@check
def trees_stated_in_the_comment():
    said = ('Data collected on field refers on  400 of trees per ha divided by the lifetime '
            'years of the orchard (25 years).')
    seedling = 'fruit tree seedling, for planting'
    assert not with_comments(exchange('FromTechnosphere', seedling, 'unit', 0.0016, comment=said))
    assert with_comments(exchange('FromTechnosphere', seedling, 'unit', 0.002, comment=said))


@check
def planting_over_the_lifespan_or_its_productive_years():
    said = ('Data based on Ribaudo (2017). The quantity refers to the planting of 370 trees per '
            'hectare (planting grid 6 x 4.5 m) and a lifespan of 50 years, with the first '
            'three years unproductive. ')
    seedling = 'fruit tree seedling, for planting'
    for per_ha in (370 / 50, 370 / 47):                     # either reading is consistent
        assert not with_comments(exchange('FromTechnosphere', seedling, 'unit',
                                          per_ha / 10000, comment=said))
    assert with_comments(exchange('FromTechnosphere', seedling, 'unit', 370 / 40 / 10000,
                                  comment=said))


@check
def diesel_per_pass_against_its_field_operation():
    said = ('This flow represents the amount of kg of diesel consumed by the specific field '
            'operation. The diesel consumption of this field operation is 3 kg per hectare.')
    operation = exchange('FromTechnosphere', 'fertilising, by broadcaster', 'ha', 0.0002)
    right = exchange('FromTechnosphere', 'diesel used in fertilising, by broadcaster', 'kg',
                     0.0006, comment=said)
    wrong = dict(right, amount=0.0003)
    assert not with_comments(operation, right, yield_kg_ha=None)   # no yield needed
    assert with_comments(operation, wrong, yield_kg_ha=None)


@check
def irrigation_total_that_is_per_intervention():
    said = ('Irrigation process. The quantity refers to water and energy consumption per 55 '
            'micro-irrigation interventions, with a total use of approximately 30 m³ of water '
            'and 15.9 kWh of electricity.')
    assert not with_comments(exchange('FromTechnosphere', 'irrigation', 'm3', 0.003, comment=said))
    hits = with_comments(exchange('FromTechnosphere', 'irrigation', 'm3', 0.165, comment=said))
    assert hits and '55 is another number in the comment' in hits[0].note, hits


@check
def parts_listed_in_the_comment_sum_to_the_amount():
    said = 'Data collected on field – Abamectine (1,5E-07); Mineral Oil (8,92E-04);'
    pesticide = 'pesticide, unspecified'
    assert not with_comments(exchange('FromTechnosphere', pesticide, 'kg', 0.00089215,
                                      comment=said))
    assert with_comments(exchange('FromTechnosphere', pesticide, 'kg', 0.00096, comment=said))
    # A source year in brackets is no part.
    assert not with_comments(exchange('FromTechnosphere', 'sulfur', 'kg', 0.0035,
                                      comment='Data based on Ribaudo (2017), wettable'))


@check
def compartment_shares_stated_in_the_comment():
    said = ('Data based on Zampori and Pant (2019) - emissions of active ingredient (a.i.) in '
            'air (9%), water (1%) and soil (90%)')

    def split(air, water, soil):
        return [exchange('ToEnvironment', 'Pyriproxifen', 'kg', a, c, None, comment=said)
                for a, c in ((air, 'air'), (water, 'water'), (soil, 'soil'))]

    assert not with_comments(*split(0.09, 0.01, 0.9))
    assert with_comments(*split(0.2, 0.01, 0.79))


@check
def one_factor_across_an_activity_points_at_the_yield():
    said = 'The quantity corresponds to {} applications.'
    hits = with_comments(
        exchange('FromTechnosphere', 'mulching', 'ha', 0.000225, comment=said.format(2)),
        exchange('FromTechnosphere', 'hoeing', 'ha', 0.0004500, comment=said.format(4)))
    assert len(hits) == 2 and all('same factor' in f.note for f in hits), hits


@check
def per_hectare_figures_without_a_yield_are_reported_unchecked():
    said = 'The quantity corresponds to 2 applications.'
    hits = with_comments(exchange('FromTechnosphere', 'mulching', 'ha', 0.0002, comment=said),
                         yield_kg_ha=None)
    assert [(f.kind, f.severity) for f in hits] == [('comment figures not checked', 'INFO')]


if __name__ == '__main__':
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f'  ok    {fn.__name__}')
        except Exception as exc:
            failed += 1
            print(f'  FAIL  {fn.__name__}: {exc.__class__.__name__}: {exc}')
    print(f'\n{len(CHECKS) - failed}/{len(CHECKS)} passed')
    sys.exit(1 if failed else 0)
