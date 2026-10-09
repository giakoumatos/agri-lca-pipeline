"""
Central Configuration — New Datasets Pipeline

Single source of truth for all file paths, allocation factors, and the pipeline
definition shared across the three new-datasets calculators:

  Step 1: calculators/olcapest/read_pesticide_data_new.py
  Step 2: calculators/multinutrient/multinutrient_calculator_new.py
  Step 3: calculators/agri_diesel/agri_diesel_calculator_new.py

To change an input/output path, update it here — the modules pick it up automatically.

Author: Andreas Giakoumatos
Last Updated: 2026-07-29
"""

import os

# ── Base directories ──────────────────────────────────────────────────────────
# Every code folder, named once:
#   calculators/   the three models, one subfolder each
#   meta_model/    the metadata, nomenclature and Master Data checks, and the review
#   spold/         EcoSpold2 in (reader) and out (converter)
#   pipeline/      the runner's parts: steps, chooser, split edits, verify
BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
CALCULATORS_DIR   = os.path.join(BASE_DIR, 'calculators')
OLCAPEST_DIR      = os.path.join(CALCULATORS_DIR, 'olcapest')
MULTINUTRIENT_DIR = os.path.join(CALCULATORS_DIR, 'multinutrient')
AGRI_DIESEL_DIR   = os.path.join(CALCULATORS_DIR, 'agri_diesel')
META_MODEL_DIR    = os.path.join(BASE_DIR, 'meta_model')
SPOLD_DIR         = os.path.join(BASE_DIR, 'spold')
PIPELINE_DIR      = os.path.join(BASE_DIR, 'pipeline')

# ── The configuration folder ─────────────────────────────────────────────────
# Every reference file the programs read -- not code, not a project's data --
# lives in config/, one place for all of them (see config/README.md):
#   master_data/  ecoinvent Master Data: ids, names, units, classifications,
#                 properties (rebuilt by the build_* scripts after a release)
#   models/       the calculators' reference data: the OLCA-Pest mapping tool,
#                 the FO geography map, the EUR2005 price model
#   review/       the internal review checklist, and the decisions remembered
#                 from your answers (shared by every project)
CONFIG_DIR       = os.path.join(BASE_DIR, 'config')
MASTER_DATA_DIR  = os.path.join(CONFIG_DIR, 'master_data')
MODELS_DATA_DIR  = os.path.join(CONFIG_DIR, 'models')
REVIEW_DATA_DIR  = os.path.join(CONFIG_DIR, 'review')
CHECKLIST_FILE   = os.path.join(REVIEW_DATA_DIR, 'agri_internal_review_checklist.xlsx')
DECISIONS_FILE   = os.path.join(REVIEW_DATA_DIR, 'meta_model_decisions.json')
PRICE_MODEL_FILE = os.path.join(MODELS_DATA_DIR, 'price_converter_model.json')

# The meta model's outputs sit with the datasets: each project's review
# workbook in output/<project>/.  The decisions remembered from your answers
# are reference knowledge, in config/review/ (DECISIONS_FILE above).
# META_OUTPUT_DIRNAME is the folder the outputs used to live in, kept only so
# a leftover one is never taken for a project.
META_OUTPUT_DIRNAME = 'meta_output'
META_OUTPUT_DIR     = os.path.join(BASE_DIR, 'output')
REVIEW_SUFFIX       = ' review.xlsx'


def is_review_workbook(path: str) -> bool:
    """The project's review workbook ('<project> review.xlsx' in its folder),
    which sits beside the datasets but is not one."""
    folder = os.path.basename(os.path.dirname(os.path.abspath(path)))
    return os.path.basename(path) == folder + REVIEW_SUFFIX

# Whether the meta model APPLIES its automatic corrections when it runs as a
# pipeline step (--batch).  True leaves finished datasets behind: the model's
# own corrections are written and logged in applied_log, while anything it
# will not decide (a missing yield, a naming call) is left for the review
# window that opens when the pipeline ends.  False makes the step propose
# only, so a pipeline run changes nothing until you approve it by hand.
META_APPLY_IN_PIPELINE = True

# The starting value of the input chooser's "also run the meta model and open
# its review" tick box (when a caller offers it).  It no longer decides
# whether the meta model's CORRECTIONS run with the models: run_pipeline.py
# runs the meta model in batch before and after the three calculators
# (no window), so the order of the two jobs does not matter.
RUN_META_IN_PIPELINE = False

# ── Input datasets directory ──────────────────────────────────────────────────
# PROJECT_NAME is derived from the input folder name so all outputs land under
# C:\python\models_modules\output\<PROJECT_NAME>\
INPUT_PROJECT_FOLDER    = 'ILCIDAF project full'   # ← UI updates this to switch projects
OLCAPEST_INPUT_FILENAME = '2_1_olive_production__integrated__non_irrigated__poli_cone__hill__south.xlsx'  # ← UI updates this to switch datasets
INPUT_DATASETS_DIR   = os.path.join(BASE_DIR, 'input datasets', INPUT_PROJECT_FOLDER)
PROJECT_NAME         = INPUT_PROJECT_FOLDER   # used to name the shared output folder

# ── Shared output root ────────────────────────────────────────────────────────
OUTPUT_ROOT_DIR = os.path.join(BASE_DIR, 'output', PROJECT_NAME)

# Output always uses the input stem + .xlsx so the file is traceable by name
import pathlib as _pl
_INPUT_STEM          = _pl.Path(OLCAPEST_INPUT_FILENAME).stem
PIPELINE_OUTPUT_FILE = os.path.join(OUTPUT_ROOT_DIR, _INPUT_STEM + '.xlsx')

# ── OLCApest file paths ───────────────────────────────────────────────────────
OLCAPEST_TOOL_FILE   = os.path.join(MODELS_DATA_DIR, 'OLCApest mapping tool_AG_updated.xlsx')
OLCAPEST_INPUT_FILE  = os.path.join(INPUT_DATASETS_DIR, OLCAPEST_INPUT_FILENAME)
OLCAPEST_INPUT_SHEET        = 'exchanges'
OLCAPEST_INPUT_FILTER_GROUP = 'ToEnvironment'   # OLCApest redistributes existing elementary emissions
OLCAPEST_OUTPUT_DIR  = OUTPUT_ROOT_DIR
OLCAPEST_OUTPUT_FILE = PIPELINE_OUTPUT_FILE

# ── Multinutrient file paths ──────────────────────────────────────────────────
MULTINUTRIENT_OUTPUT_DIR  = OUTPUT_ROOT_DIR
MULTINUTRIENT_OUTPUT_FILE = PIPELINE_OUTPUT_FILE

# ── Agri Diesel file paths ────────────────────────────────────────────────────
AGRI_DIESEL_OUTPUT_DIR  = OUTPUT_ROOT_DIR
AGRI_DIESEL_OUTPUT_FILE = PIPELINE_OUTPUT_FILE
AGRI_DIESEL_SPLIT_DIR   = os.path.join(OUTPUT_ROOT_DIR, 'crop_datasets_new')
AGRI_DIESEL_CHUNK_SIZE  = 50
FO_GEO_MAPPING_FILE          = os.path.join(MODELS_DATA_DIR, 'fo_geography_map.csv')

# ── Diesel model: the three cases ────────────────────────────────────────────
# Per field operation (FO), in this order:
#   1. The data provider gives the FO's diesel -- kg/ha in the FO comment (or
#      in its own diesel row), or MJ per kg of product in the FO comment:
#      that figure is used.
#   2. The provider gives ONE total diesel consumption (an exchange named in
#      DIESEL_TOTAL_FLOWS): it is split over the FOs it covers in proportion to
#      their kg/ha for the geography -- z * x / (x + y + ...) -- and replaces
#      the total exchange.  The FOs it covers are the ones its comment names;
#      a named FO the dataset does not have gets a diesel row of its own.  If
#      the comment names none, every FO without its own diesel (case 1) shares.
#   3. Otherwise: kg/ha for the geography x passes / yield.
# Diesel rows are always named "diesel used in <operation>" -- no comma.
# A pass count the provider states ("number of passes is 0.017493") is used
# as given; any other pass count is rounded to 0.5.
DIESEL_TOTAL_FLOWS = (
    'diesel, burned in agricultural machinery',
)
# Net heating value of diesel, for a total given in MJ.  ecoinvent's 'diesel'
# carries 42.3-42.8 MJ/kg ("heating value, net", properties 3.12); 42.8 is
# the common value.
DIESEL_LHV_MJ_PER_KG = 42.8
# A word in a total's comment -> the FO (as named in FO_DIESEL_INTENSITIES)
# it stands for.  Needed when the word alone is ambiguous ('harvesting' is
# nine different FOs) or names an FO the dataset does not contain.  A word
# that matches none is reported, and the total is then left as it is.
DIESEL_FO_ALIASES = {
    # 'harvesting': 'combine harvesting',
}

# Raw ecoinvent sources behind the FO_DIESEL_INTENSITIES table below.  The
# pipeline NEVER opens these -- they are read only by
# calculators/agri_diesel/build_fo_diesel_table.py when the table is
# regenerated for a new ecoinvent release.
FO_EXCHANGES_DB_FILE    = r'C:\python\Fuel Project\excel\exchanges (3.12, undef).xlsx'
FO_DATASETS_LIST_FILE   = r'C:\python\Fuel Project\excel\FO UND.xlsx'
FO_DATASETS_LIST_SHEET  = 'Field Operation Datasets Undefi'

# BEGIN GENERATED: FO_DIESEL_INTENSITIES
# Diesel intensity (kg diesel per 1 ha) for every field operation, keyed by
# (fo_name, geographies).  Geographies sharing the same intensity are collapsed
# onto one row and pipe-joined; the calculator expands them back into one key
# per geography at load time, so the geography fallback chain is unaffected.
#
# 104 rows covering 151 (field operation, geography) pairs.
#
# Only field operations with a RELEVANT diesel input are listed: the activity
# must be measured per hectare and its diesel input must be machinery diesel in
# kg.  That excludes irrigation and water pumping (m3 / MJ), agricultural
# transport (metric ton*km) and the "market for ..." activities, whose diesel is
# real but is not diesel per hectare of field work.
#
# Generated by calculators/agri_diesel/build_fo_diesel_table.py from
#   exchanges (3.12, undef).xlsx  (diesel exchange amounts)
#   FO UND.xlsx  (field-operation whitelist)
# Re-run that script with --write after an ecoinvent release.  Hand edits to
# individual values survive only until the next regeneration, so prefer
# correcting the source data.
FO_DIESEL_INTENSITIES = {
    ('application of plant protection product, by field sprayer', 'AU|CA-QC'): 1.67,
    ('application of plant protection product, by field sprayer', 'BR|GLO'): 0.32512675,
    ('application of plant protection product, by field sprayer', 'CH'): 1.76,
    ('application of plant protection product, by field sprayer', 'Canada without Quebec'): 8.80598123140239,
    ('application of plant protection product, by field sprayer', 'US'): 0.636682752479663,
    ('application of plant protection product, by field sprayer', 'ZA'): 1.6,
    ('application of plant protection product, fumigation', 'US'): 4.15047706955357,
    ('chopping, maize', 'CH|GLO'): 52.8,
    ('combine harvesting', 'AU|CA-QC|ZA'): 15.48,
    ('combine harvesting', 'BR'): 5.5,
    ('combine harvesting', 'CH|GLO'): 33.3,
    ('combine harvesting', 'Canada without Quebec'): 3.93321389093878,
    ('combine harvesting', 'US'): 14.3487921547424,
    ('combine harvesting, maize', 'US'): 13.2225222223424,
    ('combine harvesting, soybean', 'US'): 13.458030942803,
    ('combine harvesting, sweet corn', 'US'): 13.3710195889274,
    ('fertilising, by broadcaster', 'AU|CA-QC|ZA'): 2.5,
    ('fertilising, by broadcaster', 'BR'): 0.7,
    ('fertilising, by broadcaster', 'CH|GLO'): 5.29,
    ('fertilising, by broadcaster', 'Canada without Quebec'): 2.67634907944963,
    ('fertilising, by broadcaster', 'US'): 1.77726,
    ('fertilising, by injection', 'US'): 4.32152192069901,
    ('fertilising, by rig fertiliser, sugarcane', 'BR|GLO'): 10.94176563,
    ('fertilising, by stool splitter, sugarcane', 'BR|GLO'): 12.11766522,
    ('field leveling, sugarcane', 'BR|GLO'): 8.534577188,
    ('furrow covering, for sugarcane', 'BR|GLO'): 10.24149263,
    ('furrowing, sugarcane', 'BR|GLO'): 6.87768125,
    ('harvesting, by complete harvester, beets', 'CH|GLO'): 103.0,
    ('harvesting, by complete harvester, ground crops', 'CA-QC'): 22.63,
    ('harvesting, by complete harvester, ground crops', 'CH|GLO'): 28.1,
    ('harvesting, by potato harvester', 'US'): 16.18242,
    ('harvesting, by sweet corn picker', 'US'): 13.3710195889274,
    ('harvesting, sugarcane', 'BR|GLO'): 66.03564016,
    ('haying, by rotary tedder', 'CA-QC'): 1.8,
    ('haying, by rotary tedder', 'CH|GLO'): 1.92,
    ('hoeing', 'CH|GLO'): 3.28,
    ('limestone and gypsum application, by spreader', 'BR|GLO'): 1.168288788,
    ('mowing, by rotary mower', 'CH|GLO'): 4.31,
    ('mowing, by rotary or sickle mower', 'US'): 6.17364,
    ('mulching', 'CH|GLO'): 3.51,
    ('planting', 'CH|GLO'): 16.8,
    ('planting with starter fertiliser or amendment, by drill', 'US'): 3.55718879545454,
    ('planting with starter fertiliser or amendment, by no till planter', 'US'): 2.6451255883,
    ('planting with starter fertiliser or amendment, by row crop planter', 'US'): 2.62544313281406,
    ('planting with starter fertiliser or amendment, by subsoiler/v-ripper planter', 'US'): 7.7375786049551,
    ('planting with starter fertiliser, by no till planter', 'AU'): 10.75,
    ('planting with starter fertiliser, by no till planter', 'BR|GLO'): 3.266808511,
    ('planting, by drill', 'US'): 4.07167129530801,
    ('planting, by no till drill', 'US'): 5.14469274887283,
    ('planting, by no till planter', 'US'): 2.6451255883,
    ('planting, by ridge planter', 'US'): 2.62544313281406,
    ('planting, by row crop planter', 'US'): 2.62544313281406,
    ('planting, potato', 'CA-QC'): 10.75,
    ('planting, potato', 'CH|GLO'): 8.9,
    ('planting, potato', 'US'): 8.8863,
    ('planting, sugarcane', 'BR|GLO'): 9.62875375,
    ('potato haulm cutting', 'CH|GLO'): 4.79,
    ('sowing', 'BR'): 0.985,
    ('sowing', 'CA-QC|ZA'): 6.29999999999998,
    ('sowing', 'CH|GLO'): 3.82,
    ('sowing', 'Canada without Quebec'): 3.41590848728595,
    ('sugarcane loading, by loader', 'BR|GLO'): 17.34009333,
    ('sugarcane transfer, by dump cart', 'BR|GLO'): 33.56377412,
    ('sugarcane vinasse application, by wheel reel irrigation equipment', 'BR|GLO'): 7.9551,
    ('swath, by rotary windrower', 'CA-QC'): 6.97000000000001,
    ('swath, by rotary windrower', 'CH|GLO'): 2.94,
    ('swath, by rotary windrower', 'Canada without Quebec'): 3.95298799620525,
    ('swath, by rotary windrower', 'US'): 4.2138,
    ('tillage, cultivating, by strip tiller', 'US'): 8.8958,
    ('tillage, cultivating, chiselling', 'AU|CA-QC'): 9.44,
    ('tillage, cultivating, chiselling', 'CH|GLO'): 15.5,
    ('tillage, cultivating, chiselling', 'Canada without Quebec'): 0.708182075204976,
    ('tillage, cultivating, chiselling', 'US'): 5.58631863502053,
    ('tillage, currying, by weeder', 'CH|GLO'): 1.6,
    ('tillage, disking', 'US'): 2.633892195,
    ('tillage, disking, with amendment', 'US'): 3.633892195,
    ('tillage, field cultivator', 'US'): 2.90826224686465,
    ('tillage, field cultivator with amendment', 'US'): 2.90826224686465,
    ('tillage, harrowing, by moldboard plow', 'US'): 9.28356526813725,
    ('tillage, harrowing, by offset disc harrow', 'BR|GLO'): 19.1246971,
    ('tillage, harrowing, by offset disc harrow', 'CA'): 1.23615448540039,
    ('tillage, harrowing, by offset disc harrow', 'US'): 5.947619666,
    ('tillage, harrowing, by offset leveling disc harrow', 'BR|GLO'): 4.731574324,
    ('tillage, harrowing, by offset leveling disc harrow', 'US'): 5.947619666,
    ('tillage, harrowing, by rotary harrow', 'CA'): 1.69485241678131,
    ('tillage, harrowing, by rotary harrow', 'CH|GLO'): 11.5,
    ('tillage, harrowing, by spring tine harrow', 'CH|GLO'): 4.44,
    ('tillage, harrowing, by tandem disk', 'US'): 5.22498946644911,
    ('tillage, hoeing and earthing-up, potatoes', 'CA-QC'): 13.4,
    ('tillage, hoeing and earthing-up, potatoes', 'CH|GLO'): 3.6,
    ('tillage, ploughing', 'CA-QC'): 21.9,
    ('tillage, ploughing', 'CH|GLO'): 26.1,
    ('tillage, ploughing', 'ZA'): 21.6,
    ('tillage, ridge cultivator', 'US'): 2.49003215681818,
    ('tillage, rolling', 'CA-QC'): 3.1,
    ('tillage, rolling', 'CH|GLO'): 3.18,
    ('tillage, rolling', 'Canada without Quebec'): 2.47073116522121,
    ('tillage, rotary cultivator', 'CA-QC|ZA'): 6.04,
    ('tillage, rotary cultivator', 'CH|GLO'): 14.1,
    ('tillage, rotary cultivator', 'Canada without Quebec'): 1.74489182901873,
    ('tillage, rotary cultivator', 'US'): 2.341,
    ('tillage, subsoiling, by subsoiler or v-ripper', 'US'): 7.45562133789422,
    ('tillage, subsoiling, by subsoiler plow', 'BR|GLO'): 16.506435,
    ('weed control, by brush cutter, pasture', 'BR|GLO'): 4.35,
}
# END GENERATED: FO_DIESEL_INTENSITIES

# Master-data (intermediateExchangeId / unitId) lookup for the custom "diesel
# used in, ..." and multinutrient (N/P2O5/K2O) flows, keyed by the canonical
# ecoinvent flow name. Built from the existing master datasets so new datasets
# link to the SAME product instead of getting a fresh, disconnected id.
MASTER_DATA_LOOKUP_FILE = os.path.join(MASTER_DATA_DIR, 'master_data_lookup.json')

# Classification block (By-product classification + CPC code) for each of the
# same custom flows, needed to declare them in a dataset's <usedUserMasterData>
# section -- without it, ecoEditor rejects the file as using "non-ecoinvent
# master data" (custom flows aren't in ecoinvent's own master database, so the
# dataset must self-declare them).
MASTER_DATA_CLASSIFICATIONS_FILE = os.path.join(MASTER_DATA_DIR, 'master_data_classifications.json')

# Real ecoinvent geographyId per shortname (e.g. "IT"), extracted from the
# existing master datasets. These are standard, already-recognized ecoinvent
# geographies -- NOT project-specific -- so they must be looked up, never
# self-generated, or ecoEditor rejects them the same way it rejects an
# undeclared custom flow.
GEOGRAPHY_ID_LOOKUP_FILE = os.path.join(MASTER_DATA_DIR, 'geography_id_lookup.json')
# Every ecoinvent geography NAME and its shortname ("Mongolia" -> "MN"), from
# the Geographies sheet of MasterData (3.11, cut-off).xlsx.  A dataset must
# carry the shortname; the meta model uses this to turn a written-out name
# into it.
GEOGRAPHY_NAME_LOOKUP_FILE = os.path.join(MASTER_DATA_DIR, 'geography_name_lookup.json')

# The ecoinvent MasterData workbook, and the flow index built from it by
# calculators/agri_diesel/build_master_data_index.py: the unit, CPC and
# default properties Master Data defines per flow, for the checklist checks
# (meta_model/skill_checks.py).  Rebuild after a new release.
MASTER_DATA_XLSX = r'C:\python\Agricultural Database\excel\MasterData (3.11, cut-off).xlsx'
MASTER_DATA_FLOW_INDEX_FILE = os.path.join(MASTER_DATA_DIR, 'master_data_flow_index.json')

# Real ecoinvent classificationId per ISIC rev.4 value (e.g. "0121:Growing of
# grapes"), extracted from the existing master datasets. Standard, shared
# classification codes -- same "must be looked up, not self-generated" rule.
ISIC_CLASSIFICATION_LOOKUP_FILE = os.path.join(MASTER_DATA_DIR, 'isic_classification_lookup.json')

# Real intermediateExchangeId (by exchange name -- confirmed 100% consistent
# across the master database) and elementaryExchangeId (by name + compartment
# + subcompartment -- also confirmed 100% consistent) for every STANDARD
# ecoinvent exchange, not just the custom diesel/multinutrient flows. Same
# "must be looked up, not self-generated" rule applies here at much larger
# scale: fertiliser/pesticide inputs, market activities, emissions, etc.
STANDARD_INTERMEDIATE_EXCHANGE_LOOKUP_FILE = os.path.join(MASTER_DATA_DIR, 'standard_intermediate_exchange_lookup.json')
STANDARD_ELEMENTARY_EXCHANGE_LOOKUP_FILE   = os.path.join(MASTER_DATA_DIR, 'standard_elementary_exchange_lookup.json')

# Real subcompartmentId per (compartment, subcompartment) pair -- same rule,
# same reason: these are standard, shared ecoinvent values.
SUBCOMPARTMENT_ID_LOOKUP_FILE = os.path.join(MASTER_DATA_DIR, 'subcompartment_id_lookup.json')

# Real propertyId (by property name) and real unitId (by unit name), same rule.
# All of the lookups above (geography/ISIC/intermediate/elementary/subcompartment/
# property/unit) are sourced from ONE authoritative file: the official ecoinvent
# release master data workbook (MasterData (3.12, undef).xlsx), not scattered
# corpus scans -- every id must be verifiable against that single source.
PROPERTY_ID_LOOKUP_FILE = os.path.join(MASTER_DATA_DIR, 'property_id_lookup.json')
# Default property values per ecoinvent product, reduced from the ecoinvent
# properties export by calculators/agri_diesel/build_property_defaults.py.
PROPERTIES_EXPORT_FILE  = r'C:\python\Fuel Project\excel\properties (3.12, undef).xlsx'
PROPERTY_DEFAULTS_FILE  = os.path.join(MASTER_DATA_DIR, 'property_defaults_lookup.json')
UNIT_ID_LOOKUP_FILE     = os.path.join(MASTER_DATA_DIR, 'unit_id_lookup.json')
PERSON_ID_LOOKUP_FILE   = os.path.join(MASTER_DATA_DIR, 'person_id_lookup.json')

# ── Crop class overrides for new datasets ────────────────────────────────────
# Add entries here for any new-dataset crop whose activityName is not yet present
# Keyword overrides for crop class lookup in OLCApest.
# Keys are matched as substrings of the activityName (case-insensitive),
# so one entry covers all variants of that crop type.
# Value : class_C from the CLASSIFICATION sheet (must match exactly)
CROP_CLASS_OVERRIDES = {
    'clementine':   'Citrus fruit trees',
    'orange':       'Citrus fruit trees',
    'minor citrus': 'Citrus fruit trees',
}

# ── OLCApest substance synonyms, PER PROJECT ────────────────────────────────
# OLCApest recognises an emission by its name (or CAS) in the tool's
# contextualized_Pest sheet.  A dataset that names the emission after the
# ELEMENT rather than the active substance -- "Copper ion" where the tool
# knows "Copper oxide" -- is not recognised, and its emissions pass through
# unredistributed with nothing said about it.
#
# This is keyed BY PROJECT, and must stay that way: the same flow name means
# different things in different inventories.  In 'Olive,TN' the copper
# fungicide is emitted as "Copper ion" (3.2e-04 kg, and no Cupric hydroxide
# row exists).  In 'ILCIDAF project full' the fungicide is named "Cupric
# hydroxide" and "Copper ion" is a TRACE CONTAMINANT of 2.3e-11 kg, sitting
# with cadmium, chromium, lead and zinc at the same magnitude -- mapping it
# there would turn a trace metal into an applied fungicide.
#
# The key is matched case-insensitively against the emission name; the value
# must appear in the tool exactly.  '*' applies to every project -- use it
# only for a substance whose meaning cannot vary.
# ── Dose-driven pesticide emissions, PER PROJECT ────────────────────────────
# OLCApest redistributes the EMISSION rows of a dataset; it does not derive
# them from the applied amount.  Every dataset in this pipeline is built so
# that the whole dose is emitted -- mancozeb, sulfur and copper all come out
# at a ratio of exactly 1.0000 -- so when a dose is edited and its emission
# rows are not, the split silently follows the stale figure.
#
# Listing a project here makes the DOSE drive the emission: each recognised
# pesticide's emission total is set to (applied amount x factor) before the
# split, so editing the input is enough.  The factor is normally 1.0, which
# is the convention above.  It exists for the case where the emission is the
# ELEMENT and the dose is the COMPOUND -- emitting "Copper ion" for a dose of
# copper oxide is 0.7989 kg Cu per kg CuO, not 1.0 -- a modelling choice that
# changes the number by a fifth, so it is stated here rather than assumed.
OLCAPEST_DOSE_DRIVES_EMISSION = {
    # Empty on purpose.  Nothing is rescaled until a project is listed here.
}

# ── Pesticides EXCLUDED from emission generation, per project ──────────────
# OLCApest splits emission rows.  A pesticide the dataset buys but never
# emits is invisible to it, so by DEFAULT, in every project, the emission of
# any applied substance the mapping sheet classes as a pesticide is built
# from the dose: the whole applied mass, named after the active substance,
# split across the OLCApest compartments.  Nothing needs configuring for a
# new project or a new substance -- add it to the mapping sheet and it works.
#
# Generation never happens when the dataset already emits that substance
# under its own name, nor when an existing pesticide emission in the same
# activity carries exactly the same mass (that is the same application under
# another name -- 'copper oxide' emitted as 'Cupric hydroxide' -- and
# generating on top of it would count it twice).  Both cases are reported.
#
# List a substance here only to switch generation OFF for a project whose
# emissions you maintain by hand.
OLCAPEST_GENERATE_EMISSIONS = {
    # 'Some project': ['a substance emitted by hand'],
}

OLCAPEST_SUBSTANCE_SYNONYMS = {
    # Empty on purpose.  A synonym asserts that two names are the SAME
    # substance, so it must never be used to tie a metal emission to a
    # pesticide: 'Copper ion' is a copper emission, 'Copper oxide' is the
    # fungicide, and they are different flows.  The fungicide's emission
    # belongs in the dataset under its own name -- the way these datasets
    # carry 'Cupric hydroxide' -- and OLCApest then recognises it directly
    # from the mapping sheet with no synonym at all.
}

# ── Multinutrient allocation factors ─────────────────────────────────────────
# Mass-based split ratios for each multi-nutrient fertiliser into N / P2O5 / K2O
ALLOCATION_FACTORS = {
    'NPK (15-15-15) fertiliser production':             {'N': 0.333333333333, 'P2O5': 0.333333333333, 'K2O': 0.333333333333},
    'NPK (26-15-15) fertiliser production':             {'N': 0.464285714,    'P2O5': 0.267857143,    'K2O': 0.267857143},
    'ammonium nitrate phosphate production':            {'N': 0.5,            'P2O5': 0.5,            'K2O': 0.0},
    'monoammonium phosphate production':                {'N': 0.139073,    'P2O5': 0.860927,    'K2O': 0.0},
    'diammonium phosphate production':                  {'N': 0.28125,        'P2O5': 0.71875,        'K2O': 0.0},
    'potassium nitrate production':                     {'N': 0.2413793,    'P2O5': 0.0,            'K2O': 0.7586207},
    'potassium nitrate production, agricultural grade': {'N': 0.2413793,    'P2O5': 0.0,            'K2O': 0.7586207},
    #'potassium nitrate production, industrial grade':   {'N': 0.2413793,    'P2O5': 0.0,            'K2O': 0.7586207},
    #'potassium nitrate production, technical grade':    {'N': 0.2413793,    'P2O5': 0.0,            'K2O': 0.7586207},
}

# Single-nutrient reference products
NUTRIENT_REFERENCE_PRODUCTS = {
    'N':    'inorganic nitrogen fertiliser, as N',
    'P2O5': 'inorganic phosphorus fertiliser, as P2O5',
    'K2O':  'inorganic potassium fertiliser, as K2O',
}

# ── Pipeline Python interpreter ──────────────────────────────────────────────
# The venv that has pandas / openpyxl / all model dependencies installed.
# The UI (tkinter) runs in the system Python; model scripts run in this venv.
PIPELINE_PYTHON = os.path.join(BASE_DIR, '.venv', 'Scripts', 'python.exe')

# ── SPOLD converter ───────────────────────────────────────────────────────────
SPOLD_OUTPUT_DIR    = os.path.join(OUTPUT_ROOT_DIR, 'spold_output')
SPOLD_CONVERTER_DIR_NEW = SPOLD_DIR

# ── The three models (run_three_models.py; run_pipeline.py adds the rest) ───
# THE THREE MODELS, and nothing else.  Each of the other programs in this
# project is a separate job with its own entry point:
#
#   run_pipeline.py         the whole chain: a meta model pass that prepares
#                           the working copy, the three calculators, a meta
#                           model pass that checks the result and writes the
#                           review, and verify -- each a line to comment out
#   run_three_models.py     the three calculators below only -- they BUILD the
#                           dataset workbooks in output/
#   spold_converter_new.py  turns a workbook into EcoSpold2 -- called by
#                           every program above each time it saves one
#   meta_model_new.py       checks metadata, nomenclature and master data
#   verify_output.py        asks whether the output is complete and current
#
# Apart from those two meta model passes, and the .spold written on every
# save, none of them runs another.
PIPELINE = [
    (
        "OLCApest Calculator (New Datasets)",
        os.path.join(OLCAPEST_DIR, 'read_pesticide_data_new.py'),
    ),
    (
        "Multinutrient Calculator (New Datasets)",
        os.path.join(MULTINUTRIENT_DIR, 'multinutrient_calculator_new.py'),
    ),
    (
        "Agricultural Diesel Calculator (New Datasets)",
        os.path.join(AGRI_DIESEL_DIR, 'agri_diesel_calculator_new.py'),
    ),
]

# The converter, kept out of PIPELINE: every program rewrites the .spold of
# each workbook it saves (write_spold_for below).  Run directly, it rewrites
# them all from the workbooks as they stand -- which is how a pipeline run
# writes them: each step saves every dataset, and only the last save's .spold
# would survive, so a run that ends with this step (run_steps) defers the
# others' and converts each workbook once, as it finally stands.
SPOLD_CONVERTER_STEP = (
    "SPOLD Converter (New Datasets)",
    os.path.join(SPOLD_CONVERTER_DIR_NEW, 'spold_converter_new.py'),
)


# ── DataX sheet column schemas (full ecoEditor DataX format) ─────────────────
# These define the exact column order expected by ecoEditor for re-import.
# All pipeline outputs — including spold→Excel conversion — must match these.

META_COLS = [
    'activityId', 'activityNameId',
    'activityName', 'geography', 'specialActivityType', 'startDate', 'endDate',
    'technologyLevel', 'synonyms', 'tags', 'includedActivitiesStart',
    'includedActivitiesEnd', 'generalComment', 'geographyComment',
    'allocationComment', 'technologyComment', 'timePeriodComment', 'ISIC',
    'dataEntryBy_isActiveAuthor', 'dataEntryBy_personName',
    'dataGeneratorAndPublication_accessRestrictedTo',
    'dataGeneratorAndPublication_dataPublishedIn',
    'dataGeneratorAndPublication_pageNumbers',
    'dataGeneratorAndPublication_personName',
    'dataGeneratorAndPublication_publishedSourceId',
    'modellingAndValidation_extrapolations', 'modellingAndValidation_percent',
    'modellingAndValidation_samplingProcedure',
    'fileAttributes_majorRelease', 'fileAttributes_minorRelease',
    'fileAttributes_majorRevision', 'fileAttributes_minorRevision',
]

REVIEW_COLS = [
    'activityName', 'geography', 'reviewerName', 'reviewDate',
    'reviewedMajorRelease', 'reviewedMinorRelease', 'reviewedMajorRevision',
    'reviewedMinorRevision', 'details', 'otherDetails',
]

EXCHANGES_COLS = [
    'activityName', 'geography', 'group', 'name', 'compartment', 'subcompartment',
    'activityLink_activityName', 'activityLink_geography', 'unitName', 'amount',
    'variableName', 'mathematicalRelation', 'comment', 'pageNumbers', 'sourceId',
    'uncertainty type', 'uncertainty comment', 'variance', 'minValue',
    'mostLikelyValue', 'maxValue', 'reliability', 'completeness',
    'temporalCorrelation', 'geographicalCorrelation', 'furtherTechnologyCorrelation',
    'PV_amount', 'PV_variableName', 'PV_mathematicalRelation', 'PV_comment',
    'PV_sourceId', 'PV_uncertainty type', 'PV_uncertainty comment', 'PV_variance',
    'PV_minValue', 'PV_mostLikelyValue', 'PV_maxValue', 'PV_reliability',
    'PV_completeness', 'PV_temporalCorrelation', 'PV_geographicalCorrelation',
    'PV_furtherTechnologyCorrelation',
]

PROPERTIES_COLS = [
    'activityName', 'geography', 'group', 'name', 'compartment', 'subcompartment',
    'activityLink_activityName', 'activityLink_geography', 'propertyName',
    'unitName', 'amount', 'variableName', 'mathematicalRelation', 'comment',
    'uncertainty type', 'uncertainty comment', 'variance', 'minValue',
    'mostLikelyValue', 'maxValue', 'reliability', 'completeness',
    'temporalCorrelation', 'geographicalCorrelation', 'furtherTechnologyCorrelation',
]

PARAMETERS_COLS = [
    'activityName', 'geography', 'name', 'unitName', 'amount', 'variableName',
    'mathematicalRelation', 'comment', 'uncertainty type', 'uncertainty comment',
    'variance', 'minValue', 'mostLikelyValue', 'maxValue', 'reliability',
    'completeness', 'temporalCorrelation', 'geographicalCorrelation',
    'furtherTechnologyCorrelation',
]

# The sheets every workbook must carry WITH their full header row, even when
# empty: an empty sheet read with pandas comes back with no columns and was
# written back that way, which ecoEditor's DataX import does not accept.
# (exchanges and meta are completed elsewhere: the meta model fills the meta
# schema, and an exchanges sheet with a blank 'amount' header is repaired by
# the calculators, which an added empty 'amount' column would defeat.)
SCHEMA_SHEETS = (
    ('review',     REVIEW_COLS),
    ('properties', PROPERTIES_COLS),
    ('parameters', PARAMETERS_COLS),
)


# The data-provider template (dataset_input_template.xlsx) is an input
# dataset like any other, plus two things for the person filling it in: a
# "readme" sheet, and an explanation row under each header whose first cell
# starts with TEMPLATE_EXPLANATION_MARK.  Both are dropped when an input is
# read, so the template needs no reader of its own.
TEMPLATE_EXPLANATION_MARK = '[explanation]'
TEMPLATE_README_SHEETS = ('readme',)


def drop_template_guidance(sheets: dict, dataset_path: str | None = None,
                           step: str = '') -> dict:
    """Remove the template's readme sheet, explanation rows and example
    rows.  Returns `sheets`.  With `dataset_path`, what is removed is
    recorded in the project's deletion log (see record_deletions)."""
    import re
    import pandas as pd
    for name in [n for n in sheets if str(n).strip().lower() in TEMPLATE_README_SHEETS]:
        if dataset_path:
            record_deletions(dataset_path, step, name, [f'the whole "{name}" sheet'],
                             'template guidance sheet')
        del sheets[name]
    for name, frame in list(sheets.items()):
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        first = frame.iloc[:, 0].map(lambda v: str(v).strip().lower()
                                     .startswith(TEMPLATE_EXPLANATION_MARK) if isinstance(v, str) else False)
        # The template's own example rows: an activityName that is still the
        # placeholder ("[name of process 1]").  Left in, each became an
        # activity of its own -- with a .spold for "[name of process 1]".
        if 'activityName' in frame.columns:
            first |= frame['activityName'].map(
                lambda v: isinstance(v, str) and bool(re.fullmatch(r'\s*\[[^\]]*\]\s*', v)))
        if first.any():
            if dataset_path:
                record_deletions(dataset_path, step, name, frame[first],
                                 'template explanation or example row')
            sheets[name] = frame[~first].reset_index(drop=True)
    return sheets


# ── What was deleted, and by which step ──────────────────────────────────────
# Every row or cell a step removes from a dataset -- the meta model and the
# three calculators alike -- is written here, in full, so the project's review
# can list exactly what is gone (its Deleted sheet).  One file per project,
# beside the datasets; an entry is the dataset, the step, the sheet, the
# reason and the deleted content, with when it was first and last deleted (a
# fresh pipeline run deletes the same things again).
DELETIONS_FILENAME = 'deletions_log.json'


def _text(value) -> str:
    try:
        import pandas as pd
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def describe_row(row) -> str:
    """One deleted exchange / property / parameter row, in words:
    'Ammonia [ToEnvironment, air/non-urban air or from high stacks]: 0.0012 kg
    (activity: ...)'."""
    get = row.get if hasattr(row, 'get') else (lambda k, d=None: d)
    name = _text(get('name')) or _text(get('variableName')) or '(no name)'
    place = '/'.join(p for p in (_text(get('compartment')), _text(get('subcompartment'))) if p)
    where = ', '.join(p for p in (_text(get('group')), place) if p)
    prop = _text(get('propertyName'))
    amount = _text(get('amount'))
    unit = _text(get('unitName'))
    text = name + (f' [{where}]' if where else '') + (f", property '{prop}'" if prop else '')
    if amount:
        text += f': {amount} {unit}'.rstrip()
    if _text(get('mathematicalRelation')):
        text += f" = {_text(get('mathematicalRelation'))}"
    activity = _text(get('activityName'))
    return text + (f' (activity: {activity})' if activity else '')


# How the meta model writes a deletion into the description of a correction
# (Changes Applied, Corrections), so the same exact list is in the review's
# change history and, read back by deleted_items(), in the deletion log.
DELETED_MARK = 'DELETED: '
DELETED_SEP = ' | '


def deleted_note(items) -> str:
    return DELETED_MARK + DELETED_SEP.join(str(i) for i in items)


def deleted_items(description: str) -> list:
    text = str(description or '')
    if DELETED_MARK not in text:
        return []
    return [i.strip() for i in text.split(DELETED_MARK, 1)[1].split(DELETED_SEP) if i.strip()]


def describe_cell(row, column: str) -> str:
    """One emptied cell: 'PV_comment of Occupation, ... [FromEnvironment]: '7.33''."""
    return f'{column} of {describe_row(row)}: {_text(row.get(column))!r}'


def deletions_path(project_dir: str) -> str:
    return os.path.join(project_dir, DELETIONS_FILENAME)


def record_deletions(dataset_path: str, step: str, sheet: str, rows, reason: str) -> None:
    """
    Record what a step deleted from one dataset.  `rows` is a DataFrame of
    the removed rows (each described with describe_row) or a list of texts
    already saying exactly what was removed (a cell and its value).
    """
    import json
    from datetime import datetime
    if rows is None:
        return
    if hasattr(rows, 'iterrows'):
        items = [describe_row(r) for _, r in rows.iterrows()]
    else:
        items = [str(r) for r in rows]
    items = [i for i in items if i]
    if not items or not dataset_path:
        return
    project_dir, dataset = os.path.split(os.path.abspath(dataset_path))
    path = deletions_path(project_dir)
    try:
        with open(path, encoding='utf-8') as fh:
            log = json.load(fh)
    except (OSError, ValueError):
        log = {}
    stamp = datetime.now().isoformat(sep=' ', timespec='seconds')
    for item in items:
        key = '||'.join((dataset, step, sheet, reason, item))
        entry = log.setdefault(key, {'dataset': dataset, 'step': step, 'sheet': sheet,
                                     'reason': reason, 'deleted': item, 'first_deleted': stamp})
        entry['last_deleted'] = stamp
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(log, fh, indent=1, ensure_ascii=False)
    except OSError as exc:
        print(f'  [deletions] could not record in {path}: {exc}')


def read_deletions(project_dir: str) -> list:
    """Every recorded deletion of a project, oldest first."""
    import json
    try:
        with open(deletions_path(project_dir), encoding='utf-8') as fh:
            log = json.load(fh)
    except (OSError, ValueError):
        return []
    return sorted(log.values(), key=lambda e: (e.get('dataset', ''), e.get('first_deleted', '')))


def complete_sheet_schema(sheets: dict) -> dict:
    """
    Give review, properties and parameters every column of their DataX
    schema, in schema order (any extra column kept after them), creating a
    missing sheet empty.  Rows and values are never touched.  Returns `sheets`.
    """
    import pandas as pd
    for name, columns in SCHEMA_SHEETS:
        frame = sheets.get(name)
        if not isinstance(frame, pd.DataFrame):
            sheets[name] = pd.DataFrame(columns=columns)
            continue
        extra = [c for c in frame.columns
                 if c not in columns and not (str(c).startswith('Unnamed:') and frame[c].isna().all())]
        if list(frame.columns) == list(columns):
            continue
        sheets[name] = frame.reindex(columns=list(columns) + extra)
    return sheets


# ── What the calculators computed from ──────────────────────────────────────
# The calculators always start from input datasets/, so an output workbook
# edited by hand is re-checked by recheck.py without recalculating.  That is
# only right while the edit leaves alone what the calculators compute from:
# the yield, the inputs bought (fertilisers, pesticides, field operations)
# and the land.  A full pipeline run records a fingerprint of exactly that
# per dataset; recheck.py compares it and says when a full run is needed.
CALCULATION_FINGERPRINTS = 'calculation_fingerprints.json'


def calculation_fingerprint(workbook_path: str) -> str:
    """A hash of what the calculators work from in one output workbook."""
    import hashlib
    import re
    import pandas as pd
    try:
        sheets = pd.read_excel(workbook_path, sheet_name=['meta', 'exchanges'])
    except Exception:
        return ''
    parts = []
    for _, m in sheets['meta'].iterrows():
        yields = re.findall(r'Yield:\s*\[([^\]]*)\]', _text(m.get('generalComment')))
        parts.append(f"{_text(m.get('activityName'))}|yield={','.join(yields)}")
    e = sheets['exchanges']
    if {'group', 'name', 'amount'} <= set(e.columns):
        for _, r in e.iterrows():
            group, name = _text(r.get('group')), _text(r.get('name'))
            if group == 'FromTechnosphere' or name.lower().startswith(('occupation', 'transformation')):
                amount = pd.to_numeric(r.get('amount'), errors='coerce')
                parts.append(f"{_text(r.get('activityName'))}|{name}|"
                             f"{'' if pd.isna(amount) else f'{float(amount):.9g}'}")
    return hashlib.sha1('\n'.join(sorted(parts)).encode('utf-8')).hexdigest()


def _fingerprints_path(project: str) -> str:
    return os.path.join(BASE_DIR, 'output', project, CALCULATION_FINGERPRINTS)


def _output_workbook(project: str, filename: str) -> str:
    return os.path.join(BASE_DIR, 'output', project,
                        os.path.splitext(os.path.basename(filename))[0] + '.xlsx')


def save_fingerprints(project: str, files: list) -> None:
    """Record, after a full run, what each dataset was calculated from."""
    import json
    path = _fingerprints_path(project)
    try:
        with open(path, encoding='utf-8') as fh:
            known = json.load(fh)
    except (OSError, ValueError):
        known = {}
    for name in files:
        workbook = _output_workbook(project, name)
        if os.path.isfile(workbook):
            known[os.path.basename(workbook)] = calculation_fingerprint(workbook)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(known, fh, indent=1, sort_keys=True)


def stale_calculations(project: str, files: list) -> list:
    """The output workbooks whose calculation inputs changed since the last
    full run, or that no full run has recorded."""
    import json
    try:
        with open(_fingerprints_path(project), encoding='utf-8') as fh:
            known = json.load(fh)
    except (OSError, ValueError):
        known = {}
    stale = []
    for name in files:
        workbook = _output_workbook(project, name)
        if os.path.isfile(workbook) and known.get(os.path.basename(workbook)) != \
                calculation_fingerprint(workbook):
            stale.append(os.path.basename(workbook))
    return stale


DEFER_SPOLD_ENV = 'PIPELINE_DEFER_SPOLD'
# Likewise the split Excels: a run that ends by rebuilding them from the
# final datasets (split_edits.py --rebuild) skips Agri Diesel's own build.
DEFER_SPLIT_ENV = 'PIPELINE_DEFER_SPLIT'
SPLIT_STEP_SCRIPT = os.path.join(PIPELINE_DIR, 'split_edits.py')


def write_spold_for(workbook_path: str) -> bool:
    """
    Rewrite the .spold of a dataset workbook that was just saved.

    Called by the meta model and the three calculators after every save, so
    each output workbook always has its matching .spold in spold_output/
    beside it -- except inside a pipeline run that converts every workbook at
    its end (DEFER_SPOLD_ENV, set by run_steps).  Imported lazily: the
    converter loads its master-data lookups on import, which only a program
    that saves a workbook needs.
    """
    if os.environ.get(DEFER_SPOLD_ENV):
        return True
    import sys
    if SPOLD_CONVERTER_DIR_NEW not in sys.path:
        sys.path.insert(0, SPOLD_CONVERTER_DIR_NEW)
    import spold_converter_new
    return spold_converter_new.write_spold_for(workbook_path)
