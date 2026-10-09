"""
Build the Master Data flow index the meta model's checklist checks use.

From an ecoinvent MasterData workbook (sheets IntermediateExchanges,
ElementaryExchanges, IntermediateExchanges prop.) it writes one JSON file:

    intermediate   {name: {unitName, cpc, byproduct}}
    elementary     {"name|compartment|subcompartment": unitName}
    properties     {name: {propertyName: {amount, unitName}}}   (defining values)

These answer the checklist rows the other lookups cannot: is the unit the one
Master Data defines for this flow (Checks 96, agri 20), and does a stated
property deviate from the Master Data default (Checks 96, agri 24).

Run once per ecoinvent release:
    python calculators/agri_diesel/build_master_data_index.py            # default workbook
    python calculators/agri_diesel/build_master_data_index.py <MasterData.xlsx>
"""

import json
import os
import sys
from datetime import date

import pandas as pd

# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
from config_new_datasets import MASTER_DATA_XLSX, MASTER_DATA_FLOW_INDEX_FILE


def _text(value) -> str:
    return '' if pd.isna(value) else str(value).strip()


def build(source: str) -> dict:
    intermediate = {}
    frame = pd.read_excel(source, sheet_name='IntermediateExchanges', dtype=object)
    for _, row in frame.iterrows():
        name = _text(row.get('name'))
        if name:
            intermediate[name] = {'unitName': _text(row.get('unitName')),
                                  'cpc': _text(row.get('CPC')),
                                  'byproduct': _text(row.get('By-product classification'))}

    elementary = {}
    frame = pd.read_excel(source, sheet_name='ElementaryExchanges', dtype=object)
    for _, row in frame.iterrows():
        name = _text(row.get('name'))
        if name:
            key = f"{name}|{_text(row.get('compartment'))}|{_text(row.get('subcompartment'))}"
            elementary[key] = _text(row.get('unitName'))

    properties: dict = {}
    frame = pd.read_excel(source, sheet_name='IntermediateExchanges prop.', dtype=object)
    for _, row in frame.iterrows():
        name, prop = _text(row.get('name')), _text(row.get('propertyName'))
        if not name or not prop or pd.isna(row.get('amount')):
            continue
        try:
            amount = float(row.get('amount'))
        except (TypeError, ValueError):
            continue
        properties.setdefault(name, {})[prop] = {'amount': amount,
                                                 'unitName': _text(row.get('unitName'))}

    return {'_source': os.path.basename(source), '_built': date.today().isoformat(),
            'intermediate': intermediate, 'elementary': elementary,
            'properties': properties}


if __name__ == '__main__':
    source = sys.argv[1] if len(sys.argv) > 1 else MASTER_DATA_XLSX
    index = build(source)
    with open(MASTER_DATA_FLOW_INDEX_FILE, 'w', encoding='utf-8') as fh:
        json.dump(index, fh, ensure_ascii=False, sort_keys=True)
    print(f"{len(index['intermediate'])} intermediate, {len(index['elementary'])} elementary, "
          f"{len(index['properties'])} products with properties -> {MASTER_DATA_FLOW_INDEX_FILE}")
