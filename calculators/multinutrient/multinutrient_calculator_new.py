"""
Multinutrient Calculator for New Datasets

Reads the OLCApest new-datasets output (full dataset format: meta, review, exchanges,
properties, parameters sheets) and splits every multinutrient fertiliser row into
separate per-nutrient rows (N / P2O5 / K2O).

PIPELINE POSITION:
    Step 2 of 3  — runs after calculators/olcapest/read_pesticide_data_new.py

INPUT:
    calculators/olcapest/output_files/olcapest_new_output.xlsx
    (full dataset format with sheets: meta, review, exchanges, properties, parameters)

PROCESSING:
    1. Load all sheets from the OLCApest output
    2. Split multinutrient fertiliser exchange rows into per-nutrient rows
       (amount = original_amount × allocation_factor)
    3. Clear residual N / P2O5 / K2O variableNames and mathematicalRelations
    4. Delete properties from multinutrient exchange rows that were split
    5. Save all sheets to the output file

OUTPUT:
    output_files/multinutrient_new_output.xlsx

Author: Andreas Giakoumatos
Last Updated: 2026-07-29
"""

import os
import sys
import json
import pandas as pd

# See agri_diesel_calculator_new.py: never let a print() of arbitrary source
# data (unencodable on Windows' console codepage) abort real work.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')

# ── Configuration (paths and shared data from central config) ─────────────────
# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
from config_new_datasets import (
    OLCAPEST_OUTPUT_FILE      as INPUT_FILEPATH,
    MULTINUTRIENT_OUTPUT_FILE as OUTPUT_FILEPATH,
    ALLOCATION_FACTORS,
    NUTRIENT_REFERENCE_PRODUCTS,
    MASTER_DATA_LOOKUP_FILE,
    REVIEW_COLS,
    complete_sheet_schema,
    record_deletions,
    write_spold_for,
)

# ── Master data (intermediateExchangeId / unitId) for the per-fertiliser
# N/P2O5/K2O flows, keyed by the FULL flow name -- e.g. "inorganic nitrogen
# fertiliser, as N, from NPK (15-15-15) fertiliser" -- since each source
# fertiliser has its own distinct master-data id, not a shared generic one.
if os.path.isfile(MASTER_DATA_LOOKUP_FILE):
    with open(MASTER_DATA_LOOKUP_FILE, encoding='utf-8') as _fh:
        _MASTER_DATA = json.load(_fh)
else:
    _MASTER_DATA = {}


def _nutrient_master_data(full_name: str) -> dict:
    """Return {'intermediateExchangeId', 'unitId'} for a nutrient flow name, or {} if unknown."""
    entry = _MASTER_DATA.get(full_name)
    if not entry:
        return {}
    return {'intermediateExchangeId': entry['intermediateExchangeId'], 'unitId': entry['unitId']}

# ── Uncertainty / DQS of the per-nutrient rows ────────────────────────────────
# The values the multinutrient project scored these flows with -- the same for
# every one of them (combined_models/input_files/inorganic_exchanges_meta.xlsx,
# which the original multinutrient_calculator.py copied onto every row it
# made).  They replace whatever the source fertiliser row carried: a split
# row is a new flow, scored as such, not the provider's mixed fertiliser.
MULTINUTRIENT_DQS = {
    'uncertainty type':             'lognormal',
    'variance':                     0.0006,
    'reliability':                  1,
    'completeness':                 4,
    'temporalCorrelation':          1,
    'geographicalCorrelation':      1,
    'furtherTechnologyCorrelation': 1,
}


def _fill_dqs(row: pd.Series) -> pd.Series:
    """The split row's uncertainty and pedigree (MULTINUTRIENT_DQS)."""
    for column, value in MULTINUTRIENT_DQS.items():
        row[column] = value
    return row


# Strict lookup: exchange name without " production" suffix → allocation factors
_STRICT_LOOKUP = {
    key.replace(' production', '').strip(): factors
    for key, factors in ALLOCATION_FACTORS.items()
}

_PROPERTY_EXCHANGE_KEY_COLUMNS = [
    'activityName', 'geography', 'group', 'name', 'compartment', 'subcompartment',
    'activityLink_activityName', 'activityLink_geography',
]


def _allocation_factors_for_name(name):
    clean_name = str(name or '').strip()
    return _STRICT_LOOKUP.get(clean_name) or _STRICT_LOOKUP.get(clean_name.replace(' production', '').strip())


def _key_value(value):
    return '' if pd.isna(value) else str(value).strip()


def remove_split_multinutrient_properties(properties_df: pd.DataFrame,
                                          original_exchanges_df: pd.DataFrame,
                                          removed: list | None = None) -> tuple[pd.DataFrame, int]:
    """Remove property rows belonging to multinutrient exchanges that were
    split.  The removed rows are appended to `removed`, when given."""
    if properties_df.empty or original_exchanges_df.empty:
        return properties_df.copy(), 0

    key_columns = [
        col for col in _PROPERTY_EXCHANGE_KEY_COLUMNS
        if col in properties_df.columns and col in original_exchanges_df.columns
    ]
    if not key_columns:
        return properties_df.copy(), 0

    split_keys = {
        tuple(_key_value(row.get(col)) for col in key_columns)
        for _, row in original_exchanges_df.iterrows()
        if _allocation_factors_for_name(row.get('name', ''))
    }
    if not split_keys:
        return properties_df.copy(), 0

    remove_mask = properties_df.apply(
        lambda row: tuple(_key_value(row.get(col)) for col in key_columns) in split_keys,
        axis=1,
    )
    if removed is not None and remove_mask.any():
        removed.append(properties_df.loc[remove_mask])
    return properties_df.loc[~remove_mask].reset_index(drop=True), int(remove_mask.sum())


# ── Processing functions ──────────────────────────────────────────────────────

def split_multinutrient_exchanges(exchanges_df: pd.DataFrame) -> pd.DataFrame:
    """
    Replace each multinutrient fertiliser row with per-nutrient rows.
    Amount   = original_amount * allocation_factor
    Name     = '<nutrient reference product>, from <fertiliser name>'
    mathematicalRelation and variableName are cleared (amount is now explicit).
    Uncertainty and pedigree are always set (see MULTINUTRIENT_DQS).
    """
    result_rows = []
    for _, row in exchanges_df.iterrows():
        name    = str(row.get('name', '')).strip()
        factors = _allocation_factors_for_name(name)

        if not factors:
            result_rows.append(row)
            continue

        original_amount = row['amount']
        clean_name      = name.replace(' production', '')

        for nutrient, factor in factors.items():
            if factor == 0:
                continue
            new_row = row.copy()
            base             = NUTRIENT_REFERENCE_PRODUCTS[nutrient]
            full_name        = f'{base}, from {clean_name}'
            new_row['name']  = full_name
            new_row['amount'] = round(original_amount * factor, 10)
            for _k, _v in _nutrient_master_data(full_name).items():
                new_row[_k] = _v
            if 'mathematicalRelation' in new_row.index:
                new_row['mathematicalRelation'] = None
            if 'variableName' in new_row.index:
                new_row['variableName'] = None
            prefix = (
                'The amount of the compound reported in this specific flow '
                'corresponds to its share from the original multinutrient composition.'
            )
            existing_comment = (
                str(new_row['comment'])
                if 'comment' in new_row.index and pd.notna(new_row.get('comment'))
                else ''
            )
            new_row['comment'] = f"{prefix} {existing_comment}".strip() if existing_comment else prefix
            result_rows.append(_fill_dqs(new_row))

    return pd.DataFrame(result_rows).reset_index(drop=True)


def main(_input_path=None, _output_path=None):
    _input_path  = _input_path  or INPUT_FILEPATH
    _output_path = _output_path or OUTPUT_FILEPATH
    os.makedirs(os.path.dirname(_output_path), exist_ok=True)

    # 1. Load all sheets from OLCApest output
    print("\n1. Loading OLCApest new-datasets output...")
    merged_data = pd.read_excel(_input_path, sheet_name=None)
    print(f"   Sheets found: {list(merged_data.keys())}")

    # 2. Split multinutrient fertiliser rows into per-nutrient rows
    print("\n2. Splitting multinutrient fertiliser exchanges into per-nutrient rows...")
    df_exchanges_original = merged_data.get('exchanges', pd.DataFrame()).copy()
    before_split  = len(df_exchanges_original)
    df_exchanges  = split_multinutrient_exchanges(df_exchanges_original)
    after_split   = len(df_exchanges)
    print(f"   Exchanges: {before_split} rows -> {after_split} rows ({after_split - before_split:+d})")

    # 3. Clear residual N, P2O5, K2O variable names and equations
    print("\n3. Clearing residual N, P2O5, K2O variables and equations...")
    # Columns may be all-NaN (float64) when OLCApest left them empty, or may
    # contain a stray numeric value (e.g. a literal 0.0) in an otherwise
    # text column -- either shape makes the plain dtype!=object cast
    # insufficient, since .str accessor requires every non-null value to
    # actually BE a string. Force that explicitly instead of assuming it.
    for _col in ('variableName', 'mathematicalRelation'):
        if _col in df_exchanges.columns:
            df_exchanges[_col] = df_exchanges[_col].apply(
                lambda v: v if isinstance(v, str) else (None if pd.isna(v) else str(v))
            )
    variables_to_clear = ['N', 'P2O5', 'K2O', 'amount_inorganic_N_fert']
    variables_cleared  = 0
    equations_cleared  = 0

    if 'variableName' in df_exchanges.columns:
        var_mask = df_exchanges['variableName'].isin(variables_to_clear)
        df_exchanges.loc[var_mask, 'variableName'] = ''
        variables_cleared = var_mask.sum()

    if 'mathematicalRelation' in df_exchanges.columns:
        for var in variables_to_clear:
            pattern  = rf'\b{var}\b'
            eq_mask  = df_exchanges['mathematicalRelation'].str.contains(
                pattern, case=False, na=False, regex=True
            )
            df_exchanges.loc[eq_mask, 'mathematicalRelation'] = ''
            equations_cleared += eq_mask.sum()

    print(f"   Cleared {variables_cleared} variableNames")
    print(f"   Cleared {equations_cleared} mathematicalRelations")

    # 4. Remove properties that belonged to exchange rows replaced by the split.
    print("\n4. Removing properties from split multinutrient exchanges...")
    df_properties = merged_data.get('properties', pd.DataFrame()).copy()
    removed_properties: list = []
    df_properties, properties_removed = remove_split_multinutrient_properties(
        df_properties,
        df_exchanges_original,
        removed_properties,
    )
    # What the split took out, in the project's deletion log: each mixed
    # fertiliser row (now its N / P2O5 / K2O rows) and its properties.
    if not df_exchanges_original.empty and 'name' in df_exchanges_original.columns:
        record_deletions(
            _output_path, 'Multinutrient', 'exchanges',
            df_exchanges_original[df_exchanges_original['name'].map(
                lambda n: bool(_allocation_factors_for_name(n)))],
            'multinutrient fertiliser replaced by its N / P2O5 / K2O rows')
    for frame in removed_properties:
        record_deletions(_output_path, 'Multinutrient', 'properties', frame,
                         'property of a multinutrient fertiliser that was split')
    print(f"   Removed {properties_removed} property row(s)")

    # 5. Save output preserving all sheets
    print("\n5. Saving output...")
    all_sheets = ['meta', 'review', 'exchanges', 'properties', 'parameters']
    # Every sheet with its full header row, however empty.
    _schema = complete_sheet_schema({'review': merged_data.get('review'),
                                     'properties': df_properties,
                                     'parameters': merged_data.get('parameters')})
    merged_data = {**merged_data, 'review': _schema['review'],
                   'parameters': _schema['parameters']}
    df_properties = _schema['properties']
    with pd.ExcelWriter(_output_path, engine='openpyxl') as writer:
        for sheet in all_sheets:
            if sheet == 'exchanges':
                df_exchanges.to_excel(writer, sheet_name=sheet, index=False)
            elif sheet == 'properties':
                df_properties.to_excel(writer, sheet_name=sheet, index=False)
            elif sheet == 'review':
                src = merged_data.get(sheet, pd.DataFrame())
                if src.columns.empty:
                    src = pd.DataFrame(columns=REVIEW_COLS)
                src.to_excel(writer, sheet_name=sheet, index=False)
            else:
                src = merged_data.get(sheet, pd.DataFrame())
                src.to_excel(writer, sheet_name=sheet, index=False)

    print(f"   Saved to: {_output_path}")
    write_spold_for(_output_path)
    print("\n" + "=" * 80)
    print("MULTINUTRIENT (NEW DATASETS) COMPLETED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    import argparse as _ap, json as _json, pathlib as _pl
    _parser = _ap.ArgumentParser()
    _parser.add_argument('--batch', default=None)
    _args, _ = _parser.parse_known_args()

    if _args.batch:
        _batch  = _json.loads(_args.batch)
        _BASE   = _ROOT
        _failed = []
        print(f'[Multinutrient BATCH] {len(_batch)} datasets')
        for _project, _filename in _batch:
            _stem = _pl.Path(_filename).stem
            _f    = os.path.join(_BASE, 'output', _project, _stem + '.xlsx')
            print(f'\n--- {_filename} ---')
            try:
                ok = main(_input_path=_f, _output_path=_f)
                if ok is False:
                    _failed.append(_filename)
            except Exception as _exc:
                print(f'  ERROR processing {_filename}: {_exc}')
                _failed.append(_filename)
        if _failed:
            print(f'\n[Multinutrient BATCH] WARNING: {len(_failed)} file(s) skipped: {_failed}')
            print(f'[Multinutrient BATCH] {len(_batch) - len(_failed)}/{len(_batch)} datasets completed.')
        else:
            print(f'\n[Multinutrient BATCH] All {len(_batch)} datasets completed.')
    else:
        main()
