import os
import re
import sys
import datetime
import pandas as pd

# See agri_diesel_calculator_new.py: never let a print() of arbitrary source
# data (unencodable on Windows' console codepage) abort real work.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')

# ── Configuration (paths from central config) ─────────────────────────────────
# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
from config_new_datasets import (
    OLCAPEST_TOOL_FILE         as TOOL_FILEPATH,
    OLCAPEST_INPUT_FILE        as EXAMPLE_FILEPATH,
    OLCAPEST_INPUT_SHEET       as EXAMPLE_SHEET,
    OLCAPEST_OUTPUT_FILE       as OUTPUT_FILEPATH,
    CROP_CLASS_OVERRIDES,
    REVIEW_COLS,
    complete_sheet_schema,
    drop_template_guidance,
    record_deletions,
    write_spold_for,
)
try:
    from config_new_datasets import OLCAPEST_SUBSTANCE_SYNONYMS
except ImportError:          # older config without the synonym table
    OLCAPEST_SUBSTANCE_SYNONYMS = {}
try:
    from config_new_datasets import OLCAPEST_DOSE_DRIVES_EMISSION
except ImportError:
    OLCAPEST_DOSE_DRIVES_EMISSION = {}
try:
    from config_new_datasets import OLCAPEST_GENERATE_EMISSIONS
except ImportError:
    OLCAPEST_GENERATE_EMISSIONS = {}

# Sheet names
CLASSIFICATION_SHEET = 'CLASSIFICATION'
OLCA_CALC_SHEET = 'OLCA CALC'
CONTEXTUALIZED_PEST_SHEET = 'contextualized_Pest'

# Column names
CLASS_COL = 'class_C'
PEST_CLASS_COL = 'Pest_class'
GEOGRAPHY_COL = 'geography'
CROP_COL = 'activityName'
PESTICIDE_NAME_COL = 'name'
AMOUNT_COL = 'amount'

# Subcompartment names (must match reshaped OLCA CALC columns)
NON_URBAN_AIR_SUBC = 'non-urban air or from high stacks'
AGRICULTURAL_SUBC = 'agricultural'
FORESTRY_SUBC = 'forestry'
SURFACE_WATER_SUBC = 'surface water'
COMPARTMENT_COLS = [NON_URBAN_AIR_SUBC, AGRICULTURAL_SUBC, FORESTRY_SUBC, SURFACE_WATER_SUBC]

COMPARTMENT_MAP = {
    NON_URBAN_AIR_SUBC: 'air',
    AGRICULTURAL_SUBC: 'soil',
    FORESTRY_SUBC: 'soil',
    SURFACE_WATER_SUBC: 'water',
}

# ── Geography helpers ─────────────────────────────────────────────────────────
_GEOGRAPHY_TO_UN_REGION = {
    'GLO': 'GLO',
    'RAF': 'RAF', 'Africa': 'RAF',
    'UN-AMERICAS': 'UN-AMERICAS', 'Americas': 'UN-AMERICAS',
    'RAS': 'RAS', 'Asia': 'RAS',
    'RER': 'RER', 'Europe': 'RER',
    'UN-OCEANIA': 'UN-OCEANIA', 'Oceania': 'UN-OCEANIA',
}
_AFRICA   = {'DZ','AO','BJ','BW','BF','BI','CM','CV','CF','TD','KM','CG','CD','CI','DJ','EG','GQ','ER','ET','GA','GM','GH','GN','GW','KE','LS','LR','LY','MG','MW','ML','MR','MU','YT','MA','MZ','NA','NE','NG','RE','RW','ST','SN','SC','SL','SO','ZA','SS','SD','SZ','TZ','TG','TN','UG','ZM','ZW','EH'}
_AMERICAS = {'AG','AR','AW','BS','BB','BZ','BM','BO','BR','CA','KY','CL','CO','CR','CU','CW','DM','DO','EC','SV','FK','GF','GL','GD','GP','GT','GY','HT','HN','JM','MQ','MX','MS','NI','PA','PY','PE','PR','BL','KN','LC','MF','PM','VC','SX','SR','TT','TC','US','UY','VE','VG','VI'}
_ASIA     = {'AF','AM','AZ','BH','BD','BT','BN','KH','CN','CX','CC','IO','GE','HK','IN','ID','IR','IQ','IL','JP','JO','KZ','KW','KG','LA','LB','MO','MY','MV','MN','MM','NP','KP','OM','PK','PS','PH','QA','SA','SG','KR','LK','SY','TW','TJ','TH','TL','TR','TM','AE','UZ','VN','YE'}
_EUROPE   = {'AX','AL','AD','AT','BY','BE','BA','BG','HR','CY','CZ','DK','EE','FO','FI','FR','DE','GI','GR','GG','HU','IS','IE','IM','IT','JE','XK','LV','LI','LT','LU','MK','MT','MD','MC','ME','NL','NO','PL','PT','RO','RU','SM','RS','SK','SI','ES','SJ','SE','CH','UA','GB','VA'}
_OCEANIA  = {'AS','AU','CK','FJ','PF','GU','KI','MH','FM','NR','NC','NZ','NU','NF','MP','PW','PG','PN','WS','SB','TK','TO','TV','UM','VU','WF'}

def _map_to_un_region(geo: str) -> str:
    if geo in _GEOGRAPHY_TO_UN_REGION:
        return _GEOGRAPHY_TO_UN_REGION[geo]
    g = geo.upper()
    if g in _AFRICA:   return 'RAF'
    if g in _AMERICAS: return 'UN-AMERICAS'
    if g in _ASIA:     return 'RAS'
    if g in _EUROPE:   return 'RER'
    if g in _OCEANIA:  return 'UN-OCEANIA'
    return 'GLO'

# ── Read and reshape OLCA CALC sheet (geography-aware) ────────────────────────
def reshape_olca_calc(tool_filepath: str) -> pd.DataFrame:
    """
    Read OLCA CALC sheet and reshape from wide format (geography as column groups)
    to long format (one row per class_C / Pest_class / geography combination).

    Excel layout:
      Row 2 (index 1) : ISO codes at positions 8, 15, 22, … (every 7 columns)
      Row 7 (index 6) : Column headers used as DataFrame header
      Each geography block has 7 compartment columns.
    """
    olca_df  = pd.read_excel(tool_filepath, sheet_name=OLCA_CALC_SHEET, header=6)
    olca_raw = pd.read_excel(tool_filepath, sheet_name=OLCA_CALC_SHEET, header=None)

    iso_row = olca_raw.iloc[1]
    iso_mapping = {}
    for col_idx in range(8, len(iso_row), 7):
        iso = iso_row[col_idx]
        if pd.notna(iso) and str(iso).strip() not in ('', 'nan'):
            iso_mapping[col_idx] = str(iso).strip()

    if not iso_mapping:
        raise ValueError("No ISO geography codes found in OLCA CALC sheet row 2. Check file structure.")

    id_cols = [col for col in olca_df.columns
               if isinstance(col, str) and (col in (CLASS_COL, PEST_CLASS_COL) or col.startswith('c_'))]

    raw_compartment_names = [
        'soil, agricultural', 'soil, natural/forest', 'water, surface/river',
        'crop', 'air', 'tot', 'soil, agricultural + crop',
    ]

    geo_frames = []
    for col_base, iso_code in iso_mapping.items():
        geo_cols = [olca_df.columns[col_base + i]
                    for i in range(7) if col_base + i < len(olca_df.columns)]
        if len(geo_cols) != 7:
            continue
        geo_df = olca_df[id_cols + geo_cols].copy()
        geo_df = geo_df.rename(columns=dict(zip(geo_cols, raw_compartment_names)))
        geo_df[GEOGRAPHY_COL] = iso_code
        geo_frames.append(geo_df)

    result = pd.concat(geo_frames, ignore_index=True)

    # Combine soil, agricultural + crop → 'agricultural'
    result[AGRICULTURAL_SUBC] = result['soil, agricultural'] + result['crop']

    # Rename to final subcompartment names
    result = result.rename(columns={
        'soil, natural/forest':  FORESTRY_SUBC,
        'water, surface/river':  SURFACE_WATER_SUBC,
        'air':                   NON_URBAN_AIR_SUBC,
    })

    # Drop intermediate / unused columns
    drop_cols = {'c_ScenarioName', 'tot', 'soil, agricultural + crop', 'crop', 'soil, agricultural'}
    drop_cols |= {col for col in result.columns if isinstance(col, str) and col.startswith('c_Prim')}
    result = result.drop(columns=[c for c in drop_cols if c in result.columns])

    # Deduplicate (keep first if correction factors are effectively equal)
    result = result.drop_duplicates(subset=[CLASS_COL, PEST_CLASS_COL, GEOGRAPHY_COL], keep='first')

    return result

# ── Herbicide class adjustment (pure function) ────────────────────────────────
def adjust_pest_class(class_c: str, pest_class: str) -> str:
    if pest_class in ('Hrb-pre', 'Hrb-Avg'):
        if any(kw in (class_c or '') for kw in ('trees', 'Other', 'Grapes')):
            return 'Hrb-post'
    return pest_class

# ── Uncertainty / DQS configuration ──────────────────────────────────────────
_VARIANCE_BY_SUBC = {
    AGRICULTURAL_SUBC:  0.033,
    FORESTRY_SUBC:      0.033,
    NON_URBAN_AIR_SUBC: 0.04,
    SURFACE_WATER_SUBC: 0.04,
}
_VARIANCE_OVERRIDE = {
    ('Ac/Mi', FORESTRY_SUBC): 0.04,
}
UNCERTAINTY_TYPE = 'lognormal'
DQS_RELIABILITY  = 3
DQS_COMPLETENESS = 3
DQS_TEMPORAL     = 1
DQS_GEOGRAPHICAL = 2
DQS_TECHNOLOGY   = 3

def get_variance(pest_class: str, subcomp: str) -> float:
    return _VARIANCE_OVERRIDE.get((pest_class, subcomp), _VARIANCE_BY_SUBC.get(subcomp, 0.033))

CLEAN_COLS = [
    'activityName', 'geography', 'group', 'name', 'compartment', 'subcompartment',
    'activityLink_activityName', 'activityLink_geography', 'unitName', 'amount',
    'variableName', 'mathematicalRelation', 'comment', 'pageNumbers', 'sourceId',
    'uncertainty type', 'uncertainty comment', 'variance', 'minValue', 'mostLikelyValue',
    'maxValue', 'reliability', 'completeness', 'temporalCorrelation',
    'geographicalCorrelation', 'furtherTechnologyCorrelation',
    'PV_amount', 'PV_variableName', 'PV_mathematicalRelation', 'PV_comment', 'PV_sourceId',
    'PV_uncertainty type', 'PV_uncertainty comment', 'PV_variance', 'PV_minValue',
    'PV_mostLikelyValue', 'PV_maxValue', 'PV_reliability', 'PV_completeness',
    'PV_temporalCorrelation', 'PV_geographicalCorrelation', 'PV_furtherTechnologyCorrelation',
]


def load_tool_data(tool_filepath: str) -> dict:
    """
    Load OLCApest tool sheets from Excel, with a pickle cache so that repeated
    calls (e.g. across datasets in batch mode) skip the slow Excel parse.
    Invalidated automatically when the tool file is newer than the cache.
    """
    import pickle
    cache_path = tool_filepath + '_olcapest_cache.pkl'
    if (os.path.exists(cache_path)
            and os.path.getmtime(cache_path) > os.path.getmtime(tool_filepath)):
        with open(cache_path, 'rb') as fh:
            return pickle.load(fh)
    print('  Loading OLCApest tool from Excel (first run or tool updated)...')
    data = {
        'df_pesticides': pd.read_excel(tool_filepath, sheet_name=CONTEXTUALIZED_PEST_SHEET),
        'df_activities': pd.read_excel(tool_filepath, sheet_name=CLASSIFICATION_SHEET),
        'olca_calc':     reshape_olca_calc(tool_filepath),
    }
    with open(cache_path, 'wb') as fh:
        pickle.dump(data, fh)
    print(f'  OLCApest tool cached to {cache_path}')
    return data


MASS_BALANCE_TOLERANCE = 1e-3      # relative


def check_pesticide_mass_balance(df_non_env, df_results, lookup_pest_class,
                                 tool_name_for, alias_skips, factor=None) -> bool:
    """
    For every pesticide bought FromTechnosphere, the OLCApest-allocated
    emissions of it must sum to the amount bought (x factor, where the project
    has the dose drive the emission).  Reports every activity that does not,
    and returns True when all balance.
    """
    if df_non_env.empty or 'group' not in df_non_env.columns:
        return True
    inputs = df_non_env[df_non_env['group'] == 'FromTechnosphere']
    applied: dict[tuple, float] = {}
    for _, row in inputs.iterrows():
        name = str(row.get(PESTICIDE_NAME_COL, '')).strip()
        if not name or lookup_pest_class(name) is None:
            continue
        amount = pd.to_numeric(row.get(AMOUNT_COL), errors='coerce')
        if pd.isna(amount):
            continue
        key = (str(row.get(CROP_COL, '')), name)
        applied[key] = applied.get(key, 0.0) + float(amount)
    if not applied:
        return True

    emitted: dict[tuple, float] = {}
    if not df_results.empty:
        for (crop, name), amt in (df_results.groupby(['Crop', 'Pesticide'])['Amount']
                                  .sum().items()):
            emitted[(str(crop), str(name))] = float(amt)

    # An input is paired with the emission of the same name (or its synonym),
    # or with the emission already taken as the same application under
    # another name ('copper oxide' bought, 'Copper oxychloride' emitted).
    aliases = {(crop, name.lower()): alias for crop, name, alias, _ in alias_skips}
    expected_factor = 1.0 if factor is None else float(factor)

    bad = []
    for (crop, name), dose in sorted(applied.items()):
        if dose <= 0:
            continue
        alias = aliases.get((crop, name.lower()), '').lower()
        total = sum(amt for (c, e), amt in emitted.items()
                    if c == crop and (e.lower() == name.lower()
                                      or tool_name_for(e).strip().lower() == name.lower()
                                      or (alias and e.lower() == alias)))
        target = dose * expected_factor
        if abs(total / target - 1.0) > MASS_BALANCE_TOLERANCE:
            bad.append((crop, name, target, total))

    if not bad:
        print(f"\nOK Mass balance: OLCApest emissions match the applied amount "
              f"for all {sum(1 for v in applied.values() if v > 0)} pesticide input(s).")
        return True

    print()
    print(f"  CHECK FAILED: OLCApest emissions do not sum to the FromTechnosphere "
          f"input for {len(bad)} pesticide(s):")
    for crop, name, target, total in bad:
        print(f"      {name[:30]:<32} input {target:.6g} kg, allocated {total:.6g} kg "
              f"({total / target:.2%})   [{crop[:40]}]")
    print("           The rest of the dose is emitted under a flow OLCApest does not "
          "recognise,")
    print("           and passed through unsplit.  Rename that emission row in the "
          "input dataset to")
    print("           the pesticide's own emission flow (e.g. 'Copper ion' -> "
          "'Copper oxide').")
    print()
    return False


def main(tool_data=None, _input_path=None, _output_path=None):
    _input_path  = _input_path  or EXAMPLE_FILEPATH
    _output_path = _output_path or OUTPUT_FILEPATH
    os.makedirs(os.path.dirname(_output_path), exist_ok=True)

    # ── Load ALL sheets from input file ────────────────────────────────────────────────
    print("Reading data...")
    print(f"  Input file : {_input_path}")

    input_path = _input_path
    if str(input_path).lower().endswith('.spold'):
        from config_new_datasets import SPOLD_DIR as _spold_dir
        sys.path.insert(0, _spold_dir)
        from spold_reader_new import read_spold
        print("  Detected .spold input -- converting to DataX format...")
        all_input_sheets = read_spold(input_path)
        print(f"  Sheets loaded: {list(all_input_sheets.keys())}")
    else:
        all_input_sheets = pd.read_excel(input_path, sheet_name=None)
        # The provider template's readme sheet and explanation rows.
        drop_template_guidance(all_input_sheets, _output_path, 'OLCApest')
        print(f"  Sheets found: {list(all_input_sheets.keys())}")

    # ── Blank rows in, blank rows everywhere ─────────────────────────────────
    # A workbook edited by hand often carries empty trailing rows.  Carried
    # through, they reach the split as an activity of their own -- a project
    # of four activities reported five -- and the .spold as exchanges
    # belonging to nothing.  They are dropped here, once, at the door.
    for _sheet_name, _sheet in list(all_input_sheets.items()):
        if not isinstance(_sheet, pd.DataFrame) or _sheet.empty:
            continue
        _kept = _sheet.dropna(how='all')
        # A row with neither an activity nor a flow name is not an exchange
        # whatever else is in it -- a stray number left in one column while
        # editing, say.  It has nothing to belong to.
        if {'activityName', 'name'} <= set(_kept.columns):
            _kept = _kept[~(_kept['activityName'].isna() & _kept['name'].isna())]
        if len(_kept) != len(_sheet):
            print(f"  {_sheet_name}: dropped {len(_sheet) - len(_kept)} "
                  f"row(s) that name neither an activity nor a flow")
            # Only rows that held something are worth listing: a wholly
            # empty row carries nothing to lose.
            _gone = _sheet.loc[~_sheet.index.isin(_kept.index)].dropna(how='all')
            record_deletions(_output_path, 'OLCApest', _sheet_name,
                             [', '.join(f'{c}={v!r}' for c, v in r.dropna().items())
                              for _, r in _gone.iterrows()],
                             'row naming neither an activity nor a flow')
            all_input_sheets[_sheet_name] = _kept.reset_index(drop=True)

    # ── Work on the exchanges sheet ───────────────────────────────────────────
    df_all_exchanges = all_input_sheets.get(EXAMPLE_SHEET, pd.DataFrame()).copy()

    # Some files have a blank header for the 'amount' column; pandas names it
    # 'Unnamed: N'.  Detect it (first unnamed column whose values are >50%
    # numeric) and rename it so downstream code can always use AMOUNT_COL.
    if 'amount' not in df_all_exchanges.columns:
        for _col in df_all_exchanges.columns:
            if str(_col).startswith('Unnamed:'):
                _frac = pd.to_numeric(df_all_exchanges[_col], errors='coerce').notna().mean()
                if _frac > 0.5:
                    df_all_exchanges = df_all_exchanges.rename(columns={_col: 'amount'})
                    print(f"  Note: 'amount' header was blank in Excel; renamed from '{_col}'.")
                    break

    # OLCApest operates on existing ToEnvironment (elementary) emissions — NOT inputs.
    # We take existing pesticide emissions, sum duplicates, then redistribute via OLCApest.
    if 'group' in df_all_exchanges.columns:
        df_to_env = df_all_exchanges[
            df_all_exchanges['group'] == 'ToEnvironment'
        ].reset_index(drop=True)
        df_non_env = df_all_exchanges[
            df_all_exchanges['group'] != 'ToEnvironment'
        ].reset_index(drop=True)
    else:
        df_to_env = pd.DataFrame()
        df_non_env = df_all_exchanges

    print(f"  ToEnvironment rows: {len(df_to_env)}  |  other rows: {len(df_non_env)}")

    # ── Load OLCApest tool (pickle-cached; pre-loaded in batch mode) ──────────────
    _tool         = tool_data if tool_data is not None else load_tool_data(TOOL_FILEPATH)
    df_pesticides = _tool['df_pesticides']
    df_activities = _tool['df_activities']
    olca_calc     = _tool['olca_calc']

    # Pesticide name → class mapping (name and lowercase)
    pesticide_mapping = dict(zip(df_pesticides['name'], df_pesticides[PEST_CLASS_COL]))
    _pest_lower = {k.lower(): v for k, v in pesticide_mapping.items()}
    # The synonyms of THIS project only.  The same flow name means the
    # applied fungicide in one inventory and a trace contaminant in another,
    # so the table is keyed by project; '*' covers every project.
    _project_name = os.path.basename(os.path.dirname(os.path.abspath(input_path)))
    _table = OLCAPEST_SUBSTANCE_SYNONYMS or {}
    _entries = {}
    _entries.update(_table.get('*') or {})
    _entries.update(_table.get(_project_name) or {})
    _synonyms = {str(k).strip().lower(): v for k, v in _entries.items()}
    # Substances a project wants EXCLUDED from emission generation, because
    # it models them by hand.  Generation is otherwise on for everything the
    # mapping sheet classes as a pesticide.
    _forbid = {str(n).strip().lower()
               for n in ((OLCAPEST_GENERATE_EMISSIONS or {}).get(_project_name) or ())}
    if _forbid:
        print(f"  Emission generation switched OFF for {_project_name!r}: "
              + ', '.join(sorted(_forbid)))
    if _synonyms:
        print(f"  Substance synonyms for {_project_name!r}: "
              + ', '.join(f'{k!r} -> {v!r}' for k, v in _entries.items()))

    # CAS number → class mapping if the tool sheet has a CAS column
    _pest_cas: dict[str, str] = {}
    for cas_col in ('casNumber', 'CAS', 'CAS number', 'CAS Number'):
        if cas_col in df_pesticides.columns:
            _with_cas = df_pesticides.dropna(subset=[cas_col])
            _pest_cas = {str(cas).strip(): cls for cas, cls in
                         zip(_with_cas[cas_col], _with_cas[PEST_CLASS_COL])}
            break

    crop_mapping      = dict(zip(df_activities['Crop'], df_activities[CLASS_COL]))
    available_geos    = set(olca_calc[GEOGRAPHY_COL].unique())
    print(f"  OLCA CALC: {len(olca_calc)} rows, {len(available_geos)} geographies.")

    _geo_cache: dict[str, str] = {}

    def get_matching_geography(geo: str) -> str:
        if geo in _geo_cache:
            return _geo_cache[geo]
        if geo in available_geos:
            result = geo
        elif geo == 'Canada without Quebec' and 'CA' in available_geos:
            result = 'CA'
        elif '-' in geo and (parent := geo.split('-')[0]) in available_geos:
            result = parent
        else:
            un = _map_to_un_region(geo)
            result = un if un in available_geos else ('GLO' if 'GLO' in available_geos else un)
        _geo_cache[geo] = result
        if result != geo:
            print(f"  Geography '{geo}' -> '{result}' (fallback)")
        return result

    def get_class_C(crop_name: str):
        # 1. Exact match in user overrides
        if crop_name in CROP_CLASS_OVERRIDES:
            return CROP_CLASS_OVERRIDES[crop_name]
        # 2. Substring match in user overrides (key appears anywhere in activity name)
        cn_lower = crop_name.lower()
        for key, cls in CROP_CLASS_OVERRIDES.items():
            if key.lower() in cn_lower:
                return cls
        # 3. Exact match in CLASSIFICATION crop_mapping
        if crop_name in crop_mapping:
            return crop_mapping[crop_name]
        for suffix in (' production', ''):
            test = crop_name.replace(' production', '').replace(' Production', '') + suffix
            if test in crop_mapping:
                return crop_mapping[test]
        # 4. Fuzzy match via substring containment
        for activity_name, class_c in crop_mapping.items():
            an_lower = activity_name.lower()
            if cn_lower in an_lower or an_lower in cn_lower:
                return class_c
        # 5. Fuzzy match ignoring spaces
        cn_nospace = cn_lower.replace(' ', '').replace('-', '')
        for activity_name, class_c in crop_mapping.items():
            an_nospace = activity_name.lower().replace(' ', '').replace('-', '')
            if cn_nospace in an_nospace or an_nospace in cn_nospace:
                return class_c
        return None

    def tool_name_for(name: str) -> str:
        """The tool's name for a dataset's emission name."""
        return _synonyms.get(str(name).strip().lower(), str(name))

    def lookup_pest_class(name: str, cas: str = '') -> str | None:
        cls = pesticide_mapping.get(name) or _pest_lower.get(name.lower())
        if cls:
            return cls
        # A dataset may name the emission after the element ("Copper ion")
        # where the tool knows the active substance ("Copper oxide").
        synonym = _synonyms.get(str(name).strip().lower())
        if synonym:
            cls = (pesticide_mapping.get(synonym)
                   or _pest_lower.get(synonym.lower()))
            if cls:
                return cls
        if cas and cas in _pest_cas:
            return _pest_cas[cas]
        return None

    def lookup_allocation(class_c: str, pest_class: str, geo: str) -> pd.DataFrame:
        mask = (
            (olca_calc[CLASS_COL]      == class_c)    &
            (olca_calc[PEST_CLASS_COL] == pest_class) &
            (olca_calc[GEOGRAPHY_COL]  == geo)
        )
        row = olca_calc[mask]
        if row.empty and pest_class == 'Hrb-Avg':
            row = olca_calc[(olca_calc[CLASS_COL]==class_c)&(olca_calc[PEST_CLASS_COL]=='Hrb-post')&(olca_calc[GEOGRAPHY_COL]==geo)]
        elif row.empty and pest_class == 'Hrb-post':
            row = olca_calc[(olca_calc[CLASS_COL]==class_c)&(olca_calc[PEST_CLASS_COL]=='Hrb-Avg') &(olca_calc[GEOGRAPHY_COL]==geo)]
        return row

    # ── STEP 1: identify pesticide rows in ToEnvironment ─────────────────────
    # A row is a pesticide emission if its name (or CAS number) maps to a pest_class.
    if not df_to_env.empty:
        has_cas = 'casNumber' in df_to_env.columns
        pest_mask = df_to_env[PESTICIDE_NAME_COL].apply(
            lambda n: lookup_pest_class(str(n)) is not None
        )
        if has_cas:
            pest_mask = pest_mask | df_to_env['casNumber'].apply(
                lambda c: bool(c) and str(c).strip() in _pest_cas
            )
        df_pest_rows   = df_to_env[pest_mask].copy()
        df_nonpest_env = df_to_env[~pest_mask].copy()
    else:
        df_pest_rows   = pd.DataFrame()
        df_nonpest_env = pd.DataFrame()

    print(f"  Pesticide emissions found: {len(df_pest_rows)}  |  non-pesticide env: {len(df_nonpest_env)}")

    # ── A pesticide bought but never emitted: say so ─────────────────────────
    # An emission the tool does not recognise is carried through untouched,
    # which looks exactly like the model having nothing to do.  So the
    # dataset's own pesticide INPUTS are cross-checked against what was
    # recognised.
    #
    # The check is quantitative, because the same flow name means different
    # things: "Copper ion" is the applied copper fungicide in one inventory
    # and a 1e-11 kg trace contaminant in another.  An unrecognised emission
    # is only worth reporting when it is within three orders of magnitude of
    # the amount applied -- a trace at 1e-8 of the dose is not that
    # pesticide's emission, and saying so on every dataset would be noise.
    if not df_non_env.empty and not df_to_env.empty:
        applied: dict[str, float] = {}
        applied_class: dict[str, str] = {}
        for _, row in df_non_env.iterrows():
            name = str(row.get(PESTICIDE_NAME_COL, '')).strip()
            if not name:
                continue
            cls = lookup_pest_class(name)
            if not cls:
                continue
            amount = pd.to_numeric(row.get(AMOUNT_COL), errors='coerce')
            applied[name] = applied.get(name, 0.0) + (0.0 if pd.isna(amount)
                                                      else float(amount))
            applied_class[name] = cls

        recognised = ({str(n).strip().lower()
                       for n in df_pest_rows[PESTICIDE_NAME_COL]}
                      if not df_pest_rows.empty else set())
        recognised |= {tool_name_for(n).strip().lower() for n in recognised}

        # Unrecognised emissions, with the mass of each.
        unknown = df_to_env[~df_to_env.index.isin(df_pest_rows.index)]             if not df_pest_rows.empty else df_to_env
        by_name = (unknown.assign(_a=pd.to_numeric(unknown[AMOUNT_COL],
                                                   errors='coerce').fillna(0.0))
                   .groupby(unknown[PESTICIDE_NAME_COL].astype(str))['_a'].sum()
                   if not unknown.empty else pd.Series(dtype=float))

        # Words that say what KIND of compound it is, not which element: an
        # emission is only a candidate if it shares a real chemical word with
        # the applied substance, so ammonia is never offered as the missing
        # emission of a copper fungicide.
        generic = {'oxide', 'hydroxide', 'ion', 'sulfate', 'sulphate',
                   'chloride', 'oxychloride', 'carbonate', 'acetate',
                   'hydrate', 'ii', 'iii', 'i', 'unspecified', 'compounds'}

        def chemical_words(text):
            return {w for w in re.split(r'[^a-z0-9]+', str(text).lower())
                    if w and w not in generic}

        for name, dose in sorted(applied.items()):
            if name.strip().lower() in recognised or dose <= 0:
                continue
            if name.strip().lower() not in _forbid:
                continue      # its emission is generated below, not missing
            words = chemical_words(name)
            candidates = [(n, m) for n, m in by_name.items()
                          if m > 0 and m >= dose / 1000.0
                          and chemical_words(n) & words]
            if not candidates:
                continue          # nothing of a plausible size: stay quiet
            print()
            print(f"  WARNING: '{name}' is applied here ({dose:.3g} kg) and the "
                  f"OLCApest tool classes it as '{applied_class[name]}', but no "
                  f"emission of it was redistributed.")
            print("           The dataset needs an emission row named after the "
                  "ACTIVE SUBSTANCE for")
            print("           OLCApest to act on -- the way these datasets carry "
                  "'Cupric hydroxide'.")
            print("           Similarly named emissions are listed below for "
                  "orientation only. They")
            print("           are DIFFERENT FLOWS unless you know otherwise: a "
                  "metal emission")
            print("           ('Copper ion') is not the emission of a pesticide "
                  "('Copper oxide'),")
            print("           and a synonym must never be used to tie the two "
                  "together.")
            for n, m in sorted(candidates, key=lambda c: -c[1])[:5]:
                print(f"             {n:<34} {m:.3g} kg")
            print()

    # ── Applied dose vs emitted total ────────────────────────────────────────
    # The model redistributes the EMISSION rows; it does not derive them from
    # the dose.  These datasets are built so that the whole applied mass is
    # emitted -- every substance comes out at a ratio of exactly 1.0000 -- so
    # a substance whose emission total does not match its dose means the two
    # were edited out of step, and the split is then computed from the stale
    # figure.  Changing the numbers is not this step's business; saying so is.
    # When the dose is going to drive the emission anyway (below), reporting
    # the disagreement first and correcting it second reads as contradiction.
    _dose_driven = _project_name in (OLCAPEST_DOSE_DRIVES_EMISSION or {})
    if not df_non_env.empty and not df_pest_rows.empty:
        dose: dict[tuple, float] = {}
        for _, row in df_non_env.iterrows():
            name = str(row.get(PESTICIDE_NAME_COL, '')).strip()
            if not name or lookup_pest_class(name) is None:
                continue
            amount = pd.to_numeric(row.get(AMOUNT_COL), errors='coerce')
            if pd.isna(amount):
                continue
            key = (str(row.get(CROP_COL, '')), name.lower())
            dose[key] = dose.get(key, 0.0) + float(amount)

        emitted: dict[tuple, float] = {}
        for _, row in df_pest_rows.iterrows():
            name = str(row.get(PESTICIDE_NAME_COL, '')).strip()
            amount = pd.to_numeric(row.get(AMOUNT_COL), errors='coerce')
            if pd.isna(amount):
                continue
            key = (str(row.get(CROP_COL, '')), name.lower())
            emitted[key] = emitted.get(key, 0.0) + float(amount)
            # The dataset may name the emission after the element while the
            # dose names the compound; the synonym ties the two together.
            alias = tool_name_for(name).strip().lower()
            if alias != name.lower():
                emitted[(str(row.get(CROP_COL, '')), alias)] =                     emitted.get((str(row.get(CROP_COL, '')), alias), 0.0) + float(amount)

        off = []
        for key, applied_kg in dose.items():
            if applied_kg <= 0 or key not in emitted:
                continue
            ratio = emitted[key] / applied_kg
            if abs(ratio - 1.0) > 0.01:
                off.append((key[0], key[1], applied_kg, emitted[key], ratio))
        if off and not _dose_driven:
            print()
            print("  WARNING: applied dose and emitted total disagree -- the split "
                  "is computed from the emission rows, so it follows the emitted "
                  "figure, not the dose:")
            for crop, name, applied_kg, emi, ratio in sorted(off,
                                                             key=lambda r: -abs(r[4] - 1)):
                print(f"             {name[:34]:<36} applied {applied_kg:.4g} kg, "
                      f"emitted {emi:.4g} kg  (x{ratio:.4f})  [{crop[:30]}]")
            print("           Every other dataset in this pipeline emits the whole "
                  "dose (x1.0000).")
            print("           Update the emission rows to match the dose, or say so "
                  "and the dose can drive them.")
            print()

    # ── STEP 2: sum all compartment emissions per (activity, geo, substance) ──
    # All original compartment rows (air, soil, water …) for the same pesticide
    # are summed into ONE total, then redistributed across 4 compartments via
    # the OLCApest model fractions (OLCA CALC sheet).
    results = []

    # ── A pesticide that is applied but not emitted ──────────────────────────
    # OLCApest splits emission rows.  A pesticide the dataset BUYS but never
    # EMITS is therefore invisible to it, and its impact silently disappears.
    # So wherever the mapping sheet classes an applied substance and the
    # dataset carries no emission for it, the emission is built from the dose:
    # the whole applied mass, named after the active substance, split across
    # the OLCApest compartments.  This is the default for every project.
    #
    # The one thing that must not happen is counting the same application
    # twice.  A dataset may already emit it under a DIFFERENT name -- in
    # 'ILCIDAF project full' the applied 'copper oxide' comes out as
    # 'Cupric hydroxide' -- and there the mass is the giveaway: an existing
    # pesticide emission in the same activity whose total equals this dose IS
    # this application, under another name.  Generating on top of it would
    # double the copper, so it is skipped and reported instead.
    _to_generate: list = []
    _generated_skips: list = []
    if not df_non_env.empty:
        _emitted_names = ({str(n).strip().lower()
                           for n in df_to_env[PESTICIDE_NAME_COL]}
                          if not df_to_env.empty else set())
        # Totals of the emissions OLCApest already recognises, per activity.
        _emitted_totals: dict = {}
        if not df_pest_rows.empty:
            for _, _r in df_pest_rows.iterrows():
                _a = pd.to_numeric(_r.get(AMOUNT_COL), errors='coerce')
                if pd.isna(_a):
                    continue
                _k = (str(_r.get(CROP_COL, '')), str(_r.get(PESTICIDE_NAME_COL, '')).strip())
                _emitted_totals[_k] = _emitted_totals.get(_k, 0.0) + float(_a)

        for _, _row in df_non_env.iterrows():
            _name = str(_row.get(PESTICIDE_NAME_COL, '')).strip()
            if not _name:
                continue
            _cls = lookup_pest_class(_name)
            if not _cls:
                continue                      # not a pesticide the tool knows
            if str(_name).strip().lower() in _forbid:
                continue                      # this project handles it by hand
            # The tool's spelling is the ecoinvent flow name: 'copper oxide'
            # is bought, 'Copper oxide' is emitted.
            _flow = next((k for k in pesticide_mapping
                          if str(k).strip().lower() == _name.lower()), _name)
            if str(_flow).strip().lower() in _emitted_names:
                continue                      # already emitted under its own name
            _amount = pd.to_numeric(_row.get(AMOUNT_COL), errors='coerce')
            if pd.isna(_amount) or _amount <= 0:
                continue
            _amount = float(_amount)
            _crop = str(_row.get(CROP_COL, ''))

            # Already emitted under another name?  Equal mass in the same
            # activity is the evidence.
            _alias = next((n for (c, n), tot in _emitted_totals.items()
                           if c == _crop and tot > 0
                           and abs(tot / _amount - 1.0) <= 0.01), None)
            if _alias:
                _generated_skips.append((_crop, _name, _alias, _amount))
                continue

            _to_generate.append((_crop, str(_row.get(GEOGRAPHY_COL, '')),
                                 str(_flow), _cls, _amount))

    if _generated_skips:
        print()
        for _crop, _name, _alias, _amount in _generated_skips:
            print(f"  NOTE: '{_name}' ({_amount:.4g} kg) is applied with no emission "
                  f"of its own, but '{_alias}' in the same activity emits exactly "
                  f"that mass --")
            print(f"        taken as the same application under another name, so "
                  f"nothing is generated (it would double it).")
        print()

    if not df_pest_rows.empty or _to_generate:
        has_cas = 'casNumber' in df_pest_rows.columns

        summed: dict[tuple, dict] = {}
        for _, row in df_pest_rows.iterrows():
            crop      = str(row[CROP_COL])
            geography = str(row[GEOGRAPHY_COL])
            name      = str(row[PESTICIDE_NAME_COL])
            cas       = str(row['casNumber']).strip() if has_cas else ''
            amount    = pd.to_numeric(row[AMOUNT_COL], errors='coerce')

            if pd.isna(amount):
                continue

            pest_class = lookup_pest_class(name, cas)
            if not pest_class:
                continue

            key = (crop, geography, name)
            if key not in summed:
                summed[key] = {'amount': 0.0, 'pest_class': pest_class}
            summed[key]['amount'] += amount

        # ── The dose drives the emission, where the project asks for it ──
        # Set each recognised pesticide's total to its applied amount before
        # the split, so an edited dose is what gets distributed.  Only
        # substances that are actually applied are touched, and a total that
        # already matches is left exactly alone -- a project at the usual
        # ratio of 1.0000 sees no change at all.
        _factor = (OLCAPEST_DOSE_DRIVES_EMISSION or {}).get(_project_name)
        if _factor is not None and dose:
            rescaled = []
            for key in list(summed):
                crop, _geo, name = key
                applied = dose.get((crop, str(name).strip().lower()))
                if applied is None:
                    applied = dose.get((crop, tool_name_for(name).strip().lower()))
                if applied is None or applied <= 0:
                    continue
                target = applied * float(_factor)
                before = summed[key]['amount']
                if before and abs(target / before - 1.0) <= 1e-9:
                    continue
                summed[key]['amount'] = target
                rescaled.append((name, crop, before, target))
            if rescaled:
                print()
                print(f"  Dose drives the emission for {_project_name!r} "
                      f"(factor {_factor:g}): {len(rescaled)} substance total(s) "
                      f"set from the applied amount:")
                for name, crop, before, target in rescaled:
                    print(f"      {str(name)[:28]:<30} {before:.4g} -> {target:.4g} kg"
                          f"   [{str(crop)[:34]}]")
                print()

        # The generated ones: the whole applied dose, under the active
        # substance's own name, to be split like any other emission below.
        if _to_generate:
            print()
            print(f"  Generating the emission of {len(_to_generate)} applied "
                  f"pesticide(s) from the dose ({_project_name!r}):")
            for crop, geo, flow, cls, amount in _to_generate:
                key = (crop, geo, flow)
                if key in summed:
                    continue
                summed[key] = {'amount': amount, 'pest_class': cls}
                print(f"      {flow:<24} {amount:.6g} kg  [{cls}]  {crop[:36]}")
            print()

        # ── STEP 3: split total via OLCApest fractions into 4 compartments ───
        for (crop, geography, name), info in summed.items():
            total_amount = info['amount']
            pest_class   = info['pest_class']

            class_c = get_class_C(crop)
            if class_c:
                pest_class = adjust_pest_class(class_c, pest_class)

            matched_geo = get_matching_geography(geography)

            alloc = pd.DataFrame()
            if class_c:
                alloc = lookup_allocation(class_c, pest_class, matched_geo)

            if alloc.empty or class_c is None:
                # Fallback: assign everything to agricultural soil
                results.append({
                    'Crop':          crop,
                    'Geography':     geography,
                    'Pesticide':     name,
                    'Pest_class':    pest_class,
                    'Compartment':   'soil',
                    'Subcompartment': AGRICULTURAL_SUBC,
                    'Amount':        total_amount,
                })
                continue

            # Normalize fractions so they sum to exactly 1.0
            raw_fracs = {subc: alloc.iloc[0].get(subc) for subc in COMPARTMENT_COLS}
            valid_fracs = {k: v for k, v in raw_fracs.items()
                           if v is not None and not pd.isna(v) and v > 0}
            frac_total = sum(valid_fracs.values())
            norm_fracs = {k: v / frac_total for k, v in valid_fracs.items()} if frac_total > 0 else valid_fracs

            # One row per compartment: amount = total * normalised fraction
            for subc in COMPARTMENT_COLS:
                fraction = norm_fracs.get(subc)
                if fraction is None:
                    continue
                results.append({
                    'Crop':          crop,
                    'Geography':     geography,
                    'Pesticide':     name,
                    'Pest_class':    pest_class,
                    'Compartment':   COMPARTMENT_MAP[subc],
                    'Subcompartment': subc,
                    'Amount':        total_amount * fraction,
                })

    # ── Build redistributed emission rows ─────────────────────────────────────
    df_results = pd.DataFrame(results)
    if not df_results.empty:
        df_clean = pd.DataFrame(index=df_results.index, columns=CLEAN_COLS)
        df_clean['activityName']   = df_results['Crop'].values
        df_clean['geography']      = df_results['Geography'].values
        df_clean['group']          = 'ToEnvironment'
        df_clean['name']           = df_results['Pesticide'].values
        df_clean['compartment']    = df_results['Compartment'].values
        df_clean['subcompartment'] = df_results['Subcompartment'].values
        df_clean['unitName']       = 'kg'
        df_clean['amount']         = df_results['Amount'].values
        df_clean['uncertainty type'] = UNCERTAINTY_TYPE
        df_clean['variance']       = [
            get_variance(pc, sc)
            for pc, sc in zip(df_results['Pest_class'].values, df_results['Subcompartment'].values)
        ]
        df_clean['reliability']             = DQS_RELIABILITY
        df_clean['completeness']            = DQS_COMPLETENESS
        df_clean['temporalCorrelation']     = DQS_TEMPORAL
        df_clean['geographicalCorrelation'] = DQS_GEOGRAPHICAL
        df_clean['furtherTechnologyCorrelation'] = DQS_TECHNOLOGY
        df_clean['comment'] = (
            f'Automatic update {datetime.date.today().year}: recalculated according to the OLCA-Pest model '
            '(Nemecek et al. 2022). Total pesticide amount split across air/soil/water compartments '
            'using OLCApest correction factors.'
        )
        n_subst = len(summed)
        n_rows  = len(df_results)
        print(f"\nOK Redistributed {len(df_pest_rows)} pesticide emission rows "
              f"-> {n_rows} compartment row(s) for {n_subst} unique substance(s).")
        print(df_results[['Crop', 'Geography', 'Pesticide', 'Subcompartment', 'Amount']].head(20).to_string())
    else:
        df_clean = pd.DataFrame(columns=CLEAN_COLS)
        print("WARNING: No pesticide emissions redistributed (no matching substances found in OLCApest tool).")

    # ── Check: allocated emissions sum to the applied input ──────────────────
    # Whatever the dataset looked like going in, what OLCApest hands on must
    # account for the whole dose: for every pesticide bought FromTechnosphere,
    # the redistributed emissions of it add up to the amount bought.  A
    # shortfall means part of the dose is emitted under a flow the tool does
    # not recognise -- 'Copper ion, air' carrying 9% of a copper oxide dose,
    # say -- and passed through unsplit.
    check_pesticide_mass_balance(
        df_non_env, df_results, lookup_pest_class, tool_name_for,
        _generated_skips, (OLCAPEST_DOSE_DRIVES_EMISSION or {}).get(_project_name))

    # ── Assemble output exchanges ─────────────────────────────────────────────
    # Layout: non-env rows + non-pesticide env rows + redistributed pesticide emissions
    # (original pesticide emission rows are REPLACED by the redistributed ones)
    all_cols = list(df_non_env.columns)
    for col in list(df_nonpest_env.columns) + list(df_clean.columns):
        if col not in all_cols:
            all_cols.append(col)

    df_exchanges_out = pd.concat(
        [
            df_non_env.reindex(columns=all_cols),
            df_nonpest_env.reindex(columns=all_cols),
            df_clean.reindex(columns=all_cols).dropna(how='all'),
        ],
        ignore_index=True,
    )

    # The pesticide emission rows the dataset came with are gone from the
    # output: listed, with their amounts, in the project's deletion log.
    if not df_pest_rows.empty:
        record_deletions(_output_path, 'OLCApest', 'exchanges', df_pest_rows,
                         'pesticide emission replaced by the OLCA-Pest emissions')

    # ── Save (all 5 sheets, pesticide rows replaced) ──────────────────────────
    OUTPUT_SHEETS = ['meta', 'review', 'exchanges', 'properties', 'parameters']
    # Every sheet with its full header row, however empty.
    complete_sheet_schema(all_input_sheets)
    try:
        with pd.ExcelWriter(_output_path, engine='openpyxl') as writer:
            for sheet in OUTPUT_SHEETS:
                if sheet == 'exchanges':
                    df_exchanges_out.to_excel(writer, sheet_name=sheet, index=False)
                elif sheet == 'review':
                    src = all_input_sheets.get(sheet, pd.DataFrame())
                    if src.columns.empty:
                        src = pd.DataFrame(columns=REVIEW_COLS)
                    src.to_excel(writer, sheet_name=sheet, index=False)
                else:
                    src = all_input_sheets.get(sheet, pd.DataFrame())
                    src.to_excel(writer, sheet_name=sheet, index=False)
    except PermissionError:
        print(f"\nERROR: Cannot write to '{_output_path}' -- close it in Excel and re-run.")
        return False

    print(f"\nOK Output saved to '{_output_path}'")
    write_spold_for(_output_path)
    print(f"  exchanges: {len(df_non_env)} non-env + {len(df_nonpest_env)} non-pest env "
          f"+ {len(df_clean.dropna(how='all'))} redistributed pest emissions "
          f"= {len(df_exchanges_out)} total")


if __name__ == "__main__":
    import argparse as _ap, json as _json, pathlib as _pl
    _parser = _ap.ArgumentParser()
    _parser.add_argument('--batch', default=None,
                         help='JSON list of [project, filename] pairs')
    _parser.add_argument('--from-output', dest='from_output', action='store_true',
                         help='read the working copy in output/ (prepared by the '
                              'meta model) instead of input datasets/')
    _args, _ = _parser.parse_known_args()

    if _args.batch:
        _batch   = _json.loads(_args.batch)
        _BASE    = _ROOT
        _failed  = []
        print(f'[OLCApest BATCH] {len(_batch)} datasets -- loading tool once...')
        _tool  = load_tool_data(TOOL_FILEPATH)
        for _project, _filename in _batch:
            _stem = _pl.Path(_filename).stem
            _inp  = os.path.join(_BASE, 'input datasets', _project, _filename)
            _out  = os.path.join(_BASE, 'output', _project, _stem + '.xlsx')
            # In a pipeline run the working copy was just started afresh from
            # the input and corrected by the meta model (exchange names above
            # all), so that is what the models build on.
            if _args.from_output and os.path.isfile(_out):
                _inp = _out
            print(f'\n--- {_filename} ---')
            try:
                ok = main(tool_data=_tool, _input_path=_inp, _output_path=_out)
                if ok is False:
                    _failed.append(_filename)
            except Exception as _exc:
                print(f'  ERROR processing {_filename}: {_exc}')
                _failed.append(_filename)
        if _failed:
            print(f'\n[OLCApest BATCH] WARNING: {len(_failed)} file(s) skipped: {_failed}')
            print(f'[OLCApest BATCH] {len(_batch) - len(_failed)}/{len(_batch)} datasets completed.')
        else:
            print(f'\n[OLCApest BATCH] All {len(_batch)} datasets completed.')
    else:
        main()

