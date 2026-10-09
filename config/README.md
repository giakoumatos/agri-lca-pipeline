# config/ — the reference files

Everything the programs **read** but that is neither code nor a project's data.
Each path is defined once, in `config_new_datasets.py` (`CONFIG_DIR` and the
constants built on it); no program names a file here on its own.

Replace a file here, not the code, when its source changes — and rebuild the
generated ones with the script named below after a new ecoinvent release.

## master_data/ — ecoinvent Master Data

What a dataset must link to: real ecoinvent ids and names, never made up
(an unknown id is rejected by ecoEditor and by the database import).

| File | What it holds | Comes from / rebuilt by | Used by |
|---|---|---|---|
| `master_data_flow_index.json` | Per flow: unit, CPC, by-product class, the properties Master Data defines | `calculators/agri_diesel/build_master_data_index.py` from `MASTER_DATA_XLSX` | checks (units, properties — Master Data is the standard), names |
| `master_data_lookup.json` | The custom flows (diesel used in …, N / P2O5 / K2O from a fertiliser): ids, unit, CPC | maintained by hand — add a new custom flow here | Multinutrient, Agri Diesel, converter, checks |
| `master_data_classifications.json` | The classification blocks (by-product class, CPC) of those custom flows | maintained by hand, with the line above | converter |
| `standard_intermediate_exchange_lookup.json` | intermediateExchangeId by product name | Master Data | converter, verify |
| `standard_elementary_exchange_lookup.json` | elementaryExchangeId by name + compartment + subcompartment | Master Data | converter, verify |
| `subcompartment_id_lookup.json` | subcompartmentId per compartment / subcompartment | Master Data | converter, verify |
| `unit_id_lookup.json` | unitId per unit | Master Data | converter, verify |
| `property_id_lookup.json` | propertyId per property | Master Data | converter |
| `property_defaults_lookup.json` | Default property values per product, where Master Data has none | `calculators/agri_diesel/build_property_defaults.py` from `PROPERTIES_EXPORT_FILE` | meta model |
| `geography_id_lookup.json` | geographyId per shortname | Master Data | converter |
| `geography_name_lookup.json` | Geography name → shortname ("Mongolia" → "MN") | Master Data, Geographies sheet | meta model |
| `isic_classification_lookup.json` | classificationId per ISIC rev.4 value | Master Data | converter |
| `person_id_lookup.json` | personId per data provider / entry person | Master Data | converter |

## models/ — the calculators' reference data

| File | What it holds | Used by |
|---|---|---|
| `OLCApest mapping tool_AG_updated.xlsx` | The OLCA-Pest tool: pesticide classes, crop classes, emission shares | OLCApest |
| `…_olcapest_cache.pkl` | A cache of the tool, rebuilt automatically when the tool is newer — delete it to force a reload | OLCApest |
| `fo_geography_map.csv` | Which field-operation geography each crop geography uses (updated by Agri Diesel) | Agri Diesel |
| `price_converter_model.json` | Exchange rates and deflators to convert a price to EUR2005 (from the checkdataset skill) | meta model |

## review/ — the review

| File | What it holds | Used by |
|---|---|---|
| `agri_internal_review_checklist.xlsx` | The team's internal review checklist (Checks, agri related checks). "MD" in it is Master Data | the review workbook |
| `meta_model_decisions.json` | Every answer the meta model remembers and reuses: the same issue is never asked twice, in any project. Delete an entry to be asked again | meta model |

The remembered decisions are team knowledge, shared by every project, which is
why they live here and not among the outputs: clearing `output/` must not
forget them.
