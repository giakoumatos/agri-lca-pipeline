"""
SPOLD Reader for New Datasets Pipeline

Converts an EcoSpold2 (.spold) file to DataX Excel format
(sheets: meta, review, exchanges, properties, parameters).

Uses pyecospold — mirrors the reference at
C:\\python\\spold_converter_project\\spold_to_excel_datax_format.py

Author: Andreas Giakoumatos
Last Updated: 2026-08-25
"""

import math
import os
import sys
import pandas as pd
from pyecospold import parse_file_v2
from pyecospold.model_v2 import ActivityDataset

_NS = 'http://www.EcoInvent.org/EcoSpold02'

# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
from config_new_datasets import (
    META_COLS, REVIEW_COLS, EXCHANGES_COLS, PROPERTIES_COLS, PARAMETERS_COLS,
)

# ── Enum → text maps ──────────────────────────────────────────────────────────
_SAT_MAP = {
    0: 'ordinary transforming activity', 1: 'market activity',
    2: 'IO activity',                    3: 'residual activity mix',
    4: 'production mix',                 5: 'import activity',
    6: 'supply mix',                     7: 'export activity',
    8: 're-export activity',             9: 'correction activity',
    10: 'market group',
}
_TL_MAP = {
    0: 'undefined', 1: 'New', 2: 'Modern',
    3: 'Current',   4: 'Old', 5: 'Outdated',
}
_ACCESS_MAP = {
    0: 'Public', 1: 'Licensees', 2: 'Results only', 3: 'Individuals',
}
_PUBLISHED_MAP = {
    0: 'Data has been published entirely in',
    1: 'The data of some unit processes or subsystems are published',
    2: 'Data as such not published elsewhere',
    3: 'Data published in different form',
}

# pyecospold groupStr → DataX group name
_GROUP_MAP = {
    'ReferenceProduct':               'ReferenceProduct',
    'By-product':                     'ByProduct',
    'From Technosphere (unspecified)':'FromTechnosphere',
    'FromEnvironment':                'FromEnvironment',
    'ToEnvironment':                  'ToEnvironment',
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sg(obj, attr, default=''):
    """Safely get attribute, returning default for None/missing."""
    try:
        val = getattr(obj, attr, None) if obj is not None else None
        return default if val is None else val
    except Exception:
        return default


def _paragraphs(texts) -> str:
    """All <text> paragraphs of a comment, one per line -- the way the
    converter splits a multi-line comment back into paragraphs."""
    return '\n'.join(str(t).strip() for t in texts if t is not None and str(t).strip())


def _join(val):
    """Coerce a value or list to a single string."""
    if val is None:
        return ''
    if isinstance(val, list):
        parts = []
        for v in val:
            if v is None:
                continue
            if isinstance(v, str):
                parts.append(v)
            elif hasattr(v, 'texts') and v.texts:
                parts.append(_paragraphs(v.texts))
            elif hasattr(v, 'text') and v.text:
                parts.append(str(v.text))
            else:
                s = str(v)
                if s:
                    parts.append(s)
        return ', '.join(p for p in parts if p)
    if hasattr(val, 'texts') and val.texts:
        return _paragraphs(val.texts)
    if hasattr(val, 'text') and val.text:
        return str(val.text)
    return str(val) if val else ''


def _text1(obj, attr):
    """Get first string from a list-of-strings attribute, or '' if absent."""
    val = _sg(obj, attr, [])
    if isinstance(val, list):
        return str(val[0]) if val else ''
    return str(val) if val else ''


def _comment1(obj):
    """Get first comment string from a pyecospold object."""
    comments = _sg(obj, 'comments', [])
    if not comments:
        return ''
    c0 = comments[0]
    if isinstance(c0, str):
        return c0
    if hasattr(c0, 'texts') and c0.texts:
        return _paragraphs(c0.texts)
    return _join(c0)


def _unc_fields(uncertainty):
    """Extract all DataX uncertainty columns from a pyecospold uncertainty object."""
    out = {
        'uncertainty type': '', 'uncertainty comment': '',
        'variance': '', 'minValue': '', 'mostLikelyValue': '', 'maxValue': '',
        'reliability': '', 'completeness': '', 'temporalCorrelation': '',
        'geographicalCorrelation': '', 'furtherTechnologyCorrelation': '',
    }
    if uncertainty is None:
        return out

    unc_type = ''
    variance = min_val = most_likely = max_val = ''

    lg = _sg(uncertainty, 'lognormal', None)
    nm = _sg(uncertainty, 'normal', None)
    uf = _sg(uncertainty, 'uniform', None)
    tr = _sg(uncertainty, 'triangular', None)

    if lg is not None:
        unc_type = 'lognormal'
        # The basic variance: the converter adds the pedigree part back on
        # the way out, so reading the total here would count it twice.
        variance = str(_sg(lg, 'variance') or _sg(lg, 'varianceWithPedigreeUncertainty', ''))
    elif nm is not None:
        unc_type = 'normal'
        variance = str(_sg(nm, 'variance') or _sg(nm, 'varianceWithPedigreeUncertainty', ''))
    elif uf is not None:
        unc_type = 'uniform'
        min_val = str(_sg(uf, 'minValue', ''))
        max_val = str(_sg(uf, 'maxValue', ''))
    elif tr is not None:
        unc_type = 'triangular'
        min_val     = str(_sg(tr, 'minValue', ''))
        most_likely = str(_sg(tr, 'mostLikelyValue', ''))
        max_val     = str(_sg(tr, 'maxValue', ''))

    # pedigree matrix — attribute name varies by pyecospold version
    pm = None
    mats = _sg(uncertainty, 'pedigreeMatrices', [])
    if isinstance(mats, list) and mats:
        pm = mats[0]
    elif not isinstance(mats, list) and mats:
        pm = mats
    if pm is None:
        pm = _sg(uncertainty, 'pedigreeMatrix', None) or None

    out.update({
        'uncertainty type':    unc_type,
        'uncertainty comment': _comment1(uncertainty),
        'variance':            variance,
        'minValue':            min_val,
        'mostLikelyValue':     most_likely,
        'maxValue':            max_val,
        'reliability':                  str(_sg(pm, 'reliability', ''))                if pm is not None else '',
        'completeness':                 str(_sg(pm, 'completeness', ''))               if pm is not None else '',
        'temporalCorrelation':          str(_sg(pm, 'temporalCorrelation', ''))        if pm is not None else '',
        'geographicalCorrelation':      str(_sg(pm, 'geographicalCorrelation', ''))    if pm is not None else '',
        'furtherTechnologyCorrelation': str(_sg(pm, 'furtherTechnologyCorrelation','')) if pm is not None else '',
    })
    return out


def read_spold(spold_file: str) -> dict[str, pd.DataFrame]:
    """Parse a .spold file and return DataX DataFrames for all five sheets."""
    spold_obj = parse_file_v2(str(spold_file))
    # list(spold_obj) may include non-ActivityDataset lxml elements; filter them out
    activities = [a for a in list(spold_obj) if isinstance(a, ActivityDataset)]

    meta_rows, review_rows, exchange_rows, property_rows, parameter_rows = [], [], [], [], []

    for activity in activities:
        act_desc = activity.activityDescription
        act_obj  = act_desc.activity[0] if isinstance(act_desc.activity, list) else act_desc.activity

        # activity name
        activity_name = _text1(act_obj, 'activityNames') or _text1(act_obj, 'activityName')

        # geography shortname
        geo_list  = _sg(act_desc, 'geography', [])
        geo_obj   = geo_list[0] if isinstance(geo_list, list) and geo_list else (geo_list if geo_list else None)
        geography = _text1(geo_obj, 'shortNames') if geo_obj is not None else ''

        # ── META ─────────────────────────────────────────────────────────────
        tp_list   = _sg(act_desc, 'timePeriod',  [])
        tech_list = _sg(act_desc, 'technology',  [])
        cls_list  = _sg(act_desc, 'classification', [])
        tp   = tp_list[0]   if isinstance(tp_list, list)   and tp_list   else None
        tech = tech_list[0] if isinstance(tech_list, list) and tech_list else None
        cls  = cls_list[0]  if isinstance(cls_list, list)  and cls_list  else None

        adm = activity.administrativeInformation
        deb = _sg(adm, 'dataEntryBy')
        dgp = _sg(adm, 'dataGeneratorAndPublication')
        fa  = _sg(adm, 'fileAttributes')
        rep = _sg(activity.modellingAndValidation, 'representativeness')

        sat_raw = _sg(act_obj, 'specialActivityType', '')
        tl_raw  = _sg(tech,    'technologyLevel',     '') if tech is not None else ''

        def _tc(obj, attr):
            """Get first text from a TextAndImage-style attribute."""
            v = _sg(obj, attr, '')
            return _join(v)

        geo_comment  = ''
        if geo_obj is not None:
            geo_comments = _sg(geo_obj, 'comments', [])
            if isinstance(geo_comments, list) and geo_comments:
                c = geo_comments[0]
                geo_comment = _paragraphs(c.texts) if hasattr(c, 'texts') and c.texts else _join(c)

        tech_comment = ''
        if tech is not None:
            tech_comment = _comment1(tech)

        tp_comment = ''
        if tp is not None:
            tp_comment = _comment1(tp)

        alloc_comment = ''
        if hasattr(act_obj, 'allocationComment') and act_obj.allocationComment is not None:
            ac = act_obj.allocationComment
            alloc_comment = _paragraphs(ac.texts) if hasattr(ac, 'texts') and ac.texts else _join(ac)

        gen_comment = ''
        if hasattr(act_obj, 'generalComment') and act_obj.generalComment is not None:
            gc = act_obj.generalComment
            gen_comment = _paragraphs(gc.texts) if hasattr(gc, 'texts') and gc.texts else _join(gc)

        meta_rows.append({
            'activityId':          _sg(act_obj, 'id', ''),
            'activityNameId':      _sg(act_obj, 'activityNameId', ''),
            'activityName':        activity_name,
            'geography':           geography,
            'specialActivityType': _SAT_MAP.get(sat_raw, str(sat_raw) if sat_raw != '' else 'ordinary transforming activity'),
            'startDate':           str(_sg(tp, 'startDate', '')) if tp is not None else '',
            'endDate':             str(_sg(tp, 'endDate',   '')) if tp is not None else '',
            'technologyLevel':     _TL_MAP.get(tl_raw, str(tl_raw) if tl_raw != '' else 'Current'),
            'synonyms':            _join(_sg(act_obj, 'synonyms', [])),
            'tags':                _join(_sg(act_obj, 'tags', [])),
            'includedActivitiesStart': (lambda e: (e.text or '').strip() if e is not None else '')(act_obj.find('{%s}includedActivitiesStart' % _NS)),
            'includedActivitiesEnd':   (lambda e: (e.text or '').strip() if e is not None else '')(act_obj.find('{%s}includedActivitiesEnd'   % _NS)),
            'generalComment':      gen_comment,
            'geographyComment':    geo_comment,
            'allocationComment':   alloc_comment,
            'technologyComment':   tech_comment,
            'timePeriodComment':   tp_comment,
            'ISIC':                _join(_sg(cls, 'classificationValue', '')) if cls is not None else '',
            'dataEntryBy_isActiveAuthor':                str(_sg(deb, 'isActiveAuthor', '')),
            'dataEntryBy_personName':                    _join(_sg(deb, 'personName', '')),
            'dataGeneratorAndPublication_accessRestrictedTo': _ACCESS_MAP.get(_sg(dgp, 'accessRestrictedTo', ''), str(_sg(dgp, 'accessRestrictedTo', ''))),
            'dataGeneratorAndPublication_dataPublishedIn':    _PUBLISHED_MAP.get(_sg(dgp, 'dataPublishedIn', ''), str(_sg(dgp, 'dataPublishedIn', ''))),
            'dataGeneratorAndPublication_pageNumbers':        _join(_sg(dgp, 'pageNumbers', '')),
            'dataGeneratorAndPublication_personName':         _join(_sg(dgp, 'personName', '')),
            'dataGeneratorAndPublication_publishedSourceId':  _join(_sg(dgp, 'publishedSourceId', '')),
            'modellingAndValidation_extrapolations':    _join(_sg(rep, 'extrapolations', '')),
            'modellingAndValidation_percent':           (lambda v: '' if v == '' or (isinstance(v, float) and math.isnan(v)) else str(v))(_sg(rep, 'percent', '')),
            'modellingAndValidation_samplingProcedure': (lambda e: (e.text or '').strip() if e is not None else '')(rep.find('{%s}samplingProcedure' % _NS) if rep is not None else None),
            'fileAttributes_majorRelease':  str(_sg(fa, 'majorRelease', '')),
            'fileAttributes_minorRelease':  str(_sg(fa, 'minorRelease', '')),
            'fileAttributes_majorRevision': str(_sg(fa, 'majorRevision', '')),
            'fileAttributes_minorRevision': str(_sg(fa, 'minorRevision', '')),
        })

        # ── REVIEW ───────────────────────────────────────────────────────────
        rev_obj = _sg(activity.modellingAndValidation, 'review', None)
        if rev_obj is not None:
            ns = '{http://www.EcoInvent.org/EcoSpold02}'
            def _rev_text(tag):
                elem = rev_obj.find(f'{ns}{tag}')
                if elem is None:
                    return ''
                texts = elem.findall(f'{ns}text')
                return (_paragraphs(t.text for t in texts) if texts
                        else (elem.text or '').strip())
            review_rows.append({
                'activityName':          activity_name,
                'geography':             geography,
                'reviewerName':          rev_obj.get('reviewerName', ''),
                'reviewDate':            rev_obj.get('reviewDate', ''),
                'reviewedMajorRelease':  rev_obj.get('reviewedMajorRelease', ''),
                'reviewedMinorRelease':  rev_obj.get('reviewedMinorRelease', ''),
                'reviewedMajorRevision': rev_obj.get('reviewedMajorRevision', ''),
                'reviewedMinorRevision': rev_obj.get('reviewedMinorRevision', ''),
                'details':               _rev_text('details'),
                'otherDetails':          _rev_text('otherDetails'),
            })

        # ── INTERMEDIATE EXCHANGES ────────────────────────────────────────────
        for exc in _sg(activity.flowData, 'intermediateExchanges', []):
            group = _GROUP_MAP.get(_sg(exc, 'groupStr', ''), _sg(exc, 'groupStr', ''))
            uncs  = _sg(exc, 'uncertainties', [])
            u     = _unc_fields(uncs[0] if isinstance(uncs, list) and uncs else None)

            al_name = _text1(exc, 'activityLinkActivityNames')
            al_geo_obj = _sg(exc, 'activityLinkGeography', None)
            al_geo = _text1(al_geo_obj, 'shortNames') if al_geo_obj is not None else ''

            pv_uncs    = _sg(exc, 'productionVolumeUncertainties', [])
            pv_u       = _unc_fields(pv_uncs[0] if isinstance(pv_uncs, list) and pv_uncs else None)
            _pvc       = exc.find('{%s}productionVolumeComment' % _NS)
            pv_comment = (_pvc.text or '').strip() if _pvc is not None else ''

            exc_name = _text1(exc, 'names')
            exc_unit = _text1(exc, 'unitNames')
            exc_comment = _comment1(exc)
            pn_list = _sg(exc, 'pageNumbers', [])
            page_numbers = str(pn_list[0]) if isinstance(pn_list, list) and pn_list else ''

            exchange_rows.append({
                'activityName': activity_name, 'geography': geography,
                'group': group, 'name': exc_name,
                'compartment': '', 'subcompartment': '',
                'activityLink_activityName': al_name,
                'activityLink_geography':    al_geo,
                'unitName':               exc_unit,
                'amount':                 _sg(exc, 'amount', ''),
                'variableName':           _sg(exc, 'variableName', ''),
                'mathematicalRelation':   _sg(exc, 'mathematicalRelation', ''),
                'comment':                exc_comment,
                'pageNumbers':            page_numbers,
                'sourceId':               _sg(exc, 'sourceId', ''),
                **u,
                'PV_amount':               (lambda v: '' if isinstance(v, float) and math.isnan(v) else v)(_sg(exc, 'productionVolumeAmount', '')),  # nan when absent
                'PV_variableName':         _sg(exc, 'productionVolumeVariableName', ''),
                'PV_mathematicalRelation': _sg(exc, 'productionVolumeMathematicalRelation', ''),
                'PV_comment':              pv_comment,
                'PV_sourceId':             _sg(exc, 'productionVolumeSourceId', ''),
                'PV_uncertainty type':             pv_u['uncertainty type'],
                'PV_uncertainty comment':          pv_u['uncertainty comment'],
                'PV_variance':                     pv_u['variance'],
                'PV_minValue':                     pv_u['minValue'],
                'PV_mostLikelyValue':              pv_u['mostLikelyValue'],
                'PV_maxValue':                     pv_u['maxValue'],
                'PV_reliability':                  pv_u['reliability'],
                'PV_completeness':                 pv_u['completeness'],
                'PV_temporalCorrelation':          pv_u['temporalCorrelation'],
                'PV_geographicalCorrelation':      pv_u['geographicalCorrelation'],
                'PV_furtherTechnologyCorrelation': pv_u['furtherTechnologyCorrelation'],
            })

            # properties from intermediate exchanges
            for prop in _sg(exc, 'properties', []):
                prop_uncs = _sg(prop, 'uncertainties', [])
                pu = _unc_fields(prop_uncs[0] if isinstance(prop_uncs, list) and prop_uncs else None)
                property_rows.append({
                    'activityName': activity_name, 'geography': geography,
                    'group': group, 'name': exc_name,
                    'compartment': '', 'subcompartment': '',
                    'activityLink_activityName': al_name,
                    'activityLink_geography':    al_geo,
                    'propertyName': _text1(prop, 'names'),
                    'unitName':     _text1(prop, 'unitNames'),
                    'amount':       _sg(prop, 'amount', ''),
                    'variableName': _sg(prop, 'variableName', ''),
                    'mathematicalRelation': _sg(prop, 'mathematicalRelation', ''),
                    'comment':      _comment1(prop),
                    **pu,
                })

        # ── ELEMENTARY EXCHANGES ─────────────────────────────────────────────
        for exc in _sg(activity.flowData, 'elementaryExchanges', []):
            group = _GROUP_MAP.get(_sg(exc, 'groupStr', ''), _sg(exc, 'groupStr', ''))
            uncs  = _sg(exc, 'uncertainties', [])
            u     = _unc_fields(uncs[0] if isinstance(uncs, list) and uncs else None)

            comp_obj = _sg(exc, 'compartment', None)
            compartment = subcompartment = ''
            if comp_obj is not None:
                comps = _sg(comp_obj, 'compartments', [])
                compartment = str(comps[0]) if isinstance(comps, list) and comps else ''
                # pyecospold uses subCompartments (capital C) — try both
                subs = _sg(comp_obj, 'subCompartments', []) or _sg(comp_obj, 'subcompartments', [])
                subcompartment = str(subs[0]) if isinstance(subs, list) and subs else ''

            exc_name    = _text1(exc, 'names')
            exc_unit    = _text1(exc, 'unitNames')
            exc_comment = _comment1(exc)
            pn_list     = _sg(exc, 'pageNumbers', [])
            page_numbers = str(pn_list[0]) if isinstance(pn_list, list) and pn_list else ''

            exchange_rows.append({
                'activityName': activity_name, 'geography': geography,
                'group': group, 'name': exc_name,
                'compartment': compartment, 'subcompartment': subcompartment,
                'activityLink_activityName': '', 'activityLink_geography': '',
                'unitName':               exc_unit,
                'amount':                 _sg(exc, 'amount', ''),
                'variableName':           _sg(exc, 'variableName', ''),
                'mathematicalRelation':   _sg(exc, 'mathematicalRelation', ''),
                'comment':                exc_comment,
                'pageNumbers':            page_numbers,
                'sourceId':               _sg(exc, 'sourceId', ''),
                **u,
                'PV_amount': '', 'PV_variableName': '', 'PV_mathematicalRelation': '',
                'PV_comment': '', 'PV_sourceId': '',
                'PV_uncertainty type': '', 'PV_uncertainty comment': '',
                'PV_variance': '', 'PV_minValue': '', 'PV_mostLikelyValue': '', 'PV_maxValue': '',
                'PV_reliability': '', 'PV_completeness': '', 'PV_temporalCorrelation': '',
                'PV_geographicalCorrelation': '', 'PV_furtherTechnologyCorrelation': '',
            })

            # properties from elementary exchanges
            for prop in _sg(exc, 'properties', []):
                prop_uncs = _sg(prop, 'uncertainties', [])
                pu = _unc_fields(prop_uncs[0] if isinstance(prop_uncs, list) and prop_uncs else None)
                property_rows.append({
                    'activityName': activity_name, 'geography': geography,
                    'group': group, 'name': exc_name,
                    'compartment': compartment, 'subcompartment': subcompartment,
                    'activityLink_activityName': '', 'activityLink_geography': '',
                    'propertyName': _text1(prop, 'names'),
                    'unitName':     _text1(prop, 'unitNames'),
                    'amount':       _sg(prop, 'amount', ''),
                    'variableName': _sg(prop, 'variableName', ''),
                    'mathematicalRelation': _sg(prop, 'mathematicalRelation', ''),
                    'comment':      _comment1(prop),
                    **pu,
                })

        # ── PARAMETERS ───────────────────────────────────────────────────────
        for param in _sg(activity.flowData, 'parameters', []):
            param_uncs = _sg(param, 'uncertainties', [])
            pau = _unc_fields(param_uncs[0] if isinstance(param_uncs, list) and param_uncs else None)
            parameter_rows.append({
                'activityName':         activity_name,
                'geography':            geography,
                'name':                 _text1(param, 'names'),
                'unitName':             _text1(param, 'unitNames'),
                'amount':               _sg(param, 'amount', ''),
                'variableName':         _sg(param, 'variableName', ''),
                'mathematicalRelation': _sg(param, 'mathematicalRelation', ''),
                'comment':              _comment1(param),
                **pau,
            })

    def _to_df(rows, cols):
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        for col in cols:
            if col not in df.columns:
                df[col] = None
        return df[cols]

    return {
        'meta':       _to_df(meta_rows,     META_COLS),
        'review':     _to_df(review_rows,   REVIEW_COLS),
        'exchanges':  _to_df(exchange_rows, EXCHANGES_COLS),
        'properties': _to_df(property_rows, PROPERTIES_COLS),
        'parameters': _to_df(parameter_rows, PARAMETERS_COLS),
    }


def spold_to_excel(spold_file: str, output_excel: str) -> None:
    """Convert a .spold file to full DataX Excel format (.xlsx)."""
    print(f"  Reading SPOLD: {spold_file}")
    sheets = read_spold(spold_file)
    os.makedirs(os.path.dirname(output_excel) or '.', exist_ok=True)
    with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
        for sheet_name in ('meta', 'review', 'exchanges', 'properties', 'parameters'):
            sheets[sheet_name].to_excel(writer, sheet_name=sheet_name, index=False)
    print(f"  Done: {len(sheets['meta'])} activities, "
          f"{len(sheets['exchanges'])} exchanges, "
          f"{len(sheets['properties'])} properties -> {output_excel}")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: spold_reader_new.py <input.spold> <output.xlsx>")
        sys.exit(1)
    spold_to_excel(sys.argv[1], sys.argv[2])
