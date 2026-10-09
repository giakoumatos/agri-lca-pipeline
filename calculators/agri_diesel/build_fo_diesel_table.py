"""
FO Diesel Intensity Table Builder  (maintenance script -- NOT part of the pipeline)

Regenerates the FO_DIESEL_INTENSITIES table in config_new_datasets.py from the
raw ecoinvent exchanges dump.  Run this ONLY when the underlying ecoinvent
release changes; the pipeline itself reads the generated table and never opens
the 87 MB exchanges file.

    python calculators/agri_diesel/build_fo_diesel_table.py            # preview
    python calculators/agri_diesel/build_fo_diesel_table.py --write    # patch config

Two source files, both from the Fuel Project:

  exchanges (3.12, undef).xlsx   every dataset exchange; rows whose 'exchange
                                 name' contains 'diesel' give kg diesel per 1 ha
                                 of the consuming activity.
  FO UND.xlsx                    the authoritative list of field-operation
                                 datasets.  Used as a whitelist: without it the
                                 'diesel' filter also picks up ~3,950 non-FO
                                 activities ('apple production', 'airport
                                 construction', ...) which are live targets for
                                 the partial-name matcher in the calculator.

Geographies sharing an identical intensity collapse into ONE row, with the
geography codes pipe-joined ('CH|GLO|RoW').  The calculator expands them back
into per-geography keys at load time, so the geography fallback chain is
unaffected.

Author: Andreas Giakoumatos
Last Updated: 2026-09-09
"""

import os
import re
import sys
import argparse
import collections

import pandas as pd

# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
from config_new_datasets import (
    FO_EXCHANGES_DB_FILE,
    FO_DATASETS_LIST_FILE,
    FO_DATASETS_LIST_SHEET,
    BASE_DIR,
)

CONFIG_FILE = os.path.join(BASE_DIR, 'config_new_datasets.py')

# Sentinels bounding the generated block in config_new_datasets.py.  Everything
# between them is replaced wholesale by --write; everything outside is untouched.
BEGIN_MARK = '# BEGIN GENERATED: FO_DIESEL_INTENSITIES'
END_MARK   = '# END GENERATED: FO_DIESEL_INTENSITIES'

# Intensities equal to this many significant digits are treated as the same
# value and collapsed onto one row.
_ROUND_SIG = 12

# ── Relevance filters ─────────────────────────────────────────────────────────
# Only activities whose OWN reference product is measured in hectares belong in
# the table.  A crop dataset expresses its field-operation inputs as ha of FO
# per kg of product, and the calculator only ever matches ha exchanges, so an
# activity measured in anything else can never legitimately be matched:
#   irrigation, drip / sprinkler / surface        m3 of water
#   water pump operation, diesel                  MJ
#   transport, freight, tractor and trailer       metric ton*km
#   market for cottonseed / vinasse / manure ...  kg
# Their diesel is real but it is not diesel per hectare of field work, so
# pairing it with an ha amount would produce a meaningless number.
REF_PRODUCT_UNIT = 'ha'

# The diesel input itself must be actual machinery diesel in kg.  Selecting rows
# by the substring 'diesel' alone also matches freight flows whose NAME merely
# contains the word -- 'transport, freight, lorry, diesel, unspecified' and
# 'transport, freight, tractor and trailer, diesel, agricultural' -- whose
# amounts are metric ton*km, not kg.  Those produced four bogus entries
# ('market for green manure, swiss integrated production, until january' and its
# three siblings), where a 0.058 tkm transport amount was being used as though
# it were 0.058 kg of diesel per hectare.
DIESEL_EXCHANGE_NAMES = {'diesel'}
DIESEL_EXCHANGE_UNIT  = 'kg'


def load_fo_whitelist() -> set[tuple[str, str]]:
    """Return {(fo_name_lower, geography)} for every real field-operation dataset."""
    df = pd.read_excel(FO_DATASETS_LIST_FILE, sheet_name=FO_DATASETS_LIST_SHEET,
                       usecols=['Activity Name', 'Geography'])
    names = df['Activity Name'].astype(str).str.strip().str.lower()
    geos  = df['Geography'].astype(str).str.strip()
    return set(zip(names, geos))


def load_diesel_exchanges() -> tuple[dict[tuple[str, str], float], dict[str, int]]:
    """
    Return ({(activity_name_lower, geography): kg_diesel_per_ha}, stats).

    Keeps only machinery-diesel inputs (DIESEL_EXCHANGE_NAMES / _UNIT) belonging
    to activities whose reference product is measured in REF_PRODUCT_UNIT.  When
    a pair has several such rows the largest is kept -- the rule the calculator
    has always used.
    """
    print(f"   Reading {os.path.basename(FO_EXCHANGES_DB_FILE)} (this takes a minute)...")
    df = pd.read_excel(FO_EXCHANGES_DB_FILE,
                       usecols=['activityName', 'geography', 'group',
                                'exchange name', 'exchange unitName', 'exchange amount'])
    df.columns = [c.strip() for c in df.columns]

    df['_name'] = df['activityName'].astype(str).str.strip().str.lower()
    df['_geo']  = df['geography'].astype(str).str.strip()
    df['_exch'] = df['exchange name'].astype(str).str.strip().str.lower()
    df['_unit'] = df['exchange unitName'].astype(str).str.strip().str.lower()

    # Activities whose reference product is measured per hectare
    is_ref  = df['group'].astype(str).str.strip() == 'ReferenceProduct'
    per_ha  = set(df.loc[is_ref & (df['_unit'] == REF_PRODUCT_UNIT), '_name'])

    loose   = df[df['_exch'].str.contains('diesel')]
    diesel  = df[df['_exch'].isin(DIESEL_EXCHANGE_NAMES) &
                 (df['_unit'] == DIESEL_EXCHANGE_UNIT) &
                 df['_name'].isin(per_ha)].copy()
    diesel['exchange amount'] = pd.to_numeric(diesel['exchange amount'], errors='coerce')
    diesel = diesel[diesel['exchange amount'].notna() & (diesel['exchange amount'] > 0)]

    intensities: dict[tuple[str, str], float] = {}
    for _, row in diesel.iterrows():
        k   = (row['_name'], row['_geo'])
        val = float(row['exchange amount'])
        if k not in intensities or val > intensities[k]:
            intensities[k] = val

    stats = {
        'loose_pairs': len({(n, g) for n, g in zip(loose['_name'], loose['_geo'])}),
        'per_ha_activities': len(per_ha),
        'kept_pairs': len(intensities),
    }
    return intensities, stats


def collapse(intensities: dict[tuple[str, str], float],
             whitelist: set[tuple[str, str]]) -> dict[tuple[str, str], float]:
    """
    Keep only whitelisted field operations, then collapse geographies that share
    an identical intensity into a single pipe-joined key.

    Returns {(fo_name, 'GEO|GEO|...'): intensity}.
    """
    per_fo: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for (name, geo), val in intensities.items():
        if (name, geo) in whitelist:
            per_fo[name][geo] = val

    table: dict[tuple[str, str], float] = {}
    for name, by_geo in per_fo.items():
        # Group on the rounded value so intensities that differ only in float
        # noise still collapse, but store the unrounded original -- the stored
        # number must stay bit-identical to the source exchange amount.
        by_val: dict[float, list[str]] = collections.defaultdict(list)
        for geo, val in sorted(by_geo.items()):
            by_val[float('%.*g' % (_ROUND_SIG, val))].append(geo)
        for rounded, geos in by_val.items():
            table[(name, '|'.join(geos))] = by_geo[geos[0]]
    return table


def render(table: dict[tuple[str, str], float], n_pairs: int) -> str:
    """Render the table as the generated config block."""
    rows = sorted(table.items(), key=lambda kv: (kv[0][0], kv[0][1]))

    lines = [
        BEGIN_MARK,
        '# Diesel intensity (kg diesel per 1 ha) for every field operation, keyed by',
        '# (fo_name, geographies).  Geographies sharing the same intensity are collapsed',
        '# onto one row and pipe-joined; the calculator expands them back into one key',
        '# per geography at load time, so the geography fallback chain is unaffected.',
        '#',
        f'# {len(rows)} rows covering {n_pairs} (field operation, geography) pairs.',
        '#',
        '# Only field operations with a RELEVANT diesel input are listed: the activity',
        "# must be measured per hectare and its diesel input must be machinery diesel in",
        '# kg.  That excludes irrigation and water pumping (m3 / MJ), agricultural',
        '# transport (metric ton*km) and the "market for ..." activities, whose diesel is',
        '# real but is not diesel per hectare of field work.',
        '#',
        '# Generated by calculators/agri_diesel/build_fo_diesel_table.py from',
        f'#   {os.path.basename(FO_EXCHANGES_DB_FILE)}  (diesel exchange amounts)',
        f'#   {os.path.basename(FO_DATASETS_LIST_FILE)}  (field-operation whitelist)',
        '# Re-run that script with --write after an ecoinvent release.  Hand edits to',
        '# individual values survive only until the next regeneration, so prefer',
        '# correcting the source data.',
        'FO_DIESEL_INTENSITIES = {',
    ]
    for (name, geos), val in rows:
        # repr() of a float round-trips exactly; a %g format would silently
        # truncate values such as 8.80598123140239 and shift the computed
        # diesel amounts in the last digits.
        lines.append(f'    ({repr(name)}, {repr(geos)}): {repr(val)},')
    lines.append('}')
    lines.append(END_MARK)
    return '\n'.join(lines) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true',
                        help='patch the generated block into config_new_datasets.py')
    args = parser.parse_args()

    for path in (FO_EXCHANGES_DB_FILE, FO_DATASETS_LIST_FILE):
        if not os.path.exists(path):
            print(f"  ERROR: source file not found: {path}")
            return 1

    print("1. Loading field-operation whitelist...")
    whitelist = load_fo_whitelist()
    print(f"   {len(whitelist)} (field operation, geography) datasets.")

    print("\n2. Loading diesel exchange amounts...")
    intensities, stats = load_diesel_exchanges()
    print(f"   {stats['loose_pairs']} (activity, geography) pairs mention 'diesel' somewhere.")
    print(f"   {stats['per_ha_activities']} activities are measured per {REF_PRODUCT_UNIT}.")
    print(f"   {stats['kept_pairs']} pairs have a relevant "
          f"({'/'.join(sorted(DIESEL_EXCHANGE_NAMES))} in {DIESEL_EXCHANGE_UNIT}) diesel input.")

    print("\n3. Collapsing to the config table...")
    table   = collapse(intensities, whitelist)
    n_pairs = sum(len(g.split('|')) for _, g in table)
    n_names = len({n for n, _ in table})
    print(f"   {n_pairs} field-operation pairs over {n_names} names -> {len(table)} rows.")
    dropped = {n for n, _ in intensities} - {n for n, _ in table}
    if dropped:
        print(f"   {len(dropped)} per-{REF_PRODUCT_UNIT} activity(ies) dropped by the "
              f"{os.path.basename(FO_DATASETS_LIST_FILE)} whitelist:")
        for n in sorted(dropped):
            print(f'     - {n}')

    block = render(table, n_pairs)

    if not args.write:
        print("\n--- preview ---")
        print('\n'.join(block.splitlines()[:40]))
        print(f"\n({len(block.splitlines())} lines total)  Re-run with --write to patch the config.")
        return 0

    print(f"\n4. Patching {CONFIG_FILE}...")
    txt     = open(CONFIG_FILE, 'r', encoding='utf-8').read()
    pattern = re.compile(re.escape(BEGIN_MARK) + r'.*?' + re.escape(END_MARK) + r'[^\n]*\n',
                         re.DOTALL)
    if not pattern.search(txt):
        print(f"   ERROR: generated block markers not found in {CONFIG_FILE}.")
        print(f"   Expected a block bounded by:\n     {BEGIN_MARK}\n     {END_MARK}")
        return 1
    open(CONFIG_FILE, 'w', encoding='utf-8').write(pattern.sub(lambda _: block, txt, count=1))
    print(f"   Written: {len(table)} rows.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
