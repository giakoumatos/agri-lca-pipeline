"""
SPOLD Converter for New Datasets Pipeline

Converts the final pipeline output (Excel / DataX format) to EcoSpold2 (.spold)
using a built-in writer that produces correct EcoSpold2 XML:
  - amount is an XML attribute (not a child element)
  - group codes mapped: ReferenceProduct→outputGroup 0, Products→outputGroup 2,
    FromTechnosphere→inputGroup 5, ToEnvironment→inputGroup/outputGroup 4
  - output file named after input dataset (not UUID)
  - NaN values suppressed

PIPELINE POSITION:
    Runs on every save.  The meta model and the three calculators call
    write_spold_for() right after writing a dataset workbook, so each
    output/<project>/<stem>.xlsx always has its matching
    output/<project>/spold_output/<stem>*.spold.  Run on its own (--batch)
    it rewrites them from the workbooks as they stand.

Author: Andreas Giakoumatos
Last Updated: 2026-07-29
"""

import os
import sys
import json
import math
import uuid as _uuid_mod
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

# Warning messages echo arbitrary source data (names, compartments, etc.)
# verbatim -- some contain characters Windows' console codepage can't encode
# (e.g. U+2011 non-breaking hyphen), which crashed the whole conversion
# mid-file. Never let a print() abort real work; replace what it can't show.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')

# ── Config ────────────────────────────────────────────────────────────────────
# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
from config_new_datasets import (
    AGRI_DIESEL_OUTPUT_FILE as INPUT_FILEPATH,
    SPOLD_OUTPUT_DIR,
    OLCAPEST_INPUT_FILENAME,
    MASTER_DATA_LOOKUP_FILE,
    MASTER_DATA_CLASSIFICATIONS_FILE,
    GEOGRAPHY_ID_LOOKUP_FILE,
    ISIC_CLASSIFICATION_LOOKUP_FILE,
    STANDARD_INTERMEDIATE_EXCHANGE_LOOKUP_FILE,
    STANDARD_ELEMENTARY_EXCHANGE_LOOKUP_FILE,
    SUBCOMPARTMENT_ID_LOOKUP_FILE,
    PROPERTY_ID_LOOKUP_FILE,
    UNIT_ID_LOOKUP_FILE,
    PERSON_ID_LOOKUP_FILE,
)

if os.path.isfile(PERSON_ID_LOOKUP_FILE):
    with open(PERSON_ID_LOOKUP_FILE, encoding='utf-8') as _fh:
        _PERSON_ID_MAP = json.load(_fh)
else:
    _PERSON_ID_MAP = {}

if os.path.isfile(GEOGRAPHY_ID_LOOKUP_FILE):
    with open(GEOGRAPHY_ID_LOOKUP_FILE, encoding='utf-8') as _fh:
        _GEOGRAPHY_ID_MAP = json.load(_fh)
else:
    _GEOGRAPHY_ID_MAP = {}


def _normalize_isic(value: str) -> str:
    """Normalize an ISIC value for matching -- casing/spacing around the
    colon varies between sources (e.g. "0121: Growing of Grapes" vs
    "0121:Growing of grapes")."""
    code, _, label = value.partition(':')
    return f'{code.strip()}:{label.strip().lower()}'


if os.path.isfile(ISIC_CLASSIFICATION_LOOKUP_FILE):
    with open(ISIC_CLASSIFICATION_LOOKUP_FILE, encoding='utf-8') as _fh:
        _ISIC_ID_MAP = {_normalize_isic(k): v for k, v in json.load(_fh).items()}
else:
    _ISIC_ID_MAP = {}

# Real, standard ecoinvent systemModelId for "Undefined" (constant across the
# whole database -- confirmed present in real files, not project-specific).
_SYSTEM_MODEL_UNDEFINED_ID = '8b738ea0-f89e-4627-8679-433616064e82'

# ── Custom (non-ecoinvent) flow master data, for <usedUserMasterData> ────────
# Any flow whose intermediateExchangeId matches one of these is NOT in
# ecoinvent's own master database (it's specific to this project, e.g. the
# "diesel used in, ..." tracking flows), so ecoEditor requires the dataset to
# self-declare it here or it rejects the file with "uses non-ecoinvent master
# data". Keyed by intermediateExchangeId for direct lookup from an exchange row.
if os.path.isfile(MASTER_DATA_LOOKUP_FILE) and os.path.isfile(MASTER_DATA_CLASSIFICATIONS_FILE):
    with open(MASTER_DATA_LOOKUP_FILE, encoding='utf-8') as _fh:
        _MASTER_DATA_BY_NAME = json.load(_fh)
    with open(MASTER_DATA_CLASSIFICATIONS_FILE, encoding='utf-8') as _fh:
        _MASTER_DATA_CLASSIFICATIONS_BY_NAME = json.load(_fh)
    _CUSTOM_FLOWS_BY_IEX_ID = {
        entry['intermediateExchangeId']: {
            'unitId': entry['unitId'],
            'unitName': entry.get('unitName', ''),
            'classification': _MASTER_DATA_CLASSIFICATIONS_BY_NAME[name],
        }
        for name, entry in _MASTER_DATA_BY_NAME.items()
        if name in _MASTER_DATA_CLASSIFICATIONS_BY_NAME
    }
else:
    _MASTER_DATA_BY_NAME = {}
    _MASTER_DATA_CLASSIFICATIONS_BY_NAME = {}
    _CUSTOM_FLOWS_BY_IEX_ID = {}

# The same custom-flow table keyed by NAME, lowercased.  A workbook that does
# not carry an intermediateExchangeId column at all -- a hand-built or
# hand-edited one -- would otherwise ship our own diesel and multinutrient
# flows with self-generated ids, even though their real ids are right here.
_CUSTOM_FLOWS_BY_NAME = {str(name).strip().lower(): entry
                         for name, entry in (_MASTER_DATA_BY_NAME or {}).items()}

# ── Inconsistency report ──────────────────────────────────────────────────────
# Nothing is ever dropped from the output for failing to match real master
# data -- everything ships (with a self-generated fallback id where needed),
# and every such case is logged here for review in a single Excel report
# rather than silently accepted or silently discarded.
_INCONSISTENCY_LOG: list = []

# ── Self-declared master data for fallback ids ───────────────────────────────
# Any exchange that fell through to a self-generated id (not found in real
# master data, not a pre-declared custom flow) MUST still be declared in
# usedUserMasterData, or ecoEditor rejects the file with "id ... is unknown"
# for that id -- exactly like the activity id / custom flow cases. Cleared
# and repopulated per activity by _build_spold / _exchange_xml.
_FALLBACK_FLOWS: dict = {}


def _log_inconsistency(kind: str, activity_name: str, geography: str, name: str = '',
                        compartment: str = '', subcompartment: str = '',
                        fallback_id: str = '', note: str = '') -> None:
    _INCONSISTENCY_LOG.append({
        'type': kind,
        'activityName': activity_name,
        'geography': geography,
        'name': name,
        'compartment': compartment,
        'subcompartment': subcompartment,
        'fallback_id_used': fallback_id,
        'note': note,
    })


def save_inconsistency_report(output_dir: str) -> str | None:
    """Write every logged inconsistency to one Excel file, MERGED with
    whatever is already there -- a later single-file run must never destroy
    a prior full-batch report. Returns the path, or None if nothing was
    logged and no report already existed."""
    path = os.path.join(output_dir, 'master_data_inconsistencies.xlsx')
    new_df = pd.DataFrame(_INCONSISTENCY_LOG)
    if os.path.isfile(path):
        try:
            existing_df = pd.read_excel(path)
        except Exception:
            existing_df = pd.DataFrame()
        combined = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        combined = new_df
    if combined.empty:
        return None
    key_cols = [c for c in ['type', 'activityName', 'name', 'compartment', 'subcompartment']
                if c in combined.columns]
    if key_cols:
        combined = combined.drop_duplicates(subset=key_cols, keep='last')
    combined.to_excel(path, index=False)
    print(f"\n[Inconsistency report] {len(combined)} row(s) total -> {path}")
    return path

# ── Real master-data ids for STANDARD (non-custom) exchanges ────────────────
# Every fertiliser/pesticide input, market activity, emission, etc. that
# ISN'T one of our custom diesel/multinutrient flows still needs its REAL
# ecoinvent intermediateExchangeId/elementaryExchangeId -- self-generating
# one for a real, existing ecoinvent product is exactly the same "unknown id"
# bug as the activity id / geographyId / classificationId cases, just at much
# larger scale. Confirmed 100% consistent in the master database: intermediate
# by name alone (4347 names, 0 conflicts); elementary needs name+compartment+
# subcompartment (3294 keys, 0 conflicts -- name alone conflicts 616/1189
# times, since e.g. "Carbon dioxide" differs by compartment).
if os.path.isfile(STANDARD_INTERMEDIATE_EXCHANGE_LOOKUP_FILE):
    with open(STANDARD_INTERMEDIATE_EXCHANGE_LOOKUP_FILE, encoding='utf-8') as _fh:
        _STANDARD_INTERMEDIATE_ID_MAP = json.load(_fh)
else:
    _STANDARD_INTERMEDIATE_ID_MAP = {}

if os.path.isfile(STANDARD_ELEMENTARY_EXCHANGE_LOOKUP_FILE):
    with open(STANDARD_ELEMENTARY_EXCHANGE_LOOKUP_FILE, encoding='utf-8') as _fh:
        _STANDARD_ELEMENTARY_ID_MAP = json.load(_fh)
else:
    _STANDARD_ELEMENTARY_ID_MAP = {}

if os.path.isfile(SUBCOMPARTMENT_ID_LOOKUP_FILE):
    with open(SUBCOMPARTMENT_ID_LOOKUP_FILE, encoding='utf-8') as _fh:
        _SUBCOMPARTMENT_ID_MAP = json.load(_fh)
else:
    _SUBCOMPARTMENT_ID_MAP = {}


# ── Helpers ──────────────────────────────────────────────────────────────────

# ── ecoEditor / EcoSpold2 required UUIDs ─────────────────────────────────────
# Standard ecoinvent nomenclature context UUID (same in every ecoinvent file)
_ECOINVENT_CONTEXT_ID = 'de659012-50c4-4e96-b54a-fc781bf987ab'

# EcoSpold2 internal schema version used by real ecoEditor exports (constant,
# confirmed in real corpus files -- not project-specific).
_INTERNAL_SCHEMA_VERSION = '2.0.12'

# Real ecoinvent unit and property ids, from the official
# MasterData (3.12, undef).xlsx workbook -- the single authoritative source
# for every id in this file.
if os.path.isfile(UNIT_ID_LOOKUP_FILE):
    with open(UNIT_ID_LOOKUP_FILE, encoding='utf-8') as _fh:
        _UNIT_ID_MAP = json.load(_fh)
else:
    _UNIT_ID_MAP = {}

if os.path.isfile(PROPERTY_ID_LOOKUP_FILE):
    with open(PROPERTY_ID_LOOKUP_FILE, encoding='utf-8') as _fh:
        _PROPERTY_ID_MAP = json.load(_fh)
else:
    _PROPERTY_ID_MAP = {}

_UUID_NS = _uuid_mod.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')  # standard URL namespace

def _gen_uuid(seed: str) -> str:
    """Deterministic UUID5 from seed text."""
    return str(_uuid_mod.uuid5(_UUID_NS, seed))

def _unit_id(unit_name: str, act_name: str = '', geography: str = '') -> str:
    """Return the real ecoinvent unit id, or a logged fallback for an unknown unit."""
    uid = _UNIT_ID_MAP.get(unit_name.strip())
    if uid:
        return uid
    uid = _gen_uuid('unit:' + unit_name.strip())
    print(f"   WARNING: unit '{unit_name}' not in the known unit lookup -- "
          f"self-generating an id (logged).")
    _log_inconsistency('unit', act_name, geography, name=unit_name,
                        fallback_id=uid, note='not in known unit lookup')
    return uid

def _safe(val, default: str = '') -> str:
    """Return val as a string, converting NaN/None to default."""
    if val is None:
        return default
    try:
        if math.isnan(float(val)):
            return default
    except (ValueError, TypeError):
        pass
    s = str(val).strip()
    return default if s.lower() == 'nan' else s


def _esc(text: str) -> str:
    """Escape XML special characters."""
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


def _text_paragraphs(text: str, indent: str) -> str:
    """A rich-text comment as EcoSpold2 paragraphs: one <text index="n"> per
    line of the source cell, so a long comment written in paragraphs in the
    workbook reads as paragraphs in the editor.  Blank lines are dropped."""
    paras = [p.strip() for p in str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    paras = [p for p in paras if p] or [str(text).strip()]
    return ''.join(f'{indent}<text xml:lang="en" index="{i}">{_esc(p)}</text>\n'
                   for i, p in enumerate(paras, start=1))


def _flag_group_conflict(row, group: str, problem: str) -> None:
    """Log an exchange whose group and compartment contradict each other."""
    _log_inconsistency('group_conflict', _safe(row.get('activityName')),
                       _safe(row.get('geography')), name=_safe(row.get('name')),
                       compartment=_safe(row.get('compartment')),
                       subcompartment=_safe(row.get('subcompartment')),
                       note=f'group {group}: {problem}')
    print(f"   WARNING: exchange {_safe(row.get('name'))!r} ({group}) {problem} "
          f"(logged).")


def _group_tag(row) -> tuple:
    """
    Return (xml_tag, group_number, is_output) for an exchange row.

    DataX group values → EcoSpold2:
        ReferenceProduct  → intermediateExchange, outputGroup 0
        Products          → intermediateExchange, outputGroup 2
        FromTechnosphere  → intermediateExchange, inputGroup  5
        FromEnvironment   → elementaryExchange,   inputGroup  4  (natural resources)
        ToEnvironment     → elementaryExchange,
                            inputGroup 4  (if compartment is 'natural resource')
                            outputGroup 4 (all other emissions)
    """
    group = _safe(row.get('group')).strip()
    if group == 'ReferenceProduct':
        return 'intermediateExchange', 0, True
    elif group in ('Products', 'ByProduct'):
        return 'intermediateExchange', 2, True
    elif group == 'FromTechnosphere':
        # Written as the group says, but a compartment means the row is an
        # elementary flow under the wrong group ('Water, well, in ground'),
        # and the compartment is lost on the way -- say so.
        if _safe(row.get('compartment')).strip():
            _flag_group_conflict(row, group, 'has a compartment, so it reads as an '
                                 'elementary flow; written as a technosphere input '
                                 'and the compartment dropped')
        return 'intermediateExchange', 5, False
    elif group == 'FromEnvironment':
        # Natural resource inputs — elementaryExchange inputGroup 4
        if not _safe(row.get('compartment')).strip():
            _flag_group_conflict(row, group, 'has no compartment, so it reads as a '
                                 'technosphere input; written as an elementary flow '
                                 'anyway')
        return 'elementaryExchange', 4, False
    elif group == 'ToEnvironment':
        comp = _safe(row.get('compartment')).lower()
        is_resource = 'natural resource' in comp or comp in ('resource', 'resources')
        return 'elementaryExchange', 4, not is_resource
    # A blank or unknown group used to ship every such row as a purchased
    # technosphere input -- an Abamectine emission to ground water among them.
    # The compartment says what it is: only elementary flows carry one.
    comp = _safe(row.get('compartment')).strip().lower()
    if comp:
        is_resource = 'natural resource' in comp or comp in ('resource', 'resources')
        guess = 'FromEnvironment' if is_resource else 'ToEnvironment'
        result = ('elementaryExchange', 4, not is_resource)
    else:
        guess = 'FromTechnosphere'
        result = ('intermediateExchange', 5, False)
    _log_inconsistency('group_inferred', _safe(row.get('activityName')),
                       _safe(row.get('geography')), name=_safe(row.get('name')),
                       compartment=_safe(row.get('compartment')),
                       subcompartment=_safe(row.get('subcompartment')),
                       note=f'group {group!r} is not a DataX group; written as '
                            f'{guess} from its compartment')
    print(f"   WARNING: exchange {_safe(row.get('name'))!r} has group {group!r} "
          f"-- written as {guess} (logged).")
    return result


# DQG (Weidema et al.) Table 10.5: pedigree score 1..5 -> variance factor.
_PEDIGREE_FACTORS = {
    'reliability':                  (0.0, 0.0006, 0.002, 0.008, 0.04),
    'completeness':                 (0.0, 0.0001, 0.0006, 0.002, 0.008),
    'temporalCorrelation':          (0.0, 0.0002, 0.002, 0.008, 0.04),
    'geographicalCorrelation':      (0.0, 2.5e-5, 0.0001, 0.0006, 0.002),
    'furtherTechnologyCorrelation': (0.0, 0.0006, 0.008, 0.04, 0.12),
}


def _variance_with_pedigree(variance: str, row, col_prefix: str = '') -> str:
    """Basic variance + the Table 10.5 factor of each pedigree score present.
    A score that is missing or outside 1..5 adds nothing."""
    try:
        total = float(variance)
    except (ValueError, TypeError):
        return variance
    for indicator, factors in _PEDIGREE_FACTORS.items():
        try:
            score = int(float(_safe(row.get(col_prefix + indicator))))
        except (ValueError, TypeError):
            continue
        if 1 <= score <= 5:
            total += factors[score - 1]
    return f'{total:.10g}' if total else variance


def _uncertainty_xml(row, indent: str = '        ', col_prefix: str = '', elem_name: str = 'uncertainty') -> str:
    """Build <uncertainty> or <productionVolumeUncertainty> block from DataX columns."""
    ut = _safe(row.get(col_prefix + 'uncertainty type'))
    if not ut:
        return ''

    i2 = indent + '  '

    mean     = _safe(row.get(col_prefix + 'amount' if col_prefix else 'amount'))
    variance = _safe(row.get(col_prefix + 'variance'))
    minv     = _safe(row.get(col_prefix + 'minValue'))
    maxv     = _safe(row.get(col_prefix + 'maxValue'))
    mlv      = _safe(row.get(col_prefix + 'mostLikelyValue'))

    dist_attrs = ''
    if ut == 'lognormal':
        # mu (log-space mean) is required by the schema; derived from amount
        try:
            mu = str(round(math.log(float(mean)), 6)) if mean and float(mean) > 0 else '0'
        except (ValueError, TypeError):
            mu = '0'
        dist_attrs += f' meanValue="{_esc(mean)}"'
        dist_attrs += f' mu="{mu}"'
        if variance:
            # DataX 'variance' is the BASIC uncertainty (V1², DQG Table 10.3).
            # The total adds the five pedigree factors (Table 10.5).
            dist_attrs += f' variance="{_esc(variance)}"'
            dist_attrs += (f' varianceWithPedigreeUncertainty='
                           f'"{_esc(_variance_with_pedigree(variance, row, col_prefix))}"')
    elif ut == 'normal':
        if mean:     dist_attrs += f' meanValue="{_esc(mean)}"'
        if variance:
            dist_attrs += f' variance="{_esc(variance)}"'
            dist_attrs += f' varianceWithPedigreeUncertainty="{_esc(variance)}"'
    else:
        if mean: dist_attrs += f' meanValue="{_esc(mean)}"'
        if minv: dist_attrs += f' minValue="{_esc(minv)}"'
        if mlv:  dist_attrs += f' mostLikelyValue="{_esc(mlv)}"'
        if maxv: dist_attrs += f' maxValue="{_esc(maxv)}"'

    lines = [f'{indent}<{elem_name}>\n', f'{i2}<{ut}{dist_attrs} />\n']

    pm_pairs = [
        (col_prefix + 'reliability',                  'reliability'),
        (col_prefix + 'completeness',                 'completeness'),
        (col_prefix + 'temporalCorrelation',          'temporalCorrelation'),
        (col_prefix + 'geographicalCorrelation',      'geographicalCorrelation'),
        (col_prefix + 'furtherTechnologyCorrelation', 'furtherTechnologyCorrelation'),
    ]
    def _int_score(v: str) -> str:
        try:
            return str(int(float(v)))
        except (ValueError, TypeError):
            return v
    pm_attrs = ''.join(
        f' {attr}="{_int_score(_safe(row.get(col)))}"'
        for col, attr in pm_pairs
        if _safe(row.get(col))
    )
    if pm_attrs:
        lines.append(f'{i2}<pedigreeMatrix{pm_attrs} />\n')

    unc_comment = _safe(row.get(col_prefix + 'uncertainty comment'))
    if unc_comment:
        lines.append(f'{i2}<comment xml:lang="en">{_esc(unc_comment)}</comment>\n')

    lines.append(f'{indent}</{elem_name}>\n')
    return ''.join(lines)


def _exchange_xml(row, act_name: str = '', prop_rows=None) -> str:
    """Serialize one exchange row to EcoSpold2 XML."""
    tag, grp_num, is_output = _group_tag(row)
    amount    = _safe(row.get('amount'), '0')
    name_raw  = _safe(row.get('name', ''))
    unit_raw  = _safe(row.get('unitName', ''))
    name      = _esc(name_raw)
    unit      = _esc(unit_raw)
    comment   = _safe(row.get('comment'))
    comp_raw  = _safe(row.get('compartment'))
    sub_raw   = _safe(row.get('subcompartment'))
    var_name  = _safe(row.get('variableName', ''))
    math_rel  = _safe(row.get('mathematicalRelation', ''))
    source_id = _safe(row.get('sourceId', ''))
    page_num  = _safe(row.get('pageNumbers', ''))
    al_name   = _safe(row.get('activityLink_activityName', ''))
    al_geo    = _safe(row.get('activityLink_geography', ''))

    # Compartment/subcompartment defaults computed up front (not just where
    # the <compartment> block is written) so the elementaryExchangeId lookup
    # below keys on the SAME values that end up in the file.
    comp_for_lookup, sub_for_lookup = comp_raw, sub_raw
    if tag == 'elementaryExchange' and (not comp_raw or not sub_raw):
        comp_for_lookup = comp_raw or 'unspecified'
        sub_for_lookup  = sub_raw or 'unspecified'

    # Required ecoEditor attributes. Priority for the flow id: (1) a real
    # master-data id already on the row (diesel/multinutrient custom flows),
    # (2) the real ecoinvent id for this STANDARD exchange looked up by name
    # (elementary: name+compartment+subcompartment), (3) only as a last
    # resort a deterministic hash. Nothing is ever dropped -- a fallback case
    # ships with a self-generated id AND gets logged for the inconsistency
    # report, so the real fix (correct the source data, or add real/declared
    # master data) can be tracked and applied without losing data meanwhile.
    # Compartment/subcompartment MUST be part of this seed: the same
    # substance name (e.g. "Fluopicolide") legitimately appears once per
    # compartment as genuinely distinct exchanges -- omitting them here
    # collapsed all of them onto one shared id, which ecoEditor detects as
    # a duplicate and "fixes" by renaming variables (the actual root cause
    # of the "Fixed Duplicate Variable Names" dialog, not the blank
    # variableName itself).
    ex_id     = _gen_uuid(f'ex:{act_name}:{name_raw}:{unit_raw}:{grp_num}:{comp_for_lookup}:{sub_for_lookup}')
    real_uid  = _safe(row.get('unitId', ''))
    uid       = real_uid if real_uid else _unit_id(unit_raw, act_name, _safe(row.get('geography', '')))
    real_iex  = _safe(row.get('intermediateExchangeId', ''))
    geography = _safe(row.get('geography', ''))
    if tag == 'intermediateExchange':
        std_iex = _STANDARD_INTERMEDIATE_ID_MAP.get(name_raw)
        # Our own declared custom flows, by name: the id belongs to the flow,
        # not to the workbook, so a row that arrives without one still gets
        # the real id rather than a hash.
        # A provider's own diesel row may carry the older spelling
        # "diesel used in, <operation>"; the row keeps its name, and the flow
        # is found with the comma set aside.
        custom = (_CUSTOM_FLOWS_BY_NAME.get(str(name_raw).strip().lower())
                  or _CUSTOM_FLOWS_BY_NAME.get(str(name_raw).strip().lower()
                                               .replace('diesel used in, ', 'diesel used in ', 1)))
        if real_iex:
            iex_val = real_iex
        elif std_iex:
            iex_val = std_iex
        elif custom and custom.get('intermediateExchangeId'):
            iex_val = custom['intermediateExchangeId']
            if not real_uid and custom.get('unitId'):
                uid = custom['unitId']
        else:
            iex_val = _gen_uuid("iflow:" + name_raw + ":" + unit_raw)
            print(f"   WARNING: intermediate exchange '{name_raw}' (activity: "
                  f"{act_name}) not in the standard master-data lookup and not "
                  f"a declared custom flow -- self-generating an id (logged).")
            _log_inconsistency('intermediate_exchange', act_name, geography,
                                name=name_raw, fallback_id=iex_val,
                                note='not in standard master data or declared custom flows')
            _FALLBACK_FLOWS[iex_val] = {
                'kind': 'intermediate', 'name': name_raw, 'unitId': uid, 'unitName': unit_raw,
            }
        flow_id_attr = f'intermediateExchangeId="{iex_val}"'
    else:
        elem_key = f'{name_raw}|{comp_for_lookup}|{sub_for_lookup}'
        std_eex = _STANDARD_ELEMENTARY_ID_MAP.get(elem_key)
        if std_eex:
            eex_val = std_eex
        else:
            eex_val = _gen_uuid("eflow:" + name_raw + ":" + comp_for_lookup + ":" + sub_for_lookup)
            print(f"   WARNING: elementary exchange '{name_raw}' (activity: "
                  f"{act_name}, compartment='{comp_for_lookup}', "
                  f"subcompartment='{sub_for_lookup}') not in the standard "
                  f"master-data lookup -- self-generating an id (logged).")
            _log_inconsistency('elementary_exchange', act_name, geography,
                                name=name_raw, compartment=comp_for_lookup,
                                subcompartment=sub_for_lookup, fallback_id=eex_val,
                                note='not in standard master data')
            _sub_id_for_decl = (_SUBCOMPARTMENT_ID_MAP.get(f'{comp_for_lookup}|{sub_for_lookup}')
                                 or _gen_uuid('subcompartment:' + comp_for_lookup + ':' + sub_for_lookup))
            _FALLBACK_FLOWS[eex_val] = {
                'kind': 'elementary', 'name': name_raw, 'unitId': uid, 'unitName': unit_raw,
                'compartment': comp_for_lookup, 'subcompartment': sub_for_lookup,
                'subcompartmentId': _sub_id_for_decl,
            }
        flow_id_attr = f'elementaryExchangeId="{eex_val}"'

    ex_attrs = f'id="{ex_id}" unitId="{uid}" amount="{amount}" {flow_id_attr}'
    if var_name:  ex_attrs += f' variableName="{_esc(var_name)}"'
    if math_rel:  ex_attrs += f' mathematicalRelation="{_esc(math_rel)}"'
    if source_id: ex_attrs += f' sourceId="{_esc(source_id)}"'
    if page_num:  ex_attrs += f' pageNumbers="{_esc(page_num)}"'
    if tag == 'intermediateExchange' and al_name:
        ex_attrs += f' activityLinkId="{_gen_uuid("activityLink:" + al_name + ":" + al_geo)}"'

    # productionVolume attributes (intermediate exchanges only)
    if tag == 'intermediateExchange':
        pv_amount = _safe(row.get('PV_amount', ''))
        pv_var    = _safe(row.get('PV_variableName', ''))
        pv_mr     = _safe(row.get('PV_mathematicalRelation', ''))
        pv_src    = _safe(row.get('PV_sourceId', ''))
        pv_comm   = _safe(row.get('PV_comment', ''))
        if pv_amount: ex_attrs += f' productionVolumeAmount="{_esc(pv_amount)}"'
        if pv_var:    ex_attrs += f' productionVolumeVariableName="{_esc(pv_var)}"'
        if pv_mr:     ex_attrs += f' productionVolumeMathematicalRelation="{_esc(pv_mr)}"'
        if pv_src:    ex_attrs += f' productionVolumeSourceId="{_esc(pv_src)}"'
    else:
        pv_comm = ''

    xml  = f'      <{tag} {ex_attrs}>\n'
    xml += f'        <name xml:lang="en">{name}</name>\n'
    xml += f'        <unitName xml:lang="en">{unit}</unitName>\n'

    if comment:
        xml += f'        <comment xml:lang="en">{_esc(comment)}</comment>\n'

    # EcoSpold2 element order: uncertainty → property → productionVolumeUncertainty → productionVolumeComment → group
    xml += _uncertainty_xml(row)

    # Property child elements
    if prop_rows is not None and not prop_rows.empty:
        for _, pr in prop_rows.iterrows():
            xml += _property_xml(pr)

    if tag == 'intermediateExchange':
        xml += _uncertainty_xml(row, col_prefix='PV_', elem_name='productionVolumeUncertainty')

    if pv_comm:
        xml += f'        <productionVolumeComment xml:lang="en">{_esc(pv_comm)}</productionVolumeComment>\n'

    # Compartment block — required by schema for every elementary exchange.
    # comp_for_lookup/sub_for_lookup (computed above, already defaulted to
    # 'unspecified' when the source data omits either) keep this consistent
    # with the elementaryExchangeId lookup that already used the same values.
    if tag == 'elementaryExchange':
        comp, sub = comp_for_lookup, sub_for_lookup
        if not comp_raw or not sub_raw:
            print(f"   WARNING: '{name_raw}' (activity: {act_name}) is missing "
                  f"compartment and/or subcompartment -- defaulting to "
                  f"'unspecified' (logged). Fix the source data's compartment column.")
            _log_inconsistency('compartment_missing', act_name, geography,
                                name=name_raw, compartment=comp, subcompartment=sub,
                                note='compartment and/or subcompartment blank in source data')
        sub_id = _SUBCOMPARTMENT_ID_MAP.get(f'{comp}|{sub}')
        if not sub_id:
            sub_id = _gen_uuid('subcompartment:' + comp + ':' + sub)
            print(f"   WARNING: compartment/subcompartment ('{comp}', '{sub}') for "
                  f"'{name_raw}' (activity: {act_name}) not in the standard "
                  f"master-data lookup -- self-generating an id (logged).")
            _log_inconsistency('subcompartment', act_name, geography,
                                name=name_raw, compartment=comp, subcompartment=sub,
                                fallback_id=sub_id, note='not in standard master data')
        xml += f'        <compartment subcompartmentId="{sub_id}">\n'
        xml += f'          <compartment xml:lang="en">{_esc(comp)}</compartment>\n'
        xml += f'          <subcompartment xml:lang="en">{_esc(sub)}</subcompartment>\n'
        xml += '        </compartment>\n'

    grp_elem = 'outputGroup' if is_output else 'inputGroup'
    xml += f'        <{grp_elem}>{grp_num}</{grp_elem}>\n'
    xml += f'      </{tag}>\n'
    return xml


# ── EcoSpold2 controlled-vocabulary maps ─────────────────────────────────────
# specialActivityType: text description → numeric code
_SAT_MAP = {
    'ordinary transforming activity': '0',
    'market activity':                '1',
    'io activity':                    '2',
    'residual activity mix':          '3',
    'production mix':                 '4',
    'import activity':                '5',
    'supply mix':                     '6',
    'export activity':                '7',
    're-export activity':             '8',
    'correction activity':            '9',
    'market group':                   '10',
}
# technologyLevel: text description → numeric code
_TL_MAP = {
    'undefined': '0',
    'new':       '1',
    'modern':    '2',
    'current':   '3',
    'old':       '4',
    'outdated':  '5',
}

# specialActivityType numeric code → EcoSpold2 <activity type> attribute
# type=1 = ordinary transforming/production; type=2 = market activity
_ACT_TYPE_MAP = {
    '0': '1',   # ordinary transforming activity
    '1': '2',   # market activity
    '4': '2',   # production mix
    '5': '2',   # import activity
    '6': '2',   # supply mix
    '7': '2',   # export activity
    '8': '2',   # re-export activity
    '10': '2',  # market group
}

# text → numeric reverse maps (reader returns text, XML needs numeric)
_ACCESS_CODE = {'public': '0', 'licensees': '1', 'results only': '2', 'individuals': '3'}
_PUBLISHED_CODE = {
    'data has been published entirely in':                                    '0',
    'the data of some unit processes or subsystems are published':             '1',
    'data as such not published elsewhere':                                    '2',
    'data published in different form':                                        '3',
}


def _property_xml(pr) -> str:
    """Serialize one property row to an EcoSpold2 <property> element."""
    pname   = _safe(pr.get('propertyName', ''))
    punit   = _safe(pr.get('unitName', ''))
    pamount = _safe(pr.get('amount', ''))
    pmr     = _safe(pr.get('mathematicalRelation', ''))
    pvar    = _safe(pr.get('variableName', ''))
    pcomm   = _safe(pr.get('comment', ''))
    pact    = _safe(pr.get('activityName', ''))
    pgeo    = _safe(pr.get('geography', ''))
    # A blank value is unknown, not zero: writing amount="0" would ship a
    # dry mass or a carbon content of nothing.  Without a formula to compute
    # it from, the property is left out and reported.
    if not pamount:
        if not pmr:
            pexch = _safe(pr.get('name', ''))
            print(f"   WARNING: property '{pname}' of '{pexch}' has no value "
                  f"-- not written (logged).")
            _log_inconsistency('property_blank', pact, pgeo, name=pexch,
                               note=f"property '{pname}' has no amount and no "
                                    f"formula; omitted")
            return ''
        pamount = '0'      # placeholder; the formula sets the value
    std_prop = _PROPERTY_ID_MAP.get(pname)
    if std_prop:
        prop_id = std_prop['propertyId']
        uid = std_prop['unitId'] or _unit_id(punit, pact, pgeo)
    else:
        prop_id = _gen_uuid('prop:' + pname + ':' + punit)
        uid = _unit_id(punit, pact, pgeo)
        print(f"   WARNING: property '{pname}' not in the known property "
              f"lookup -- self-generating an id (logged).")
        _log_inconsistency('property', pact, pgeo, name=pname,
                            fallback_id=prop_id, note='not in known property lookup')
    attrs   = f' propertyId="{prop_id}" unitId="{uid}" amount="{_esc(pamount)}"'
    if pmr:  attrs += f' mathematicalRelation="{_esc(pmr)}"'
    if pvar: attrs += f' variableName="{_esc(pvar)}"'
    xml  = f'        <property{attrs}>\n'
    xml += f'          <name xml:lang="en">{_esc(pname)}</name>\n'
    xml += f'          <unitName xml:lang="en">{_esc(punit)}</unitName>\n'
    # TProperty is a sequence: name, unitName, uncertainty, comment.  A
    # comment written before the uncertainty makes the whole file invalid,
    # and ecoEditor rejects it.
    xml += _uncertainty_xml(pr, indent='          ')
    if pcomm:
        xml += f'          <comment xml:lang="en">{_esc(pcomm)}</comment>\n'
    xml += '        </property>\n'
    return xml


def _build_spold(meta, exchanges: pd.DataFrame, review_df: pd.DataFrame,
                 properties_df: pd.DataFrame = None) -> str:
    """Produce a complete EcoSpold2 XML string for one activity.

    Structure matches the EcoSpold2 standard (and the reference orange spold):
      - <geography>, <timePeriod>, <technology> are siblings of <activity>
        inside <activityDescription>
      - <activity specialActivityType="0" type="1"> (both attributes present)
      - <generalComment><text xml:lang="en" index="1">...</text>...</generalComment>
        (one <text> paragraph per line of the source comment)
      - <timePeriod startDate="..." endDate="..."> (dates as XML attributes)
      - <administrativeInformation> section included
    """
    _FALLBACK_FLOWS.clear()
    act_name    = _safe(meta.get('activityName'))
    geography   = _safe(meta.get('geography'))
    gen_comment = _safe(meta.get('generalComment'))
    act_id      = _safe(meta.get('activityId'))   # UUID, present in real ecoinvent datasets

    # Normalise specialActivityType → always write numeric code
    spe_type_raw = _safe(meta.get('specialActivityType'), '0')
    spe_type     = _SAT_MAP.get(spe_type_raw.strip().lower(), spe_type_raw)

    # Normalise technologyLevel → always write numeric code
    tech_level_raw = _safe(meta.get('technologyLevel'), '3')
    tech_level     = _TL_MAP.get(tech_level_raw.strip().lower(), tech_level_raw)

    # Optional rich-text fields present in xlsx-based datasets
    incl_start     = _safe(meta.get('includedActivitiesStart'))
    incl_end       = _safe(meta.get('includedActivitiesEnd'))
    geo_comment    = _safe(meta.get('geographyComment'))
    tech_comment   = _safe(meta.get('technologyComment'))
    tp_comment     = _safe(meta.get('timePeriodComment'))
    isic           = _safe(meta.get('ISIC'))
    extrapolations = _safe(meta.get('modellingAndValidation_extrapolations'))

    # Strip time component: Excel stores dates as 'YYYY-MM-DD HH:MM:SS'
    def _date_only(d: str) -> str:
        return d.split(' ')[0].split('T')[0] if d else ''
    start_date = _date_only(_safe(meta.get('startDate')))
    end_date   = _date_only(_safe(meta.get('endDate')))
    # Both dates are required: an empty one makes the file invalid EcoSpold2
    # and ecoEditor refuses it.  Not invented here -- the period is the data
    # provider's -- but said out loud instead of shipped silently.
    for _label, _value in (('startDate', start_date), ('endDate', end_date)):
        if not _value:
            _log_inconsistency('missing_date', _safe(meta.get('activityName')),
                               _safe(meta.get('geography')), name=_label,
                               note=f'meta {_label} is blank; the .spold is not '
                                    f'valid EcoSpold2 until it is filled in')
            print(f"   WARNING: meta {_label} is blank -- the .spold will not "
                  f"import until it is filled in (logged).")

    xml  = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<ecoSpold xmlns="http://www.EcoInvent.org/EcoSpold02">\n'
    xml += '  <activityDataset>\n'
    xml += '    <activityDescription>\n'

    # <activity> — all attributes required by ecoEditor / EcoSpold2 schema
    act_type     = _ACT_TYPE_MAP.get(spe_type, '1')
    act_id_is_new  = not act_id
    act_id_val     = act_id if act_id else _gen_uuid('activity:' + act_name)
    act_name_id_raw = _safe(meta.get('activityNameId'))
    act_name_id_is_new = not act_name_id_raw
    act_name_id    = act_name_id_raw or _gen_uuid('activityName:' + act_name)
    xml += (
        f'      <activity id="{_esc(act_id_val)}"'
        f' activityNameId="{_esc(act_name_id)}"'
        f' activityNameContextId="{_ECOINVENT_CONTEXT_ID}"'
        f' inheritanceDepth="0"'
        f' type="{act_type}"'
        f' specialActivityType="{_esc(spe_type)}">\n'
    )
    xml += f'        <activityName xml:lang="en">{_esc(act_name)}</activityName>\n'
    synonyms = _safe(meta.get('synonyms'))
    if synonyms:
        xml += f'        <synonym xml:lang="en">{_esc(synonyms)}</synonym>\n'
    tags = _safe(meta.get('tags'))
    if tags:
        xml += f'        <tag>{_esc(tags)}</tag>\n'
    if incl_start:
        xml += f'        <includedActivitiesStart xml:lang="en">{_esc(incl_start)}</includedActivitiesStart>\n'
    if incl_end:
        xml += f'        <includedActivitiesEnd xml:lang="en">{_esc(incl_end)}</includedActivitiesEnd>\n'
    alloc_comment = _safe(meta.get('allocationComment'))
    if alloc_comment:
        xml += '        <allocationComment>\n'
        xml += _text_paragraphs(alloc_comment, '          ')
        xml += '        </allocationComment>\n'
    if gen_comment:
        xml += '        <generalComment>\n'
        xml += _text_paragraphs(gen_comment, '          ')
        xml += '        </generalComment>\n'
    xml += '      </activity>\n'

    # <classification> from ISIC column -- use the real, already-recognized
    # classificationId when known; self-generate (and print a warning, since
    # this would then also need declaring in usedUserMasterData) only for a
    # genuinely new/unmapped ISIC code.
    if isic:
        cls_id = _ISIC_ID_MAP.get(_normalize_isic(isic))
        if not cls_id:
            cls_id = _gen_uuid('classification:ISIC:' + isic)
            print(f"   WARNING: ISIC value '{isic}' not in the known classification "
                  f"lookup -- self-generating an id (logged).")
            _log_inconsistency('isic_classification', act_name, geography,
                                name=isic, fallback_id=cls_id, note='not in known ISIC lookup')
        xml += f'      <classification classificationId="{cls_id}">\n'
        xml += f'        <classificationSystem xml:lang="en">ISIC rev.4 ecoinvent</classificationSystem>\n'
        xml += f'        <classificationValue xml:lang="en">{_esc(isic)}</classificationValue>\n'
        xml += '      </classification>\n'

    # <geography> -- use the real, already-recognized ecoinvent geographyId
    # when known; only self-generate (and this would then need declaring in
    # usedUserMasterData too) for a genuinely new geography code.
    geo_id = _GEOGRAPHY_ID_MAP.get(geography)
    if not geo_id:
        geo_id = _gen_uuid('geography:' + geography)
        print(f"   WARNING: geography '{geography}' not in the known geography "
              f"lookup -- self-generating an id (logged).")
        _log_inconsistency('geography', act_name, geography,
                            fallback_id=geo_id, note='not in known geography lookup')
    xml += f'      <geography geographyId="{geo_id}">\n'
    xml += f'        <shortname xml:lang="en">{_esc(geography)}</shortname>\n'
    if geo_comment:
        xml += '        <comment>\n'
        xml += _text_paragraphs(geo_comment, '          ')
        xml += '        </comment>\n'
    xml += '      </geography>\n'

    # <technology>
    if tech_comment:
        xml += f'      <technology technologyLevel="{_esc(tech_level)}">\n'
        xml += '        <comment>\n'
        xml += _text_paragraphs(tech_comment, '          ')
        xml += '        </comment>\n'
        xml += '      </technology>\n'
    else:
        xml += f'      <technology technologyLevel="{_esc(tech_level)}" />\n'

    # <timePeriod>
    if tp_comment:
        xml += f'      <timePeriod startDate="{_esc(start_date)}" endDate="{_esc(end_date)}" isDataValidForEntirePeriod="true">\n'
        xml += '        <comment>\n'
        xml += _text_paragraphs(tp_comment, '          ')
        xml += '        </comment>\n'
        xml += '      </timePeriod>\n'
    else:
        xml += f'      <timePeriod startDate="{_esc(start_date)}" endDate="{_esc(end_date)}" isDataValidForEntirePeriod="true" />\n'

    xml += '      <macroEconomicScenario macroEconomicScenarioId="d9f57f0a-a01f-42eb-a57b-8f18d6635801">\n'
    xml += '        <name xml:lang="en">Business-as-Usual</name>\n'
    xml += '      </macroEconomicScenario>\n'
    xml += '    </activityDescription>\n'
    xml += '    <flowData>\n'

    # EcoSpold2 schema requires intermediateExchange before elementaryExchange.
    # Decided by the same rule _group_tag writes the row with: a row of no
    # known group is elementary when it carries a compartment.
    _KNOWN = ('ReferenceProduct', 'Products', 'ByProduct', 'FromTechnosphere')
    _is_elem = exchanges.apply(
        lambda r: _safe(r.get('group')).strip() in ('FromEnvironment', 'ToEnvironment')
        or (_safe(r.get('group')).strip() not in _KNOWN
            and bool(_safe(r.get('compartment')).strip())),
        axis=1,
    ).astype(bool) if not exchanges.empty else pd.Series(dtype=bool)
    ordered_exchanges = pd.concat([exchanges[~_is_elem], exchanges[_is_elem]])

    used_custom_flows = {}  # intermediateExchangeId -> (name, unitName, entry)
    for _, row in ordered_exchanges.iterrows():
        exc_name  = _safe(row.get('name', ''))
        exc_group = _safe(row.get('group', ''))
        exc_comp  = _safe(row.get('compartment', ''))
        exc_sub   = _safe(row.get('subcompartment', ''))
        exc_iex_id = _safe(row.get('intermediateExchangeId', ''))
        if exc_iex_id in _CUSTOM_FLOWS_BY_IEX_ID:
            used_custom_flows[exc_iex_id] = (exc_name, _safe(row.get('unitName', '')),
                                              _CUSTOM_FLOWS_BY_IEX_ID[exc_iex_id])
        if properties_df is not None and not properties_df.empty:
            prop_mask = (
                (properties_df['name'] == exc_name) &
                (properties_df['group'] == exc_group) &
                (properties_df['compartment'].fillna('') == exc_comp) &
                (properties_df['subcompartment'].fillna('') == exc_sub)
            )
            prop_rows = properties_df[prop_mask]
        else:
            prop_rows = pd.DataFrame()
        xml += _exchange_xml(row, act_name, prop_rows)

    xml += '    </flowData>\n'

    # <modellingAndValidation>
    mv_percent  = _safe(meta.get('modellingAndValidation_percent'))
    mv_sampling = _safe(meta.get('modellingAndValidation_samplingProcedure'))

    has_review = (not review_df.empty
                  and not review_df[review_df['activityName'] == act_name].empty)
    r_row  = None
    rname  = ''
    rdate  = ''
    r_maj_rel  = ''
    r_min_rel  = ''
    r_maj_rev  = ''
    r_min_rev  = ''
    r_details  = ''
    r_other    = ''
    if has_review:
        r_row      = review_df[review_df['activityName'] == act_name].iloc[0]
        rname      = _safe(r_row.get('reviewerName'))
        rdate      = _safe(r_row.get('reviewDate'))
        r_maj_rel  = _safe(r_row.get('reviewedMajorRelease'))
        r_min_rel  = _safe(r_row.get('reviewedMinorRelease'))
        r_maj_rev  = _safe(r_row.get('reviewedMajorRevision'))
        r_min_rev  = _safe(r_row.get('reviewedMinorRevision'))
        r_details  = _safe(r_row.get('details'))
        r_other    = _safe(r_row.get('otherDetails'))
        has_review = bool(rname or rdate or r_details or r_other
                          or r_maj_rel or r_min_rel or r_maj_rev or r_min_rev)

    if extrapolations or mv_percent or mv_sampling or has_review:
        xml += '    <modellingAndValidation>\n'
        if extrapolations or mv_percent or mv_sampling:
            pct_attr = f' percent="{_esc(mv_percent)}"' if mv_percent else ''
            xml += f'      <representativeness systemModelId="{_SYSTEM_MODEL_UNDEFINED_ID}"{pct_attr}>\n'
            xml += '        <systemModelName xml:lang="en">Undefined</systemModelName>\n'
            # EcoSpold2 element order: systemModelName → samplingProcedure → extrapolations
            if mv_sampling:
                xml += f'        <samplingProcedure xml:lang="en">{_esc(mv_sampling)}</samplingProcedure>\n'
            if extrapolations:
                xml += f'        <extrapolations xml:lang="en">{_esc(extrapolations)}</extrapolations>\n'
            xml += '      </representativeness>\n'
        if has_review:
            reviewer_id = _gen_uuid('reviewer:' + rname)
            rev_attrs = f' reviewerId="{reviewer_id}" reviewerName="{_esc(rname)}" reviewerEmail=""'
            if rdate:      rev_attrs += f' reviewDate="{_esc(rdate)}"'
            if r_maj_rel:  rev_attrs += f' reviewedMajorRelease="{_esc(r_maj_rel)}"'
            if r_min_rel:  rev_attrs += f' reviewedMinorRelease="{_esc(r_min_rel)}"'
            if r_maj_rev:  rev_attrs += f' reviewedMajorRevision="{_esc(r_maj_rev)}"'
            if r_min_rev:  rev_attrs += f' reviewedMinorRevision="{_esc(r_min_rev)}"'
            if r_details or r_other:
                xml += f'      <review{rev_attrs}>\n'
                if r_details:
                    xml += '        <details>\n'
                    xml += _text_paragraphs(r_details, '          ')
                    xml += '        </details>\n'
                if r_other:
                    xml += f'        <otherDetails>{_esc(r_other)}</otherDetails>\n'
                xml += '      </review>\n'
            else:
                xml += f'      <review{rev_attrs} />\n'
        xml += '    </modellingAndValidation>\n'

    # <administrativeInformation> — omitted only when all admin fields are absent
    deb_name      = _safe(meta.get('dataEntryBy_personName'))
    deb_active    = _safe(meta.get('dataEntryBy_isActiveAuthor'))
    # xs:boolean requires lowercase true/false
    if deb_active.lower() in ('true', '1', 'yes'):
        deb_active = 'true'
    elif deb_active.lower() in ('false', '0', 'no'):
        deb_active = 'false'
    dgp_name      = _safe(meta.get('dataGeneratorAndPublication_personName'))
    access_raw    = _safe(meta.get('dataGeneratorAndPublication_accessRestrictedTo'))
    published_raw = _safe(meta.get('dataGeneratorAndPublication_dataPublishedIn'))
    # Convert text descriptions back to numeric codes (XML schema requires integer)
    access        = _ACCESS_CODE.get(access_raw.strip().lower(), access_raw)
    dgp_published = _PUBLISHED_CODE.get(published_raw.strip().lower(), published_raw)
    dgp_pages     = _safe(meta.get('dataGeneratorAndPublication_pageNumbers'))
    dgp_source    = _safe(meta.get('dataGeneratorAndPublication_publishedSourceId'))
    maj_rel       = _safe(meta.get('fileAttributes_majorRelease'))
    min_rel       = _safe(meta.get('fileAttributes_minorRelease'))
    maj_rev       = _safe(meta.get('fileAttributes_majorRevision'))
    min_rev       = _safe(meta.get('fileAttributes_minorRevision'))
    # dataEntryBy, dataGeneratorAndPublication and fileAttributes are all
    # schema-mandatory for a standalone (non-child) dataset, so
    # administrativeInformation must always be written in full. Default to
    # the real pipeline author (a genuine, registered master-data person)
    # rather than a literal "Unknown" placeholder, which would always need
    # self-declaring and carries no real attribution anyway.
    deb_name = deb_name or 'Andreas Giakoumatos'
    dgp_name = dgp_name or 'Andreas Giakoumatos'
    persons_to_declare = {}

    def _person_id_email(person_name):
        entry = _PERSON_ID_MAP.get(person_name)
        if entry:
            return entry['personId'], entry['email']
        pid = _gen_uuid('person:' + person_name)
        print(f"   WARNING: person '{person_name}' not in the known person "
              f"lookup -- self-generating an id (logged).")
        _log_inconsistency('person', act_name, geography, name=person_name,
                            fallback_id=pid, note='not in known person lookup')
        persons_to_declare[pid] = person_name
        return pid, ''

    xml += '    <administrativeInformation>\n'
    deb_id, deb_email = _person_id_email(deb_name)
    deb_attrs = f' personId="{deb_id}" personName="{_esc(deb_name)}" personEmail="{_esc(deb_email)}"'
    if deb_active:
        deb_attrs += f' isActiveAuthor="{_esc(deb_active)}"'
    xml += f'      <dataEntryBy{deb_attrs} />\n'

    dgp_id, dgp_email = _person_id_email(dgp_name)
    dgp_attrs = f' personId="{dgp_id}" personName="{_esc(dgp_name)}" personEmail="{_esc(dgp_email)}" isCopyrightProtected="true"'
    if access:
        dgp_attrs += f' accessRestrictedTo="{_esc(access)}"'
    if dgp_published:
        dgp_attrs += f' dataPublishedIn="{_esc(dgp_published)}"'
    if dgp_pages:
        dgp_attrs += f' pageNumbers="{_esc(dgp_pages)}"'
    if dgp_source:
        dgp_attrs += f' publishedSourceId="{_esc(dgp_source)}"'
    xml += f'      <dataGeneratorAndPublication{dgp_attrs} />\n'

    _now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    fa_attrs = (
        f' majorRelease="{_esc(maj_rel or "3")}" minorRelease="{_esc(min_rel or "0")}"'
        f' majorRevision="{_esc(maj_rev or "0")}" minorRevision="{_esc(min_rev or "0")}"'
        f' internalSchemaVersion="{_INTERNAL_SCHEMA_VERSION}" defaultLanguage="en"'
        f' creationTimestamp="{_now}" lastEditTimestamp="{_now}" fileTimestamp="{_now}"'
        f' contextId="{_ECOINVENT_CONTEXT_ID}"'
    )
    xml += f'      <fileAttributes{fa_attrs}>\n'
    xml += '        <contextName xml:lang="en">ecoinvent</contextName>\n'
    xml += '      </fileAttributes>\n'
    xml += '    </administrativeInformation>\n'

    xml += '  </activityDataset>\n'

    # A brand-new activity id/activityNameId (always the case for these
    # new-dataset pipelines -- there is no pre-existing ecoinvent activityId)
    # must ALSO be self-declared here, exactly like a custom flow, or
    # ecoEditor rejects the file with "id ... is unknown" on <activity id>.
    # Schema element order matters: activityName, then intermediateExchange,
    # then activityIndexEntry (see TvalidUsedUserMasterData field order).
    fallback_elementary = {k: v for k, v in _FALLBACK_FLOWS.items() if v['kind'] == 'elementary'}
    fallback_intermediate = {k: v for k, v in _FALLBACK_FLOWS.items() if v['kind'] == 'intermediate'}
    if (used_custom_flows or fallback_elementary or fallback_intermediate
            or act_id_is_new or act_name_id_is_new or persons_to_declare):
        xml += (
            '  <usedUserMasterData'
            ' xmlns:es="http://www.EcoInvent.org/EcoSpold02"'
            ' xmlns="http://www.EcoInvent.org/UsedUserMasterData"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
            ' xsi:schemaLocation="http://www.EcoInvent.org/UsedUserMasterData'
            ' file:///C:/svn/ecospold2/trunk/Schemas/Extensions/UsedUserMasterData.xsd">\n'
        )
        if act_name_id_is_new:
            xml += f'    <activityName id="{_esc(act_name_id)}">\n'
            xml += f'      <name xml:lang="en">{_esc(act_name)}</name>\n'
            xml += '    </activityName>\n'
        # Schema element order: activityName, ..., person, ...,
        # elementaryExchange, intermediateExchange, ..., activityIndexEntry.
        for pid in sorted(persons_to_declare):
            xml += f'    <person id="{pid}" name="{_esc(persons_to_declare[pid])}" email="" />\n'
        for eex_id in sorted(fallback_elementary):
            f = fallback_elementary[eex_id]
            xml += f'    <elementaryExchange id="{eex_id}" unitId="{_esc(f["unitId"])}">\n'
            xml += f'      <name xml:lang="en">{_esc(f["name"])}</name>\n'
            xml += f'      <unitName xml:lang="en">{_esc(f["unitName"])}</unitName>\n'
            xml += f'      <compartment subcompartmentId="{f["subcompartmentId"]}">\n'
            xml += f'        <compartment xml:lang="en">{_esc(f["compartment"])}</compartment>\n'
            xml += f'        <subcompartment xml:lang="en">{_esc(f["subcompartment"])}</subcompartment>\n'
            xml += '      </compartment>\n'
            xml += '    </elementaryExchange>\n'
        for iex_id in sorted(used_custom_flows):
            name, unit_name, entry = used_custom_flows[iex_id]
            xml += f'    <intermediateExchange id="{iex_id}" unitId="{_esc(entry["unitId"])}">\n'
            xml += f'      <name xml:lang="en">{_esc(name)}</name>\n'
            xml += f'      <unitName xml:lang="en">{_esc(unit_name or entry["unitName"])}</unitName>\n'
            for class_line in entry['classification'].splitlines():
                xml += f'      {class_line}\n'
            xml += '    </intermediateExchange>\n'
        for iex_id in sorted(fallback_intermediate):
            f = fallback_intermediate[iex_id]
            xml += f'    <intermediateExchange id="{iex_id}" unitId="{_esc(f["unitId"])}">\n'
            xml += f'      <name xml:lang="en">{_esc(f["name"])}</name>\n'
            xml += f'      <unitName xml:lang="en">{_esc(f["unitName"])}</unitName>\n'
            # Its CPC and by-product classification too, where known by name --
            # a new product awaiting its registered id is classified all the same.
            for class_line in _MASTER_DATA_CLASSIFICATIONS_BY_NAME.get(f['name'], '').splitlines():
                xml += f'      {class_line}\n'
            xml += '    </intermediateExchange>\n'
        if act_id_is_new:
            xml += (
                f'    <activityIndexEntry id="{_esc(act_id_val)}"'
                f' activityNameId="{_esc(act_name_id)}"'
                f' geographyId="{_esc(geo_id)}"'
                f' startDate="{_esc(start_date)}"'
                f' endDate="{_esc(end_date)}"'
                f' specialActivityType="{_esc(spe_type)}"'
                f' systemModelId="{_SYSTEM_MODEL_UNDEFINED_ID}" />\n'
            )
        xml += '  </usedUserMasterData>\n'

    xml += '</ecoSpold>\n'
    return xml


# ── Main ──────────────────────────────────────────────────────────────────────

def main(_input_path=None, _output_dir=None, _stem=None):
    """Convert one pipeline-output Excel file to one or more .spold files.

    Parameters
    ----------
    _input_path : str, optional
        Path to the pipeline output Excel (.xlsx). Defaults to INPUT_FILEPATH.
    _output_dir : str, optional
        Directory to write .spold files into. Defaults to SPOLD_OUTPUT_DIR.
    _stem : str, optional
        Base filename stem (without extension) for the output .spold file.
        Defaults to the stem of OLCAPEST_INPUT_FILENAME (single-file mode).
    """
    _input_path = _input_path or INPUT_FILEPATH
    _output_dir = _output_dir or SPOLD_OUTPUT_DIR
    _stem       = _stem       or Path(OLCAPEST_INPUT_FILENAME).stem

    if not os.path.isfile(_input_path):
        print(f"  Input file not found: {_input_path}")
        return False

    os.makedirs(_output_dir, exist_ok=True)
    print(f"\n  Input  : {_input_path}")
    print(f"  Output : {_output_dir}")

    all_sheets   = pd.read_excel(_input_path, sheet_name=None)
    # The template's guidance and example rows are never an activity.
    from config_new_datasets import drop_template_guidance
    drop_template_guidance(all_sheets)
    meta_df       = all_sheets.get('meta',       pd.DataFrame())
    review_df     = all_sheets.get('review',     pd.DataFrame())
    exchanges_df  = all_sheets.get('exchanges',  pd.DataFrame())
    properties_df = all_sheets.get('properties', pd.DataFrame())

    if meta_df.empty:
        print("  WARNING: 'meta' sheet is empty -- nothing to convert.")
        return False

    activities = meta_df['activityName'].unique()
    print(f"  Found {len(activities)} activity/activities")

    # A row reaches an activity's file only through its activityName, so a
    # row with a blank one used to vanish from the .spold without a word --
    # a transport input of a clementine dataset, for one.  With a single
    # activity there is nowhere else it can belong, so it is given that
    # activity.  With several it cannot be placed: it is logged and named.
    for _sheet, _frame in (('exchanges', exchanges_df), ('properties', properties_df)):
        if _frame.empty or 'activityName' not in _frame.columns:
            continue
        _blank = _frame['activityName'].isna() | (
            _frame['activityName'].astype(str).str.strip() == '')
        if 'name' in _frame.columns:
            # Rows with neither a name nor an activity are empty filler.
            _blank &= _frame['name'].notna()
        if not _blank.any():
            continue
        if len(activities) == 1:
            _frame.loc[_blank, 'activityName'] = activities[0]
            print(f"  NOTE: {int(_blank.sum())} {_sheet} row(s) had no activityName "
                  f"-- given the dataset's only activity")
        else:
            for _, _row in _frame[_blank].iterrows():
                _log_inconsistency('missing_activityName', '', _safe(_row.get('geography')),
                                   name=_safe(_row.get('name')),
                                   note=f'{_sheet} row has no activityName; the '
                                        f'workbook has {len(activities)} activities, '
                                        f'so it is in none of their .spold files')
            print(f"  WARNING: {int(_blank.sum())} {_sheet} row(s) have no activityName "
                  f"and {len(activities)} activities to choose from -- NOT written "
                  f"(logged).")

    # Clear THIS dataset's previously written files first.  A file is named
    # after the activity it holds, so an activity that gets renamed -- or a
    # dataset that now has fewer of them -- leaves the old file behind, and
    # nothing distinguishes it from a current one when the folder is handed
    # over.  Only files this dataset wrote are touched: its own stem, and the
    # per-activity files that start with it.
    _written_before = sorted(
        [f for f in Path(_output_dir).glob(_stem + '.spold')]
        + [f for f in Path(_output_dir).glob(_stem + '_*.spold')])
    for _old in _written_before:
        try:
            _old.unlink()
        except OSError:
            pass
    if _written_before:
        print(f"  Replacing {len(_written_before)} .spold file(s) of "
              f"{_stem!r} written by an earlier run")

    for idx, act_name in enumerate(activities):
        act_meta = meta_df[meta_df['activityName'] == act_name].iloc[0]
        act_exch  = (exchanges_df[exchanges_df['activityName'] == act_name]
                     if not exchanges_df.empty else pd.DataFrame())
        act_props = (properties_df[properties_df['activityName'] == act_name]
                     if not properties_df.empty else pd.DataFrame())

        spold_xml = _build_spold(act_meta, act_exch, review_df, act_props)

        if len(activities) == 1:
            fname = _stem + '.spold'
        else:
            geo   = _safe(act_meta.get('geography'), str(idx))
            fname = f"{_stem}_{act_name}_{geo}.spold".replace('/', '_')

        out_path = Path(_output_dir) / fname
        out_path.write_text(spold_xml, encoding='utf-8')
        print(f"  Written: {fname}")

    return True


# ── Converting on save ───────────────────────────────────────────────────────
# Every program that saves a dataset workbook -- the meta model and the three
# calculators -- calls write_spold_for() right after the save, so the .spold
# in spold_output/ always matches the workbook beside it.  The inconsistency
# log is collected per output folder and written once, when the process ends:
# re-reading and rewriting the report after every one of hundreds of saves
# would cost more than the conversions themselves.
_PENDING_REPORTS: dict = {}
_FLUSH_AT_EXIT = False


def flush_spold_reports() -> None:
    """Write the inconsistency report of every folder converted into so far."""
    global _INCONSISTENCY_LOG
    pending = dict(_PENDING_REPORTS)
    _PENDING_REPORTS.clear()
    for folder, entries in pending.items():
        _INCONSISTENCY_LOG = entries
        try:
            save_inconsistency_report(folder)
        except Exception as exc:
            print(f"  WARNING: inconsistency report for {folder} not written "
                  f"({exc.__class__.__name__}: {exc})")
    _INCONSISTENCY_LOG = []


def write_spold_for(workbook_path: str) -> bool:
    """
    Rewrite the .spold of one just-saved dataset workbook.

    Written to spold_output/ beside the workbook, named after its stem.  Never
    raises: the workbook is already saved, and a conversion failure must not
    undo the step that saved it -- it is reported instead.
    """
    global _INCONSISTENCY_LOG, _FLUSH_AT_EXIT
    folder = os.path.dirname(os.path.abspath(workbook_path))
    stem   = Path(workbook_path).stem
    if not _FLUSH_AT_EXIT:
        import atexit
        atexit.register(flush_spold_reports)
        _FLUSH_AT_EXIT = True
    _INCONSISTENCY_LOG = []
    try:
        ok = main(_input_path=workbook_path,
                  _output_dir=os.path.join(folder, 'spold_output'), _stem=stem)
    except Exception as exc:
        print(f"  WARNING: .spold for {stem!r} not written "
              f"({exc.__class__.__name__}: {exc})")
        ok = False
    _PENDING_REPORTS.setdefault(folder, []).extend(_INCONSISTENCY_LOG)
    _INCONSISTENCY_LOG = []
    return ok is not False


if __name__ == '__main__':
    import argparse as _ap, json as _json, pathlib as _pl
    _parser = _ap.ArgumentParser()
    _parser.add_argument('--batch', default=None)
    _args, _ = _parser.parse_known_args()

    if _args.batch:
        _batch  = _json.loads(_args.batch)
        _BASE   = _ROOT
        _failed = []
        print(f'[SPOLD BATCH] {len(_batch)} datasets')
        for _project, _filename in _batch:
            _stem  = _pl.Path(_filename).stem
            _f     = os.path.join(_BASE, 'output', _project, _stem + '.xlsx')
            _sdir  = os.path.join(_BASE, 'output', _project, 'spold_output')
            print(f'\n--- {_filename} ---')
            try:
                ok = main(_input_path=_f, _output_dir=_sdir, _stem=_stem)
                if ok is False:
                    _failed.append(_filename)
            except Exception as _exc:
                print(f'  ERROR: {_exc}')
                _failed.append(_filename)
        if _failed:
            print(f'\n[SPOLD BATCH] WARNING: {len(_failed)} file(s) skipped: {_failed}')
            print(f'[SPOLD BATCH] {len(_batch) - len(_failed)}/{len(_batch)} datasets completed.')
        else:
            print(f'\n[SPOLD BATCH] All {len(_batch)} datasets completed.')
        if _batch:
            _report_dir = os.path.join(_BASE, 'output', _batch[0][0])
            save_inconsistency_report(_report_dir)
    else:
        main()
        save_inconsistency_report(os.path.dirname(SPOLD_OUTPUT_DIR))

