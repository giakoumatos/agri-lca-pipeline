"""
Agricultural Diesel Calculator for New Datasets

Generic calculator for any SPOLD-based crop production dataset.
For each crop activity the calculator:

     1. Takes each field operation's (FO) diesel by the three cases of the
        diesel model (config_new_datasets.py, "Diesel model"):
          case 1  the provider's kg/ha for the FO (its comment) is used;
          case 2  the provider's TOTAL diesel is split over the FOs it covers,
                  z * x / (x + y + ...) with x, y the FOs' kg/ha;
          case 3  otherwise kg/ha from FO_DIESEL_INTENSITIES x passes / yield.
    2. Identifies FromTechnosphere inputs whose name matches a field operation
       in the intensity table (exact or partial match).
    3. Computes:
           diesel_per_kg_crop = fo_amount  ×  fo_diesel_intensity

    FO exchange amounts in new SPOLD datasets are expressed per kg of
    reference product. The calculator extracts crop yield, rounds the
    implied FO pass count with the Fuel Project pass-rounding rule, and
    writes the rounded FO amount back as rounded_passes / yield.
    4. Builds "diesel used in {fo_name}" FromTechnosphere rows
       with the Fuel Project DQS metadata and appends them (no duplicates).
    5. Saves consolidated output and splits into per-chunk files.

PIPELINE POSITION:
    Step 3 of 4  — runs after multinutrient_calculator_new.py

FO DIESEL INTENSITIES:
    config_new_datasets.py -> FO_DIESEL_INTENSITIES
    kg diesel per hectare for every field operation, keyed by (fo_name,
    geographies) with equal-intensity geographies collapsed onto one row.
    Regenerate from the raw ecoinvent exchanges dump with:
        python calculators/agri_diesel/build_fo_diesel_table.py --write

Author: Andreas Giakoumatos
Last Updated: 2026-09-09
"""

import os
import sys
import re
import json
import pandas as pd

# Warning/print messages echo arbitrary source data verbatim -- some contains
# characters Windows' console codepage can't encode, which would otherwise
# crash mid-run. Never let a print() abort real work.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
import numpy as np

# ── Configuration ─────────────────────────────────────────────────────────────
# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
from config_new_datasets import (
    MULTINUTRIENT_OUTPUT_FILE as INPUT_FILEPATH,
    AGRI_DIESEL_OUTPUT_FILE   as OUTPUT_FILEPATH,
    AGRI_DIESEL_SPLIT_DIR     as SPLIT_OUTPUT_DIR,
    AGRI_DIESEL_CHUNK_SIZE    as CHUNK_SIZE,
    FO_GEO_MAPPING_FILE,
    FO_DIESEL_INTENSITIES,
    MASTER_DATA_LOOKUP_FILE,
    REVIEW_COLS,
    complete_sheet_schema,
    record_deletions,
    write_spold_for,
    DEFER_SPLIT_ENV,
)
try:
    from config_new_datasets import (DIESEL_TOTAL_FLOWS, DIESEL_LHV_MJ_PER_KG,
                                     DIESEL_FO_ALIASES)
except ImportError:          # older config without the three-case diesel model
    DIESEL_TOTAL_FLOWS = ('diesel, burned in agricultural machinery',)
    DIESEL_LHV_MJ_PER_KG = 42.8
    DIESEL_FO_ALIASES = {}

# ── Master data (intermediateExchangeId / unitId) for "diesel used in ..." ───
# Keyed by the canonical ecoinvent flow name, "diesel used in {fo_name}" -- the
# same name this file now writes.  Older outputs carry a comma after "in", so
# the lookup still strips one if present.
if os.path.isfile(MASTER_DATA_LOOKUP_FILE):
    with open(MASTER_DATA_LOOKUP_FILE, encoding='utf-8') as _fh:
        _MASTER_DATA = json.load(_fh)
else:
    _MASTER_DATA = {}


def _diesel_master_data(diesel_row_name: str) -> dict:
    """Return {'intermediateExchangeId', 'unitId'} for a diesel row name, or {} if unknown."""
    canonical = diesel_row_name.replace('diesel used in, ', 'diesel used in ', 1)
    entry = _MASTER_DATA.get(canonical)
    if not entry:
        return {}
    return {'intermediateExchangeId': entry['intermediateExchangeId'], 'unitId': entry['unitId']}

# Geography fallback order when the crop geography has no direct FO entry
_GEO_FALLBACKS = ('GLO', 'RoW')

# DQS metadata for diesel rows — identical to Fuel Project crop_update.py
DIESEL_DQS = {
    'uncertainty type':             'lognormal',
    'variance':                     0.0006,
    'reliability':                  2,
    'completeness':                 4,
    'temporalCorrelation':          1,
    'geographicalCorrelation':      3,
    'furtherTechnologyCorrelation': 2,
    'comment': (
        'This flow represents the amount of kg of diesel consumed by the '
        'specific field operation.'
    ),
}

# ── Yield extraction ──────────────────────────────────────────────────────────
_NUMBER_PATTERN = r'[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?'
_YIELD_PATTERNS = [
    # The locked form the meta model writes -- "Yield: [26299.94] kg/ha",
    # or "Yield: [9000] kg/ha" where the figure is whole.
    # Tried first and matched whole, so a thousands separator or a stray second
    # decimal point cannot make the looser patterns below read part of the
    # number: "17.089,84" was being read as 89.84 and "2734.63.00" as 63.0.
    r'Yield:\s*\[(\d+(?:\.\d{2})?)\]\s*(kg/ha)',
    r'(?:yield\s+(?:is|of)|average\s+productivity\s+is|'
    r'production[\s-]+weighted\s+average\s+yield\s+is)'
    rf'\s*\[?({_NUMBER_PATTERN})\]?\s*(kg/ha|kg\s+per\s+hectare|t/ha|tons?\s+per\s+hectare|tonnes?/ha)',
    rf'[Yy]ield\s*[:\-]?\s*\[?({_NUMBER_PATTERN})\]?\s*(kg/ha|kg\s+per\s+hectare|t/ha|tons?\s+per\s+hectare|tonnes?/ha)',
    rf'\[?({_NUMBER_PATTERN})\]?\s*(kg/ha|kg\s+per\s+hectare|t/ha|tons?\s+per\s+hectare|tonnes?/ha)',
]


def _parse_number(s: str) -> float:
    """
    Parse a numeric string in English or European format.

    Supported formats
    -----------------
    English  : "26,299.94"  last sep = dot  → remove commas
    European : "17.089,84"  last sep = comma → remove dots, swap comma→dot
    Plain    : "26299.94" / "26299,94" / "26300"
    Typo     : "2734.63.00" → extracts first valid float "2734.63"
    """
    s = s.strip()
    # A comma followed by groups of exactly three digits is a THOUSANDS
    # separator: "12,582" is 12582, "1,900" is 1900 -- not 12.582 and 1.9.
    # A first group of 0 ("0,125") or any other grouping ("8604,16") is a
    # decimal comma, as before.  Same rule as meta_spec.parse_number_loose,
    # which writes the yield line this reads back.
    if re.match(r'^[1-9]\d{0,2}(?:,\d{3})+$', s):
        return float(s.replace(',', ''))
    last_dot   = s.rfind('.')
    last_comma = s.rfind(',')
    try:
        if last_dot > last_comma:
            # dot is decimal separator (English style)
            return float(s.replace(',', ''))
        elif last_comma > last_dot:
            # comma is decimal separator (European style)
            return float(s.replace('.', '').replace(',', '.'))
        else:
            return float(s)
    except ValueError:
        # Fallback: extract first valid float pattern (handles typos like "2734.63.00")
        m = re.match(r'(\d+(?:[.,]\d+)?)', s)
        if m:
            return _parse_number(m.group(1))
        raise


def extract_yield(comment: str) -> float | None:
    """Return yield in kg/ha from a generalComment string, or None."""
    if not isinstance(comment, str):
        return None
    for pattern in _YIELD_PATTERNS:
        m = re.search(pattern, comment, re.IGNORECASE)
        if m:
            try:
                value = _parse_number(m.group(1))
                unit  = m.group(2).lower()
                if any(u in unit for u in ('t/', 'ton', 'tonne')):
                    value *= 1000.0
                return value
            except (ValueError, IndexError):
                pass
    return None


def build_yield_map(df_meta: pd.DataFrame) -> dict[tuple, float]:
    """Return {(activityName, geography): yield_kg_per_ha}.

    Keys are stripped of leading/trailing whitespace so that minor formatting
    differences between the meta and exchanges sheets never cause a silent miss.
    Warns when the same key appears more than once with different yield values.
    """
    yield_map: dict[tuple, float] = {}
    if df_meta.empty or 'generalComment' not in df_meta.columns:
        return yield_map
    for _, row in df_meta.iterrows():
        an  = str(row.get('activityName', '') or '').strip()
        geo = str(row.get('geography',    '') or '').strip()
        key = (an, geo)
        y = extract_yield(str(row.get('generalComment', '') or ''))
        if y is None:
            continue
        if key in yield_map and yield_map[key] != y:
            print(f'   WARNING: duplicate yield key {key!r}: '
                  f'existing={yield_map[key]:.1f}, new={y:.1f} -- keeping new value')
        yield_map[key] = y
    return yield_map


# More passes than this in one year is implausible for a field operation --
# more likely a unit or yield error -- so it is flagged, not rounded.
MAX_PASSES = 8
_PASS_TOL = 1e-3    # amounts are stored to ~7 digits: 8 passes comes back as 8.000003


def map_passes(passes: float) -> float | None:
    """
    Round a continuous pass count to the nearest 0.5 increment (Fuel Project logic),
    for every field operation.  Interim rule: a field-operation model with
    its own cases is to replace this.
    Mirrors yield.py:map_passes() from the original Fuel Project.

    Returns None for NaN, negative values and anything above MAX_PASSES (8),
    which is flagged by the caller rather than rounded.
    """
    if passes is None or (isinstance(passes, float) and np.isnan(passes)) or passes < 0:
        return None
    if passes < 0.75:
        return 0.5
    elif passes <= 1.25:  return 1
    elif passes <= 1.75:  return 1.5
    elif passes <= 2.25:  return 2
    elif passes <= 2.75:  return 2.5
    elif passes <= 3.25:  return 3
    elif passes <= 3.75:  return 3.5
    elif passes <= 4.25:  return 4
    elif passes <= 4.75:  return 4.5
    elif passes <= 5.25:  return 5
    elif passes <= 5.75:  return 5.5
    elif passes <= 6.25:  return 6
    elif passes <= 6.75:  return 6.5
    elif passes <= 7.25:  return 7
    elif passes <= 7.75:  return 7.5
    elif passes <= MAX_PASSES + _PASS_TOL:  return 8
    else:
        return None  # above MAX_PASSES: implausible, flagged by the caller


# ── FO pass extraction from dataset comments ─────────────────────────────────

_PASS_UNIT = r'(?:passes?|applications?|treatments?)'
_PASS_COUNT_PATTERN = rf'\b({_NUMBER_PATTERN})\s+{_PASS_UNIT}\b'
# "number of passes is[:] <values>" up to the end of the sentence (". " before
# a capital -- not a decimal point).  "number of passes per yield are ... ha"
# is an area, not a count, and does not match.
_STATED_PASSES_PATTERN = re.compile(
    r'number\s+of\s+passes\s+is\s*:?\s*(?P<values>.*?)(?:\.\s+(?=[A-Z])|\.?\s*$)',
    re.IGNORECASE | re.DOTALL)
_HIGH_LOW_PASS_PATTERN = (
    rf'\b({_NUMBER_PATTERN})\s+{_PASS_UNIT}\s+during\s+high[-\s]yield\s+years\s+'
    rf'and\s+({_NUMBER_PATTERN})\s+{_PASS_UNIT}\s+during\s+low[-\s]yield\s+years\b'
)

# The sentence this calculator writes into an FO comment to record the pass
# count its amount was built from.  The original wording ("3 treatments per
# hectare") normally sat inside the diesel clause that
# strip_diesel_from_fo_comment() removes, so without this the FO comment would
# no longer say how many passes the amount stands for.
# "per hectare" is optional so a sentence written by an earlier version is
# still recognised, and gets replaced rather than duplicated.
_FO_APPLICATIONS_SENTENCE = re.compile(
    rf'\s*The\s+quantity\s+corresponds\s+to\s+(?P<passes>{_NUMBER_PATTERN})\s+'
    rf'applications?(?:\s+per\s+hectare)?\s*\.?',
    re.IGNORECASE,
)


def extract_fo_passes_from_comment(comment: str) -> float | None:
    """Return explicit FO passes/applications/treatments from an exchange comment."""
    if not isinstance(comment, str):
        return None

    # A sentence this calculator wrote itself already carries the rounded pass
    # count the amount was built from: take it as-is.  It must never be summed
    # with other mentions still in the comment, nor pushed back through the
    # rounding rule -- either would change the amount on a re-run.
    own = _FO_APPLICATIONS_SENTENCE.search(comment)
    if own:
        try:
            return _parse_number(own.group('passes'))
        except ValueError:
            pass

    high_low_match = re.search(_HIGH_LOW_PASS_PATTERN, comment, re.IGNORECASE)
    if high_low_match:
        try:
            return (_parse_number(high_low_match.group(1))
                    + _parse_number(high_low_match.group(2))) / 2.0
        except ValueError:
            return None

    # "The production weighted average number of passes is 0.017493" -- a
    # pass count the provider states, used as given.  One sentence may list
    # one per sub-operation ("is: 1.168539 for pre plant herbicide, 0.875518
    # for post emergent herbicide, ..."); together they are the FO's passes.
    stated = _STATED_PASSES_PATTERN.search(comment)
    if stated:
        # The first figure is the count.  Only when it is a list ("1.17 for
        # ..., 0.88 for ...") are the other figures "for" something part of
        # it -- any other number further on (a yield) is not a pass count.
        number = rf'(?<![\d.,])({_NUMBER_PATTERN})(?![\d.])'
        tokens = list(re.finditer(rf'{number}(?P<for>\s+for\b)?', stated.group('values')))
        if tokens:
            chosen = ([t for t in tokens if t.group('for')] if tokens[0].group('for')
                      else tokens[:1])
            try:
                return sum(_parse_number(t.group(1)) for t in chosen)
            except ValueError:
                pass

    values = []
    for match in re.finditer(_PASS_COUNT_PATTERN, comment, re.IGNORECASE):
        try:
            values.append(_parse_number(match.group(1)))
        except ValueError:
            continue
    return sum(values) if values else None


# ── FO diesel intensity extraction from dataset comments ─────────────────────

_FO_DIESEL_NUMBER = rf'({_NUMBER_PATTERN})'

# The trailing "original consumption ... was Y kg of diesel" sentence names the
# UNSCALED reference-dataset figure -- not applicable to this FO exchange. It
# must be stripped out before looking for the applicable consumption value(s),
# since it also matches the "kg of diesel" wording searched for below.
_FO_ORIGINAL_CONSUMPTION_PATTERNS = [
    rf'original\s+(?:diesel\s+)?consumption\s+in\s+the\s+(?:dataset\s+selected|selected\s+dataset)\s+was\s+{_FO_DIESEL_NUMBER}\s*kg(?:\s+of\s+diesel|\s+diesel)?',
    rf'original\s+consumption\s+in\s+the\s+(?:dataset\s+selected|selected\s+dataset)\s+was\s+{_FO_DIESEL_NUMBER}\s*kg(?:\s+of\s+diesel|\s+diesel)?',
    rf'original\s+(?:diesel\s+)?consumption\s+was\s+{_FO_DIESEL_NUMBER}\s*kg(?:\s+of\s+diesel|\s+diesel)?',
]

# The applicable, already-scaled-for-this-FO-and-crop figure(s). Comments use
# several phrasings; each pattern below is tried in turn (most specific first)
# and, when it matches, fully determines the result -- patterns are mutually
# exclusive across the comment styles actually seen in these datasets.

# "average annual diesel consumption (22 kg during high-yield years and 10 kg
# during low-yield years)" -- the applicable figure is the annual AVERAGE.
_SCALED_DIESEL_HIGH_LOW_PATTERN = (
    rf'diesel\s+consumption\s*\(\s*{_FO_DIESEL_NUMBER}\s*kg\s+during\s+high[-\s]yield\s+years\s+'
    rf'and\s+{_FO_DIESEL_NUMBER}\s*kg\s+during\s+low[-\s]yield\s+years'
)

# "diesel consumption (4.45 kg × 2 applications of pest control and 4.9 kg ×
# 1 application of localized weeding per hectare)" -- sum of the stated kg
# figures ONLY. The "x N applications" multiplier is a treatment/pass count,
# which must never scale the diesel amount -- it only affects FO amount
# (via pass-rounding), so it is captured here but deliberately discarded.
_SCALED_DIESEL_PRODUCT_PATTERN = (
    rf'{_FO_DIESEL_NUMBER}\s*kg\s*[x×]\s*{_FO_DIESEL_NUMBER}\s*applications?\b'
)

# "consumption of 23 kg of diesel (3 treatments per hectare)" or, for FOs
# split into sub-operations, "1.184 kg of diesel for the first operation ...
# and 2.066 kg of diesel for the second operation ...". Multiple matches are
# summed to get the total per-hectare consumption for this exchange.
_SCALED_DIESEL_NUMBER_PATTERN = rf'{_FO_DIESEL_NUMBER}\s*kg\s+of\s+diesel\b'

# "diesel consumption (59 kg)" / "diesel consumption (9.2 kg, corresponding
# to ...)" -- single figure, no "of diesel" wording.
_SCALED_DIESEL_PAREN_PATTERN = rf'diesel\s+consumption\s*\(\s*{_FO_DIESEL_NUMBER}\s*kg\b'


def extract_fo_diesel_intensity_from_comment(comment: str) -> float | None:
    """
    Return the kg-diesel-per-ha consumption applicable to this FO exchange,
    from the comment's "...scaled to account for ... the consumption of X kg
    of diesel..." clause -- NOT the "original consumption ... was Y kg of
    diesel" figure, which names the unscaled reference dataset's value and is
    not what actually applies to this specific field operation / crop.
    """
    if not isinstance(comment, str) or 'diesel' not in comment.lower():
        return None

    masked = comment
    for pattern in _FO_ORIGINAL_CONSUMPTION_PATTERNS:
        masked = re.sub(pattern, '', masked, flags=re.IGNORECASE)

    high_low = re.search(_SCALED_DIESEL_HIGH_LOW_PATTERN, masked, re.IGNORECASE)
    if high_low:
        try:
            return (_parse_number(high_low.group(1)) + _parse_number(high_low.group(2))) / 2.0
        except ValueError:
            pass

    products = list(re.finditer(_SCALED_DIESEL_PRODUCT_PATTERN, masked, re.IGNORECASE))
    if products:
        # This FO's effective_amount is passes-per-year / yield (total applications
        # of ALL kinds), so the intensity multiplied against it must be a per-pass
        # rate blended across the different kg-per-application figures --
        # weighted by how many applications each one covers, not a flat sum.
        weighted_sum = 0.0
        total_applications = 0.0
        for match in products:
            try:
                kg = _parse_number(match.group(1))
                applications = _parse_number(match.group(2))
            except ValueError:
                continue
            weighted_sum += kg * applications
            total_applications += applications
        if total_applications > 0:
            return weighted_sum / total_applications

    total = 0.0
    found = False
    for match in re.finditer(_SCALED_DIESEL_NUMBER_PATTERN, masked, re.IGNORECASE):
        try:
            value = _parse_number(match.group(1))
        except ValueError:
            continue
        if value > 0:
            total += value
            found = True
    if found:
        return total

    paren = re.search(_SCALED_DIESEL_PAREN_PATTERN, masked, re.IGNORECASE)
    if paren:
        try:
            value = _parse_number(paren.group(1))
        except ValueError:
            value = None
        if value is not None and value > 0:
            return value

    # Fallback: comment only states the original/reference-dataset value
    # (no scaled figure present) -- best available number.
    for pattern in _FO_ORIGINAL_CONSUMPTION_PATTERNS:
        match = re.search(pattern, comment, re.IGNORECASE)
        if not match:
            continue
        try:
            value = _parse_number(match.group(1))
        except ValueError:
            continue
        if value > 0:
            return value
    return None


# Case 1, second form: the provider states the FO's fuel use as ENERGY PER KG
# OF PRODUCT rather than kg per hectare, e.g. "The estimated production-
# weighted average for fuel use for seeding/planting is 0.154751 MJ."  Such a
# figure is already the diesel of the dataset's reference flow, so it is used
# as it is -- converted from MJ at DIESEL_LHV_MJ_PER_KG -- with no passes and
# no kg/ha involved.  One sentence may list several figures ("... for pre-plant
# herbicide application is 0.015984 MJ, for post emergent herbicide
# application is 0.014606 MJ, ..."); together they are the FO's fuel use.
# A sentence ends at ". " before a capital -- not at a decimal point.
_SENTENCE_SPLIT = re.compile(r'\.\s+(?=[A-Z])')
_FO_FUEL_TOPIC = re.compile(r'\b(?:fuel|diesel)\s+(?:use|consumption)\b', re.IGNORECASE)
_FO_FUEL_MJ = re.compile(
    rf'\bis\s+({_NUMBER_PATTERN})\s*MJ\b(?!\s*(?:/|per)\s*(?:ha|hectare))', re.IGNORECASE)


def extract_fo_fuel_mj_per_kg(comment: str) -> float | None:
    """MJ of fuel per kg of product the FO comment states, or None.  A figure
    given per hectare ('MJ/ha') is not this form and is not read here."""
    if not isinstance(comment, str) or 'mj' not in comment.lower():
        return None
    total, found = 0.0, False
    for sentence in _SENTENCE_SPLIT.split(comment):
        if not _FO_FUEL_TOPIC.search(sentence):
            continue
        for match in _FO_FUEL_MJ.finditer(sentence):
            try:
                value = _parse_number(match.group(1))
            except ValueError:
                continue
            if value > 0:
                total += value
                found = True
    return total if found else None


# ── Moving the diesel statement from the FO comment to the diesel row ────────
# When the intensity is taken from an FO exchange comment, that figure now
# lives on the generated "diesel used in, ..." row, so the FO comment must stop
# claiming it.  Only the diesel wording is removed: the same sentence also
# records that the amount was scaled to the crop yield, which describes the FO
# amount and has nothing to do with diesel.
#
#   before  The quantity has been scaled to account for both the consumption
#           of 23 kg of diesel (3 treatments per hectare) and olive yields.
#           The original consumption in the dataset selected was 26.1 kg of
#           diesel.
#   after   The quantity has been scaled to account for olive yields.

# "... to account for [both] <diesel clause> and <crop> yields" -> the crop
# yields alone.  The diesel clause itself may contain "and" ("... for the first
# operation and 2.066 kg of diesel for the second operation"), so the lazy
# middle group backtracks until the trailing "and <crop> yields" fits.
_FO_SCALED_CLAUSE_PATTERN = re.compile(
    r'to\s+account\s+for\s+(?:both\s+)?(?P<mid>.*?)\s+and\s+'
    r'(?P<tail>(?:[A-Za-z][A-Za-z-]*\s+){0,3}yields)\b',
    re.IGNORECASE,
)

# The standalone "The original consumption in the dataset selected was X kg of
# diesel." sentence -- pure diesel provenance, removed whole.
_FO_ORIGINAL_SENTENCE_PATTERN = re.compile(
    rf'\s*(?:The\s+)?original\s+(?:diesel\s+)?consumption\s+'
    rf'(?:in\s+the\s+(?:dataset\s+selected|selected\s+dataset)\s+)?was\s+'
    rf'{_FO_DIESEL_NUMBER}\s*kg(?:\s+of\s+diesel|\s+diesel)?\s*\.?',
    re.IGNORECASE,
)


def strip_diesel_from_fo_comment(comment: str) -> str:
    """
    Return the FO comment with its diesel statement removed, everything else
    left intact.  Returns the comment unchanged if it says nothing about diesel.
    """
    if not isinstance(comment, str) or 'diesel' not in comment.lower():
        return comment if isinstance(comment, str) else ''

    def _drop_diesel_clause(match: re.Match) -> str:
        # Only rewrite when the part being dropped is the diesel clause
        if 'diesel' not in match.group('mid').lower():
            return match.group(0)
        return f"to account for {match.group('tail')}"

    out = _FO_SCALED_CLAUSE_PATTERN.sub(_drop_diesel_clause, comment)
    out = _FO_ORIGINAL_SENTENCE_PATTERN.sub('', out)

    # Tidy the seams left by the removals
    out = re.sub(r'\s+([.,;])', r'\1', out)   # " ." -> "."
    out = re.sub(r'\.\s*\.', '.', out)         # ".." -> "."
    out = re.sub(r'[ \t]{2,}', ' ', out)
    return out.strip()


def _format_passes(passes: float) -> str:
    """Format a pass count for prose: 3.0 -> '3', 2.5 -> '2.5'."""
    return f'{passes:g}'


def set_fo_applications(comment: str, passes: float) -> str:
    """
    Return the FO comment stating the pass count its amount represents.

    Any sentence written by an earlier run is replaced rather than duplicated,
    so the comment stays correct when the calculator is re-run over its own
    output.
    """
    text = comment if isinstance(comment, str) else ''
    text = _FO_APPLICATIONS_SENTENCE.sub('', text).strip()

    count = _format_passes(passes)
    noun  = 'application' if abs(passes - 1.0) < 1e-9 else 'applications'
    sentence = f'The quantity corresponds to {count} {noun}.'

    if not text:
        return sentence
    if not text.endswith('.'):
        text += '.'
    return f'{text} {sentence}'


def _format_intensity(value: float) -> str:
    """Format a kg/ha figure without float noise (3.3790000000000004 -> 3.379)."""
    return f'{value:.6g}'


_DIESEL_COMMENT_INTENSITY_SENTENCE = re.compile(
    # The value list is captured lazily up to "kg per hectare"; it must allow
    # the decimal point, so it cannot be written as [^.]*.
    # Ours: "... of this field operation is X kg per hectare"; the data
    # providers' own rows (case 1): "... for this field operation is X kg/ha".
    r'diesel\s+consumption\s+(?:of|for)\s+this\s+field\s+operation\s+is\s+(?P<values>.*?)\s*'
    r'kg\s*(?:per\s+hectare|/\s*ha)',
    re.IGNORECASE,
)


def parse_diesel_comment_intensity(comment: str) -> float | None:
    """
    Read back the per-hectare figure this calculator wrote into a diesel row's
    comment, or None.

    The calculator is re-runnable over its own output, but by then the FO
    comment it originally read the figure from has been cleaned, so the figure
    would otherwise be lost and silently replaced by the FO_DIESEL_INTENSITIES
    value.  Recovering it here keeps repeated runs idempotent.
    """
    if not isinstance(comment, str):
        return None
    match = _DIESEL_COMMENT_INTENSITY_SENTENCE.search(comment)
    if not match:
        return None
    values = []
    for token in re.finditer(_NUMBER_PATTERN, match.group('values')):
        try:
            values.append(_parse_number(token.group(0)))
        except ValueError:
            continue
    # Several figures mean several FO rows were summed into this flow; they
    # cannot be attributed back to individual rows, so leave those to the table.
    return values[0] if len(values) == 1 else None


def diesel_comment(intensities: list[float] | None = None) -> str:
    """
    Comment for a generated diesel row: the standard sentence, plus the
    per-hectare figure when it was read off the FO comment (and so no longer
    appears there).  Rows whose intensity came from the FO_DIESEL_INTENSITIES
    table keep the standard sentence alone -- that value is standard ecoinvent
    data and was never written in the dataset.
    """
    standard = DIESEL_DQS['comment']
    values = sorted({float(v) for v in (intensities or []) if v is not None})
    if not values:
        return standard
    figures = ' and '.join(_format_intensity(v) for v in values)
    return (f'{standard} The diesel consumption of this field operation is '
            f'{figures} kg per hectare.')


# ── FO diesel intensity table ─────────────────────────────────────────────────

def load_fo_diesel_intensities() -> dict[tuple[str, str], float]:
    """
    Expand the FO_DIESEL_INTENSITIES config table into a lookup map:

        (fo_activity_name_lower, geography) -> kg_diesel_per_hectare

    The table stores one row per distinct intensity, with every geography
    sharing that value pipe-joined into the key ("CH|GLO|RoW"), so this splits
    those back out into one entry per geography.

    Regenerate the table with calculators/agri_diesel/build_fo_diesel_table.py
    when the ecoinvent release changes -- the pipeline itself never reads the
    87 MB exchanges dump.
    """
    intensity_map: dict[tuple[str, str], float] = {}
    for (fo_name, geographies), value in FO_DIESEL_INTENSITIES.items():
        key = str(fo_name).strip().lower()
        for geo in str(geographies).split('|'):
            geo = geo.strip()
            if geo:
                intensity_map[(key, geo)] = float(value)
    if not intensity_map:
        print("  WARNING: FO_DIESEL_INTENSITIES is empty -- no diesel rows can be\n"
              "           computed from the table. Regenerate it with\n"
              "           calculators/agri_diesel/build_fo_diesel_table.py --write")
    return intensity_map


# ── FO geography mapping ──────────────────────────────────────────────────────

def load_fo_geo_map(path: str) -> dict[tuple[str, str], str]:
    """
    Load FO geography overrides from a CSV mapping file.
    Returns {(fo_name_lower, crop_geography): fo_db_geography}.

    Edit fo_geography_map.csv to control which FO geography is used for a
    specific FO + crop geography combination instead of relying on auto-fallback.
    Columns: fo_name, crop_geography, fo_db_geography
    """
    geo_map: dict[tuple[str, str], str] = {}
    if not os.path.exists(path):
        return geo_map
    try:
        df = pd.read_csv(path, dtype=str)
        for _, row in df.iterrows():
            fo = str(row.get('fo_name', '')).strip().lower()
            cg = str(row.get('crop_geography', '')).strip()
            fg = str(row.get('fo_db_geography', '')).strip()
            if fo and cg and fg:
                geo_map[(fo, cg)] = fg
    except Exception as e:
        print(f"  WARNING: could not read FO geography map ({e})")
    return geo_map


def save_fo_geo_map(path: str, selections: list[tuple[str, str, str]]) -> None:
    """
    Merge new (fo_name, crop_geography, fo_db_geography) auto-selections with
    any existing entries in the map file and save.  User-edited entries are
    never overwritten — only new combinations are appended.
    """
    existing = load_fo_geo_map(path)
    for fo_name, crop_geo, fo_db_geo in selections:
        k = (fo_name.strip().lower(), crop_geo.strip())
        if k not in existing:
            existing[k] = fo_db_geo
    rows = [
        {'fo_name': fo, 'crop_geography': cg, 'fo_db_geography': fg}
        for (fo, cg), fg in sorted(existing.items())
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"  FO geography map updated: {path}  ({len(rows)} entries)")


def _lookup_diesel_intensity(
    fo_name: str,
    crop_geo: str,
    intensity_map: dict[tuple[str, str], float],
    geo_map: dict[tuple[str, str], str] | None = None,
) -> tuple[float | None, str | None]:
    """
    Geography-aware diesel intensity lookup.

    If geo_map has an explicit entry for (fo_name_lower, crop_geo), that
    fo_db_geography is used directly (user override from fo_geography_map.csv).
    Otherwise the automatic fallback order is:
      1. Exact (fo_name, crop_geo) match.
      2. Partial name match with exact crop_geo.
      3. Exact/partial name match with GLO or RoW geography.
      4. Median across all geographies for any matching FO name.

    Returns (intensity, fo_db_geography_used) or (None, None) if no match.
    """
    key = fo_name.strip().lower()

    def name_matches(db_key: str) -> bool:
        return db_key == key or db_key in key or key in db_key

    # 0. Explicit mapping override
    if geo_map is not None:
        mapped_geo = geo_map.get((key, crop_geo))
        if mapped_geo:
            if (key, mapped_geo) in intensity_map:
                return intensity_map[(key, mapped_geo)], mapped_geo
            for (db_key, db_geo), val in intensity_map.items():
                if db_geo == mapped_geo and name_matches(db_key):
                    return val, mapped_geo

    # 1. Exact (name, geography)
    if (key, crop_geo) in intensity_map:
        return intensity_map[(key, crop_geo)], crop_geo

    # 2. Partial name, exact geography
    for (db_key, db_geo), val in intensity_map.items():
        if db_geo == crop_geo and name_matches(db_key):
            return val, crop_geo

    # 3. GLO / RoW fallback
    for fallback_geo in _GEO_FALLBACKS:
        for (db_key, db_geo), val in intensity_map.items():
            if db_geo == fallback_geo and name_matches(db_key):
                return val, fallback_geo

    # 4. Median across all geographies for this FO
    candidates = [(v, db_geo) for (db_key, db_geo), v in intensity_map.items() if name_matches(db_key)]
    if candidates:
        return float(np.median([v for v, _ in candidates])), 'median'

    return None, None


# ── Case 2: one total diesel consumption, split over the FOs it covers ──────
# See DIESEL_TOTAL_FLOWS in config_new_datasets.py.  The split is by kg/ha
# alone -- z * x / (x + y + ...) -- as the method states it.

# Written into every case-2 diesel row; read back on a re-run, when the total
# exchange it came from has already been replaced.
_CASE2_MARKER = 'Share of the total diesel consumption given by the data provider'
_CASE2_RE = re.compile(re.escape(_CASE2_MARKER) + r'\s*\((?P<z>' + _NUMBER_PATTERN + r')\s*kg', re.I)

# Words that name an operation.  A total's comment naming one of these that
# matches no FO of the dataset and no alias cannot be split by the method.
_OPERATION_WORDS = {
    'harvesting', 'pruning', 'mowing', 'mulching', 'spraying', 'shredding',
    'sowing', 'planting', 'ploughing', 'plowing', 'tillage', 'harrowing',
    'fertilising', 'fertilizing', 'hoeing', 'weeding', 'threshing', 'baling',
    'chopping', 'cultivating', 'disking', 'rolling', 'subsoiling', 'swathing',
    'drying', 'grinding', 'irrigating', 'seeding', 'topping', 'thinning',
}


def _canonical_total_name(name) -> str:
    """'Diesel, burned in agricultural machinery {GLO}| ... | Cut-off, S'
    -> 'diesel, burned in agricultural machinery'."""
    return str(name).split('{')[0].strip().lower()


def _fo_name_of(row, has_link_col: bool) -> str:
    fo_name = str(row.get('activityLink_activityName', '')) if has_link_col else ''
    if not fo_name or fo_name.lower() in ('nan', ''):
        fo_name = str(row.get('name', ''))
    return '' if fo_name.lower() in ('nan', '') else fo_name.strip()


def _mentions(text: str, phrase: str) -> bool:
    return bool(re.search(r'\b' + re.escape(phrase.lower()) + r'\b', text))


def plan_total_diesel(df_exchanges: pd.DataFrame, df_ft: pd.DataFrame, has_link_col: bool,
                      own_diesel: set, intensity_map: dict, geo_map: dict,
                      recovered_case2: dict) -> tuple[dict, list, list]:
    """
    Returns (plan, remove_idx, flags).

    plan        {(crop, geo): {fo_name: (amount_kg_per_kg, comment)}} -- the
                case-2 diesel of every FO the total covers, the dataset's own
                FOs and named FOs it lacks alike
    remove_idx  the total exchanges that were split (they are replaced)
    flags       (crop, message) for every total that could not be split
    """
    plan: dict = {}
    remove_idx: list = []
    flags: list = []
    if 'name' not in df_exchanges.columns or 'group' not in df_exchanges.columns:
        return plan, remove_idx, flags
    totals_wanted = {t.lower() for t in DIESEL_TOTAL_FLOWS}
    is_total = (df_exchanges['group'] == 'FromTechnosphere') & \
        df_exchanges['name'].map(_canonical_total_name).isin(totals_wanted)
    totals = df_exchanges[is_total]

    fos_by_activity: dict = {}
    for _, row in df_ft.iterrows():
        fo = _fo_name_of(row, has_link_col)
        if fo and not fo.lower().startswith('diesel used in'):
            key = (str(row.get('activityName', '') or '').strip(),
                   str(row.get('geography', '') or '').strip())
            fos_by_activity.setdefault(key, [])
            if fo not in fos_by_activity[key]:
                fos_by_activity[key].append(fo)

    aliases = {str(k).strip().lower(): str(v).strip() for k, v in (DIESEL_FO_ALIASES or {}).items()}

    for key, group in totals.groupby([totals['activityName'].astype(str).str.strip(),
                                      totals['geography'].astype(str).str.strip()]):
        crop, geo = key
        z_kg, units_ok, described = 0.0, True, []
        for _, t in group.iterrows():
            amount = pd.to_numeric(t.get('amount'), errors='coerce')
            unit = str(t.get('unitName', '') or '').strip()
            if pd.isna(amount):
                continue
            if unit.lower() == 'mj':
                z_kg += float(amount) / DIESEL_LHV_MJ_PER_KG
                described.append(f'{float(amount):.6g} MJ')
            elif unit.lower() == 'kg':
                z_kg += float(amount)
                described.append(f'{float(amount):.6g} kg')
            else:
                units_ok = False
                flags.append((crop, f"total diesel in {unit!r} -- only MJ or kg can be converted"))
        if not units_ok or z_kg <= 0:
            continue
        comment_text = ' '.join(str(c).lower() for c in group.get('comment', pd.Series(dtype=str))
                                if isinstance(c, str))
        dataset_fos = fos_by_activity.get(key, [])

        # The FOs the comment names: the dataset's own (by full name or by the
        # part before the first comma), then the alias map.
        named: list = []
        covered_words: set = set()
        for fo in dataset_fos:
            head = fo.split(',')[0].strip()
            if _mentions(comment_text, fo) or (head and _mentions(comment_text, head)):
                named.append(fo)
                covered_words |= set(re.findall(r'\w+', fo.lower()))
        for word, fo in aliases.items():
            if _mentions(comment_text, word) and fo not in named:
                named.append(fo)
                covered_words |= set(re.findall(r'\w+', word)) | set(re.findall(r'\w+', fo.lower()))
        unresolved = sorted({w for w in re.findall(r'[a-z]+', comment_text)
                             if w in _OPERATION_WORDS and w not in covered_words})
        if unresolved:
            flags.append((crop, f"total diesel ({', '.join(described)}) names "
                                f"{', '.join(repr(w) for w in unresolved)}, which matches no field "
                                f"operation of the dataset -- add it to DIESEL_FO_ALIASES "
                                f"(config_new_datasets.py) to split the total; left as it is"))
            continue
        targets = [fo for fo in (named or dataset_fos) if fo.lower() not in own_diesel_for(own_diesel, key)]
        if not targets:
            flags.append((crop, f"total diesel ({', '.join(described)}): no field operation "
                                f"without its own diesel to split it over -- left as it is"))
            continue
        intensities = {}
        for fo in targets:
            x, _db_geo = _lookup_diesel_intensity(fo, geo, intensity_map, geo_map)
            intensities[fo] = x
        missing = [fo for fo, x in intensities.items() if not x]
        if missing and not named:
            # Nothing named: the total covers the dataset's diesel-using FOs.
            # An ha row with no kg/ha (land use change, a motor mower) uses no
            # diesel by the table either, so under z * x / sum(x) its share
            # is zero -- it is left out and said so.
            intensities = {fo: x for fo, x in intensities.items() if x}
            flags.append((crop, f"total diesel ({', '.join(described)}): no kg/ha for "
                                f"{', '.join(repr(m) for m in missing)}, so no share for "
                                f"{'it' if len(missing) == 1 else 'them'} (split over the rest)"))
            missing = [] if intensities else missing
        if missing:
            flags.append((crop, f"total diesel ({', '.join(described)}): no kg/ha for "
                                f"{', '.join(repr(m) for m in missing)} in FO_DIESEL_INTENSITIES, "
                                f"so the split cannot be computed -- left as it is"))
            continue
        sum_x = sum(intensities.values())
        converted = (f', converted at {DIESEL_LHV_MJ_PER_KG:g} MJ/kg'
                     if any(d.endswith('MJ') for d in described) else '')
        plan[key] = {}
        for fo, x in intensities.items():
            share = z_kg * x / sum_x
            plan[key][fo] = (share, (
                f"{DIESEL_DQS['comment']} {_CASE2_MARKER} ({z_kg:.10g} kg per kg of product"
                f"{converted}), allocated in proportion to the diesel intensity of the "
                f"field operation ({_format_intensity(x)} of {_format_intensity(sum_x)} "
                f"kg per hectare)."))
        remove_idx.extend(group.index.tolist())

    # A re-run over our own output: the total is gone, its shares survive in
    # the diesel rows written last time.
    recovered: dict = {}
    for (crop, geo, name), (amount, comment) in recovered_case2.items():
        if (crop, geo) in plan:
            continue          # split afresh from a total still present
        fo = name[len('diesel used in '):] if name.lower().startswith('diesel used in ') else name
        recovered.setdefault((crop, geo), {})[fo] = (amount, comment)
    plan.update(recovered)
    return plan, remove_idx, flags


def own_diesel_for(own_diesel: set, key: tuple) -> set:
    """The (lower-case) FOs of one activity that carry their own diesel."""
    return {fo for (crop, geo, fo) in own_diesel if (crop, geo) == key}


# ── Dataset splitting ─────────────────────────────────────────────────────────

def split_part_name(project: str, number: int) -> str:
    """The file name of a project's split part: letters, digits and _ only
    ('grass, AR' -> grass_AR_part_1.xlsx)."""
    safe = re.sub(r'[^0-9A-Za-z]+', '_', project).strip('_') or 'project'
    return f"{safe}_part_{number}.xlsx"


def split_crop_datasets(df_exchanges: pd.DataFrame, df_sheets: dict,
                        split_dir: str | None = None, project: str | None = None) -> None:
    split_dir = split_dir or SPLIT_OUTPUT_DIR
    # The parts are named after the project ("grass, AR - part 1.xlsx"), so a
    # part opened on its own says whose datasets it holds.  Without one given,
    # the project is the folder above crop_datasets_new (output/<project>/).
    project = project or os.path.basename(os.path.dirname(os.path.abspath(split_dir)))
    os.makedirs(split_dir, exist_ok=True)
    # A row with no activityName belongs to no dataset.  Left in, it forms a
    # chunk key of its own and the split reports an activity that does not
    # exist, so those rows are not allowed to define a chunk.
    unique_keys = (
        df_exchanges[['activityName', 'geography']]
        .dropna(subset=['activityName'])
        .drop_duplicates()
        .sort_values(by=['activityName', 'geography'])
        .reset_index(drop=True)
    )
    num_chunks = (len(unique_keys) + CHUNK_SIZE - 1) // CHUNK_SIZE
    print(f"  Splitting into {num_chunks} chunk(s) of <={CHUNK_SIZE} activities...")
    for i in range(num_chunks):
        chunk_keys = unique_keys.iloc[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]
        exch_chunk = df_exchanges.merge(chunk_keys, on=['activityName', 'geography'],
                                        how='inner').drop_duplicates()
        filtered = {}
        for sn, sd in df_sheets.items():
            if sn == 'exchanges':
                filtered[sn] = exch_chunk
            elif 'activityName' in sd.columns and 'geography' in sd.columns:
                filtered[sn] = sd.merge(chunk_keys, on=['activityName', 'geography'],
                                        how='inner').drop_duplicates()
            else:
                filtered[sn] = sd
        complete_sheet_schema(filtered)
        split_file = os.path.join(split_dir, split_part_name(project, i + 1))
        with pd.ExcelWriter(split_file, engine='openpyxl') as writer:
            for sn, sd in filtered.items():
                if sn == 'review' and sd.columns.empty:
                    sd = pd.DataFrame(columns=REVIEW_COLS)
                sd.to_excel(writer, sheet_name=sn, index=False)
        print(f"    Part {i+1}/{num_chunks}: {len(chunk_keys)} activities -> {split_file}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(_input_path=None, _output_path=None, _split_dir=None, _intensity_map=None,
         _skip_split=False):
    _input_path  = _input_path  or INPUT_FILEPATH
    _output_path = _output_path or OUTPUT_FILEPATH
    _split_dir   = _split_dir   or SPLIT_OUTPUT_DIR
    os.makedirs(os.path.dirname(_output_path), exist_ok=True)

    # ── 1. Load multinutrient output ─────────────────────────────────────────────────
    print("\n1. Loading multinutrient output...")
    df_sheets    = pd.read_excel(_input_path, sheet_name=None)
    df_exchanges = df_sheets.get('exchanges', pd.DataFrame()).copy()
    df_meta      = df_sheets.get('meta',      pd.DataFrame()).copy()
    yield_map    = build_yield_map(df_meta)
    print(f"   {len(df_exchanges)} exchange records, {len(df_meta)} meta records.")
    print(f"   Yield values extracted: {len(yield_map)}")

    # ── 2. Expand the FO diesel intensity table (skip if pre-loaded in batch mode) ─
    if _intensity_map is not None:
        intensity_map = _intensity_map
        print(f"\n2. Using pre-loaded FO diesel intensities ({len(intensity_map)} entries).")
    else:
        print("\n2. Loading field-operation diesel intensities from config table...")
        intensity_map = load_fo_diesel_intensities()
        print(f"   {len(FO_DIESEL_INTENSITIES)} table rows -> "
              f"{len(intensity_map)} (field operation, geography) entries.")

    # ── 2b. Load FO geography map (user-editable overrides) ──────────────────────
    geo_map = load_fo_geo_map(FO_GEO_MAPPING_FILE)
    if geo_map:
        print(f"   FO geography overrides: {len(geo_map)} entry(ies) from {FO_GEO_MAPPING_FILE}")
    else:
        print(f"   No FO geography overrides (auto-selection will be used).")

    # ── 3. Identify FromTechnosphere FO inputs and compute diesel rows ─────────
    print("\n3. Computing diesel rows from field-operation inputs...")

    diesel_new_rows: list[dict] = []
    geo_selections:  list[tuple] = []   # (fo_name, crop_geo, fo_db_geo) per matched FO
    skipped_no_intensity = 0
    matched_from_comment  = 0
    matched_from_db       = 0
    pass_rounding_applied = 0
    pass_rounding_skipped = 0
    passes_flagged: list = []   # (activity, operation, passes, source) above MAX_PASSES
    pass_count_from_comment = 0
    fo_comments_stripped = 0
    fo_applications_noted = 0
    matched_fos          = 0
    master_data_matched  = 0
    recovered_from_diesel_row = 0
    master_data_missing_names: set[str] = set()

    # Intensities this calculator wrote into diesel rows on an earlier run over
    # the same file.  The FO comments they came from have been cleaned by now,
    # so these are the only surviving record of a dataset-specific figure.
    recovered_intensities: dict[tuple[str, str, str], float] = {}
    if 'comment' in df_exchanges.columns and 'name' in df_exchanges.columns:
        prior = df_exchanges[
            df_exchanges['name'].astype(str).str.lower().str.startswith('diesel used in')
        ]
        for _, prior_row in prior.iterrows():
            value = parse_diesel_comment_intensity(str(prior_row.get('comment', '') or ''))
            if value is not None:
                recovered_intensities[(
                    str(prior_row.get('activityName', '') or '').strip(),
                    str(prior_row.get('geography',    '') or '').strip(),
                    str(prior_row.get('name',         '') or '').strip(),
                )] = value
    if recovered_intensities:
        print(f"   Recovered {len(recovered_intensities)} per-hectare figure(s) from "
              f"diesel rows written by an earlier run.")

    # Filter to FromTechnosphere exchanges (field operation inputs live here).
    # Additional guard: only keep unit='ha' rows — all FO inputs in crop datasets
    # are expressed in ha of FO per kg of reference product.  This excludes
    # transport, fertiliser, pesticide, etc. inputs and prevents false matches
    # with non-FO activities in the diesel reference database.
    if 'group' in df_exchanges.columns:
        df_ft = df_exchanges[
            (df_exchanges['group'] == 'FromTechnosphere') &
            (df_exchanges.get('unitName', pd.Series('', index=df_exchanges.index))
             .astype(str).str.strip().str.lower() == 'ha')
        ].copy()
    else:
        df_ft = df_exchanges[
            df_exchanges.get('unitName', pd.Series('', index=df_exchanges.index))
            .astype(str).str.strip().str.lower() == 'ha'
        ].copy()

    # Prefer activityLink_activityName (linked dataset name) over exchange name;
    # fall back to `name` per-row when the link column is absent or NaN.
    has_link_col = 'activityLink_activityName' in df_ft.columns

    if 'comment' not in df_exchanges.columns:
        df_exchanges['comment'] = ''

    # ── 3a. The three cases (config_new_datasets.py, "Diesel model") ─────────
    # Case 1 first: the FOs whose own diesel the provider gives -- these never
    # share a total.  Then case 2: totals split over the FOs they cover.
    recovered_case2: dict = {}
    if 'name' in df_exchanges.columns:
        prior = df_exchanges[
            df_exchanges['name'].astype(str).str.lower().str.startswith('diesel used in')]
        for _, prior_row in prior.iterrows():
            text = str(prior_row.get('comment', '') or '')
            if _CASE2_RE.search(text):
                amount = pd.to_numeric(prior_row.get('amount'), errors='coerce')
                if not pd.isna(amount):
                    recovered_case2[(
                        str(prior_row.get('activityName', '') or '').strip(),
                        str(prior_row.get('geography',    '') or '').strip(),
                        str(prior_row.get('name',         '') or '').strip())] = (float(amount), text)
    own_diesel: set = set()
    for _, row in df_ft.iterrows():
        fo = _fo_name_of(row, has_link_col)
        crop_key = (str(row.get('activityName', '') or '').strip(),
                    str(row.get('geography', '') or '').strip())
        fo_text = str(row.get('comment', '') or '')
        if fo and (extract_fo_diesel_intensity_from_comment(fo_text) is not None
                   or extract_fo_fuel_mj_per_kg(fo_text) is not None
                   or recovered_intensities.get(crop_key + (f'diesel used in {fo}',))
                   is not None):
            own_diesel.add(crop_key + (fo.lower(),))
    case2_plan, case2_remove, case2_flags = plan_total_diesel(
        df_exchanges, df_ft, has_link_col, own_diesel, intensity_map, geo_map, recovered_case2)
    case2_used: set = set()
    case_counts = {1: 0, 2: 0, 3: 0}
    implausible_stated: list = []   # (activity, FO, kg/ha per pass implied, table kg/ha)

    for idx, row in df_ft.iterrows():
        crop_name = str(row.get('activityName', '') or '').strip()
        crop_geo  = str(row.get('geography',    '') or '').strip()

        # Resolve FO name: prefer linked activity name, fall back to exchange name
        fo_name = ''
        if has_link_col:
            fo_name = str(row.get('activityLink_activityName', ''))
        if not fo_name or fo_name.lower() in ('nan', ''):
            fo_name = str(row.get('name', ''))

        if not fo_name or fo_name.lower() in ('nan', ''):
            continue

        # Skip rows that are already "diesel used in ..." (prevent double-processing
        # when the calculator is run more than once on the same output file)
        if fo_name.lower().startswith('diesel used in'):
            continue

        comment = str(row.get('comment', '') or '')
        diesel_row_name = f"diesel used in {fo_name}"

        # The pass count is rounded (to 0.5) for EVERY field operation,
        # whether or not a diesel figure is known for it.  Above MAX_PASSES it
        # is flagged and the amount left as it is.
        raw_amount = pd.to_numeric(row.get('amount', np.nan), errors='coerce')
        if pd.isna(raw_amount) or raw_amount == 0:
            continue

        effective_amount = raw_amount
        applied_passes   = None   # pass count the written amount represents
        crop_yield = yield_map.get((crop_name, crop_geo))
        if crop_yield and crop_yield > 0:
            comment_passes = extract_fo_passes_from_comment(comment)
            if comment_passes is not None and comment_passes > MAX_PASSES + _PASS_TOL:
                passes_flagged.append((crop_name, fo_name, comment_passes, 'stated in comment'))
            if comment_passes is not None:
                effective_amount = comment_passes / crop_yield
                applied_passes   = comment_passes
                df_exchanges.loc[idx, 'amount'] = effective_amount
                if not np.isclose(effective_amount, raw_amount):
                    pass_rounding_applied += 1
                pass_count_from_comment += 1
            else:
                rounded_passes = map_passes(raw_amount * crop_yield)
                if rounded_passes is None and raw_amount * crop_yield > MAX_PASSES + _PASS_TOL:
                    passes_flagged.append((crop_name, fo_name, raw_amount * crop_yield,
                                           'amount x yield'))
                if rounded_passes is not None:
                    effective_amount = rounded_passes / crop_yield
                    applied_passes   = rounded_passes
                    df_exchanges.loc[idx, 'amount'] = effective_amount
                    if not np.isclose(effective_amount, raw_amount):
                        pass_rounding_applied += 1
                else:
                    pass_rounding_skipped += 1
        else:
            pass_rounding_skipped += 1

        # A formula on the row would recompute the amount in the editor and
        # silently undo the rounding.  No yield parameter is known to build a
        # replacement from, so it is reported rather than rewritten.
        _formula = str(row.get('mathematicalRelation', '') or '').strip()
        if (applied_passes is not None and _formula.lower() != 'nan' and _formula
                and not np.isclose(effective_amount, raw_amount)):
            print(f"   WARNING: '{fo_name}' was rounded to {applied_passes:g} pass(es) "
                  f"but carries the formula {_formula!r}, which will override it -- "
                  f"update the formula to {applied_passes:g}/yield.")

        case2_share = (case2_plan.get((crop_name, crop_geo)) or {}).get(fo_name)
        case2_comment = None
        stated_kg = None          # case 1, second form: diesel per kg of product
        intensity = extract_fo_diesel_intensity_from_comment(comment)
        fuel_mj = extract_fo_fuel_mj_per_kg(comment) if intensity is None else None
        if fuel_mj is not None:
            stated_kg = fuel_mj / DIESEL_LHV_MJ_PER_KG
            fo_db_geo = 'comment'
            matched_from_comment += 1
            case2_comment = (
                f"{DIESEL_DQS['comment']} The data provider states the fuel use of this "
                f"field operation as {_format_intensity(fuel_mj)} MJ per kg of product, "
                f"converted at {DIESEL_LHV_MJ_PER_KG:g} MJ/kg.")
            # Plausibility only -- the provider's figure is used regardless.
            table_x, _g = _lookup_diesel_intensity(fo_name, crop_geo, intensity_map, geo_map)
            if table_x and applied_passes and crop_yield:
                per_pass = stated_kg * crop_yield / applied_passes
                if not (table_x / 5 <= per_pass <= table_x * 5):
                    implausible_stated.append((crop_name, fo_name, per_pass, table_x))
        elif intensity is not None:
            # Case 1.  Snap to the precision the diesel comment will publish,
            # so the stated figure is exactly the one the amount is computed
            # from and a re-run (which reads that figure back) reproduces this
            # result.  Every observed value round-trips to within 2e-16.
            intensity = float(_format_intensity(intensity))
            fo_db_geo = 'comment'
            matched_from_comment += 1
        else:
            # Re-run over our own output: the figure lives on the diesel row now
            intensity = recovered_intensities.get((crop_name, crop_geo, diesel_row_name))
            if intensity is not None:
                fo_db_geo = 'comment'
                recovered_from_diesel_row += 1
            elif case2_share is not None:
                # Case 2: this FO's share of the provider's total.
                fo_db_geo = 'total'
                case2_comment = case2_share[1]
                case2_used.add((crop_name, crop_geo, fo_name))
            else:
                # Case 3: kg/ha for the geography x passes / yield.
                intensity, fo_db_geo = _lookup_diesel_intensity(
                    fo_name, crop_geo, intensity_map, geo_map)
                if intensity is not None:
                    matched_from_db += 1

        if stated_kg is not None:
            amount_per_kg = stated_kg
            case_counts[1] += 1
        elif case2_comment is not None:
            amount_per_kg = case2_share[0]
            case_counts[2] += 1
        else:
            if intensity is None:
                skipped_no_intensity += 1
                continue
            # Cases 1 and 3: kg/ha x the FO amount, which is passes / yield.
            amount_per_kg = effective_amount * intensity
            case_counts[1 if fo_db_geo == 'comment' else 3] += 1
        if amount_per_kg <= 0:
            continue

        matched_fos += 1
        if fo_db_geo not in ('comment', 'total'):
            geo_selections.append((fo_name, crop_geo, fo_db_geo))

        new_row: dict = {
            'activityName': crop_name,
            'geography':    crop_geo,
            'group':        'FromTechnosphere',
            'name':         diesel_row_name,
            'unitName':     'kg',
            'amount':       amount_per_kg,
        }
        master_data = _diesel_master_data(diesel_row_name)
        if master_data:
            master_data_matched += 1
        else:
            master_data_missing_names.add(diesel_row_name)
        new_row.update(master_data)
        new_row.update(DIESEL_DQS)

        # An intensity read off the FO comment moves to the diesel row: record
        # it here, and strip the now-duplicated statement from the FO comment.
        # The real comment is built after aggregation, once every contributing
        # row for this (crop, geography, diesel flow) is known.
        new_row['_comment_intensity'] = intensity if fo_db_geo == 'comment' else None
        new_row['_comment_text'] = case2_comment

        fo_comment = comment
        if fo_db_geo == 'comment':
            fo_comment = strip_diesel_from_fo_comment(fo_comment)
            if fo_comment != comment:
                fo_comments_stripped += 1
        # State the pass count the amount stands for -- the original wording
        # usually lived inside the diesel clause just removed.
        if applied_passes is not None:
            fo_comment = set_fo_applications(fo_comment, applied_passes)
        if fo_comment != comment:
            df_exchanges.loc[idx, 'comment'] = fo_comment
            fo_applications_noted += applied_passes is not None

        diesel_new_rows.append(new_row)

    # Case 2, for a FO the total's comment names but the dataset does not
    # contain: its share still needs a diesel row of its own.
    for (crop_name, crop_geo), shares in case2_plan.items():
        for fo_name, (amount, text) in shares.items():
            if (crop_name, crop_geo, fo_name) in case2_used or amount <= 0:
                continue
            diesel_row_name = f"diesel used in {fo_name}"
            new_row = {'activityName': crop_name, 'geography': crop_geo,
                       'group': 'FromTechnosphere', 'name': diesel_row_name,
                       'unitName': 'kg', 'amount': amount}
            master_data = _diesel_master_data(diesel_row_name)
            if master_data:
                master_data_matched += 1
            else:
                master_data_missing_names.add(diesel_row_name)
            new_row.update(master_data)
            new_row.update(DIESEL_DQS)
            new_row['_comment_intensity'] = None
            new_row['_comment_text'] = text
            diesel_new_rows.append(new_row)
            case_counts[2] += 1

    print(f"   Diesel per case              : 1 (provider, per FO) {case_counts[1]}  |  "
          f"2 (provider total, split) {case_counts[2]}  |  3 (kg/ha x passes / yield) {case_counts[3]}")
    if case2_remove:
        print(f"   Total diesel exchange(s) split and replaced : {len(case2_remove)}")
    for crop, message in case2_flags:
        print(f"   WARNING (diesel total): {message}  [{crop}]")
    for crop, fo, per_pass, table_x in implausible_stated:
        print(f"   NOTE (diesel stated per kg): '{fo}' implies {per_pass:.3g} kg/ha per pass, "
              f"the table has {table_x:.3g} -- used as given; check the comment  [{crop}]")
    print(f"   Matched FO inputs            : {matched_fos}")
    print(f"   Master data (intermediateExchangeId) matched : {master_data_matched}")
    if master_data_missing_names:
        print(f"   Master data MISSING for      : {sorted(master_data_missing_names)}")
    print(f"     - intensity from comments  : {matched_from_comment}")
    print(f"     - intensity recovered from diesel row : {recovered_from_diesel_row}")
    print(f"     - intensity from table     : {matched_from_db}")
    print(f"   Diesel statement moved to diesel row (FO comment cleaned) : {fo_comments_stripped}")
    print(f"   FO comments stating the application count : {fo_applications_noted}")
    print(f"   FO pass count from comments : {pass_count_from_comment}")
    print(f"   FO pass rounding applied    : {pass_rounding_applied}")
    print(f"   FO pass rounding skipped    : {pass_rounding_skipped}")
    if passes_flagged:
        print(f"\n   WARNING: {len(passes_flagged)} field operation(s) above "
              f"{MAX_PASSES} passes per year -- not rounded, amount left as it is. "
              f"Check the amount, its unit and the yield:")
        for crop, fo, n, source in passes_flagged:
            print(f"      {n:6.2f} passes ({source})  {fo}  [{crop}]")
    print(f"   Skipped (no intensity)       : {skipped_no_intensity}")

    # ── 4. Aggregate diesel rows per (crop, geo, diesel_name) ─────────────────
    # Multiple FO passes of the same type sum together (mirrors diesel_inventory_new.py)
    if diesel_new_rows:
        df_diesel = pd.DataFrame(diesel_new_rows)
        key_cols  = ['activityName', 'geography', 'name']
        helper_cols = ['_comment_intensity', '_comment_text']
        meta_cols = [c for c in df_diesel.columns
                     if c not in key_cols + ['amount'] + helper_cols]

        df_diesel_agg = (
            df_diesel.groupby(key_cols, as_index=False)['amount'].sum()
        )
        # Re-attach metadata columns (take first occurrence)
        df_diesel_meta = df_diesel.drop_duplicates(subset=key_cols)[key_cols + meta_cols]
        df_diesel_agg  = df_diesel_agg.merge(df_diesel_meta, on=key_cols, how='left')

        # Build each comment from ALL rows that were summed into it, so a flow
        # fed by several FO passes names every per-hectare figure behind it.
        # A case-2 share carries its own comment (the total and the split).
        comment_by_key = {
            key: (next(t for t in grp['_comment_text'] if isinstance(t, str))
                  if grp['_comment_text'].map(lambda t: isinstance(t, str)).any()
                  else diesel_comment(list(grp['_comment_intensity'].dropna())))
            for key, grp in df_diesel.groupby(key_cols)
        }
        df_diesel_agg['comment'] = [
            comment_by_key.get(key, DIESEL_DQS['comment'])
            for key in zip(*(df_diesel_agg[c] for c in key_cols))
        ]

        print(f"\n   -> {len(df_diesel_agg)} unique diesel exchange row(s) to add.")
        print(df_diesel_agg[['activityName', 'geography', 'name', 'amount']].head(10).to_string())
    else:
        df_diesel_agg = pd.DataFrame()
        print("\n   WARNING: No diesel rows generated.")

    # ── 5. Assemble output exchanges ──────────────────────────────────────────
    # Remove any pre-existing diesel rows (from a previous run) so they are
    # replaced by the freshly computed rows and don't appear twice.
    existing_diesel_mask = (
        df_exchanges['name'].astype(str).str.lower().str.startswith('diesel used in')
        if 'name' in df_exchanges.columns
        else pd.Series(False, index=df_exchanges.index)
    )
    # The totals split in case 2 are replaced by their shares.
    if case2_remove:
        existing_diesel_mask = existing_diesel_mask | df_exchanges.index.isin(case2_remove)
    n_removed = existing_diesel_mask.sum()
    if n_removed:
        print(f"   Removed {n_removed} pre-existing diesel row(s) from exchanges.")
        split_mask = df_exchanges.index.isin(case2_remove or [])
        record_deletions(_output_path, 'Agri Diesel', 'exchanges',
                         df_exchanges[existing_diesel_mask & ~split_mask],
                         'diesel row replaced by the Agri Diesel calculation')
        record_deletions(_output_path, 'Agri Diesel', 'exchanges',
                         df_exchanges[existing_diesel_mask & split_mask],
                         'diesel total replaced by its share per field operation')
    df_exchanges = df_exchanges[~existing_diesel_mask].copy()

    all_cols = list(df_exchanges.columns)
    for col in (df_diesel_agg.columns if not df_diesel_agg.empty else []):
        if col not in all_cols:
            all_cols.append(col)

    df_exchanges_out = pd.concat(
        [
            df_exchanges.reindex(columns=all_cols),
            df_diesel_agg.reindex(columns=all_cols) if not df_diesel_agg.empty else pd.DataFrame(),
        ],
        ignore_index=True,
    )

    # ── 4. Save ───────────────────────────────────────────────────────────────
    df_sheets['exchanges'] = df_exchanges_out
    # Every sheet with its full header row, however empty.
    complete_sheet_schema(df_sheets)
    print("\n4. Saving consolidated output...")
    with pd.ExcelWriter(_output_path, engine='openpyxl') as writer:
        for sn, sd in df_sheets.items():
            if sn == 'review' and sd.columns.empty:
                sd = pd.DataFrame(columns=REVIEW_COLS)
            sd.to_excel(writer, sheet_name=sn, index=False)
    print(f"   Saved to: {_output_path}")
    write_spold_for(_output_path)

    # ── 5. Split into per-chunk files ────────────────────────────────────────
    if not _skip_split:
        print("\n5. Splitting into per-chunk files...")
        split_crop_datasets(df_exchanges_out, df_sheets, _split_dir)

    # ── 6. Save FO geography map ────────────────────────────────────────────────────
    if geo_selections:
        save_fo_geo_map(FO_GEO_MAPPING_FILE, geo_selections)
        print("\n   FO geography selections this run:")
        seen = set()
        for fo_n, c_geo, db_geo in sorted(geo_selections):
            if (fo_n, c_geo) in seen:
                continue
            seen.add((fo_n, c_geo))
            src = "override" if (fo_n.lower(), c_geo) in geo_map else "auto"
            print(f"    {fo_n[:42]:<42}  crop={c_geo:<6}  used_db_geo={db_geo}  ({src})")

    print("\n" + "=" * 60)
    print("AGRI DIESEL (NEW DATASETS) -- COMPLETED")
    print(f"  FO intensities: {len(intensity_map)} (field operation, geography) entries")
    print(f"  Diesel rows   : {len(df_diesel_agg)} added")
    print("=" * 60)


if __name__ == "__main__":
    import argparse as _ap, json as _json, pathlib as _pl
    _parser = _ap.ArgumentParser()
    _parser.add_argument('--batch', default=None)
    _args, _ = _parser.parse_known_args()

    if _args.batch:
        _batch  = _json.loads(_args.batch)
        _BASE   = _ROOT
        _failed = []
        print(f'[Diesel BATCH] {len(_batch)} datasets -- expanding FO diesel table once...')
        _imap  = load_fo_diesel_intensities()
        _sdir_final = None
        for _project, _filename in _batch:
            _stem  = _pl.Path(_filename).stem
            _f     = os.path.join(_BASE, 'output', _project, _stem + '.xlsx')
            _sdir  = os.path.join(_BASE, 'output', _project, 'crop_datasets_new')
            _sdir_final = _sdir  # last sdir (same for all files in one project)
            print(f'\n--- {_filename} ---')
            try:
                ok = main(_input_path=_f, _output_path=_f, _split_dir=_sdir,
                          _intensity_map=_imap, _skip_split=True)
                if ok is False:
                    _failed.append(_filename)
            except Exception as _exc:
                print(f'  ERROR processing {_filename}: {_exc}')
                _failed.append(_filename)

        # ── Combine the WHOLE project and split once ─────────────────────────
        # The split files are a project-level deliverable: every dataset of
        # the project, chunked.  Building them from only the datasets in this
        # batch meant that re-running one dataset rewrote part_1 with that one
        # dataset and left the other parts behind as orphans -- 129 activities
        # became 1.  So the split is always rebuilt from everything in the
        # project's output folder, whatever this run happened to touch.
        _succeeded = [(p, fn) for p, fn in _batch if fn not in _failed]
        if _succeeded and _sdir_final and os.environ.get(DEFER_SPLIT_ENV):
            print()
            print('[Diesel BATCH] Split Excels left to the end of the run '
                  '(rebuilt from the final datasets).')
        elif _succeeded and _sdir_final:
            import glob as _glob
            _projects = sorted({p for p, _fn in _succeeded})
            _files = []
            for _project in _projects:
                _folder = os.path.join(_BASE, 'output', _project)
                # Only workbooks the pipeline BUILT belong in the deliverable.
                # A workbook is one of those when an input dataset of the same
                # stem exists.  Without this, a copy left in the output folder
                # (olive_datasets_TN_new.xlsx, say) is concatenated in and the
                # split reports activities that do not exist.
                _in_folder = os.path.join(_BASE, 'input datasets', _project)
                _built = {os.path.splitext(os.path.basename(_i))[0].lower()
                          for _i in _glob.glob(os.path.join(_in_folder, '*'))
                          if os.path.isfile(_i)}
                for _f in sorted(_glob.glob(os.path.join(_folder, '*.xlsx'))):
                    _name = os.path.basename(_f)
                    # The calculators' own aggregates and the meta model's
                    # sheets are not datasets; concatenating them would
                    # corrupt the split.
                    if (_name.startswith('~')
                            or _name == _project + ' review.xlsx'
                            or _name.startswith('meta_model_')
                            or _name.startswith('master_data')
                            or _name.endswith('_new_output.xlsx')):
                        continue
                    _stem = os.path.splitext(_name)[0].lower()
                    if _built and _stem not in _built:
                        print(f'  Not from an input dataset, left out of the '
                              f'split: {_name}')
                        continue
                    _files.append(_f)
            print()
            print(f'[Diesel BATCH] Combining {len(_files)} dataset(s) of '
                  f'{", ".join(_projects)} for split...')
            _all_sheets: dict = {}
            for _f in _files:
                try:
                    _xl = pd.read_excel(_f, sheet_name=None)
                    if 'exchanges' not in _xl or _xl['exchanges'].empty:
                        continue          # not a dataset workbook
                    for _sn, _sd in _xl.items():
                        _all_sheets.setdefault(_sn, []).append(_sd)
                except Exception as _e:
                    # Loudly: a dataset silently dropped here goes missing
                    # from the deliverable with nothing to show why.
                    print(f'  WARNING: {os.path.basename(_f)} could not be read '
                          f'for the split ({_e}) -- it is MISSING from it.')
            _combined = {_sn: pd.concat(_dfs, ignore_index=True)
                         for _sn, _dfs in _all_sheets.items() if _dfs}
            if 'exchanges' in _combined:
                # Clear the previous parts first: a project that now needs
                # fewer chunks would otherwise keep the extra ones as orphans.
                for _old in _glob.glob(os.path.join(_sdir_final, '*.xlsx')):
                    try:
                        os.remove(_old)
                    except OSError:
                        pass
                split_crop_datasets(_combined['exchanges'], _combined, _sdir_final,
                                    _project)
                print(f'[Diesel BATCH] Split complete -> {_sdir_final}')

        if _failed:
            print(f'\n[Diesel BATCH] WARNING: {len(_failed)} file(s) skipped: {_failed}')
            print(f'[Diesel BATCH] {len(_batch) - len(_failed)}/{len(_batch)} datasets completed.')
        else:
            print(f'\n[Diesel BATCH] All {len(_batch)} datasets completed.')
    else:
        main()


