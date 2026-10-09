#!/usr/bin/env python3
"""
Comprehensive metadata + nomenclature check - runs the same set of checks
against EVERY agri dataset, uniformly, covering exactly the two categories
that have repeatedly ended up under-checked in past reviews. This module
exists so these two categories can never be silently skipped again - it is
meant to be run every time, not opportunistically.

Two categories, both always checked (a PASS finding is emitted when clean, so
the checklist row is populated either way):

1. NOMENCLATURE - ActivityName and Geography, evaluated as two SEPARATE
   sub-checks: slot presence/order in the activity name, the "at farm"/
   "at plant"/"region"/geography-in-name hard rules, and whether Geography
   Comment is populated.

2. METADATA FIELDS - generalComment, geographyComment, technologyComment,
   timePeriodComment, samplingProcedure, extrapolations, allocationComment:
   presence/emptiness, the yield line's format, and - the single
   highest-risk failure mode - CROSS-CONTAMINATION: another crop's name
   appearing in a field that should only ever mention this dataset's own
   crop, from an unedited shared template.

PORTED from the .spold review skill's check_metadata_nomenclature.py. That
version reads parse_spold.py's JSON; this pipeline works on the DataX `meta`
sheet, so parsed_from_meta_row() below rebuilds the same nested shape and the
check functions are kept as close to the original as possible.

Two deliberate divergences from the original, both forced by real data here:

  * the yield check delegates to meta_spec, whose format_yield() also copes
    with scientific notation ("2.27E+03"), European decimals and the long
    "kg per hectare" unit - 26 of 129 datasets here write the yield in
    scientific notation and the original regex reads them as missing.
  * cross-contamination skips a crop name that contains, or is contained in,
    this dataset's own crop as a whole word, so a wheat dataset is not
    reported for saying "durum wheat".

Usage as a library:
    from check_metadata_nomenclature import check_all
    findings = check_all(parsed_from_meta_row(meta_row))

Usage from the command line:
    python check_metadata_nomenclature.py --project "ILCIDAF project full"
"""
import os
import re
import sys
import glob
import argparse
import collections

# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import meta_spec as spec

# Extend this list as new crop families get checked - the point isn't
# exhaustive coverage of every crop on earth, it's catching the exact
# failure mode that has already bitten this work repeatedly: a shared
# template's crop name leaking into a dataset about a DIFFERENT crop in
# the same family. Grouped by family since a lemon dataset mentioning
# "orange" is the same bug class as it mentioning "clementine".
KNOWN_CROPS = {
    "citrus": ["orange", "lemon", "clementine", "mandarin", "grapefruit", "lime", "citron", "tangerine"],
    "pome/stone": ["apple", "pear", "peach", "apricot", "plum", "cherry", "nectarine"],
    "grain": ["wheat", "durum wheat", "barley", "maize", "corn", "rice", "oat", "rye"],
    "oil/nut": ["olive", "almond", "walnut", "hazelnut", "pistachio"],
    "vine/berry": ["grape", "strawberry", "raspberry", "blueberry"],
    "legume": ["chickpea", "lentil", "bean", "pea", "soybean"],
    "root/tuber": ["potato", "carrot", "onion"],
}
ALL_CROP_NAMES = [c for group in KNOWN_CROPS.values() for c in group]

# The hardcoded PLANT crops only, snapshotted before the profile-derived
# families below are folded in. The mandatory production-system/water-regime
# axes further down are a PLANT CROP convention (conventional/organic,
# irrigated/rainfed) that a livestock or other non-crop family cannot satisfy
# by its nature -- so whether those axes are required is decided against
# this narrower set, never against the broader lexicon used for
# cross-contamination, which rightly covers every profiled family.
_HARDCODED_CROP_NAMES = frozenset(ALL_CROP_NAMES)


def _crops_from_profile():
    """
    Add every profiled project's own families to the contamination lexicon.

    The hardcoded table above only covers crops someone thought to list. A
    project's own families are known exactly -- "tomato production" gives
    "tomato" -- so contamination checking works on a new project without
    anyone editing this file. Covers every family of every profiled project,
    crop or not: a cashmere dataset naming another project's family by
    mistake is the same bug class as a lemon dataset saying "orange".
    """
    try:
        import meta_profile
    except ImportError:
        return []
    extra = []
    for profile in getattr(meta_profile, 'PROFILES', {}).values():
        for family in (profile.get('families') or {}):
            head = re.sub(r'\s+production$', '', str(family).strip().lower())
            if head and head not in ALL_CROP_NAMES and head not in extra:
                extra.append(head)
    return extra


ALL_CROP_NAMES = ALL_CROP_NAMES + _crops_from_profile()

# Crops needing a training-system axis (for woody perennials - fruit trees,
# citrus, olive, nuts, vines/trellised)
WOODY_PERENNIAL_CROPS = (
    KNOWN_CROPS["citrus"] + KNOWN_CROPS["pome/stone"] + KNOWN_CROPS["oil/nut"] + KNOWN_CROPS["vine/berry"]
)

PRODUCTION_SYSTEM_TERMS = ["conventional", "organic", "integrated"]
WATER_REGIME_TERMS = ["irrigated", "non-irrigated", "non irrigated", "rainfed"]
TRAINING_SYSTEM_TERMS = ["single-cone", "poli-cone", "single cone", "poli cone", "full wind"]

# Which meta columns feed which field of the parsed shape.
META_FIELD_MAP = {
    'generalComment':    'generalComment',
    'allocationComment': 'allocationComment',
    'geographyComment':  'geographyComment',
    'technologyComment': 'technologyComment',
    'timePeriodComment': 'timePeriodComment',
    'samplingProcedure': 'modellingAndValidation_samplingProcedure',
    'extrapolations':    'modellingAndValidation_extrapolations',
}


def _cell(row, column):
    """One meta cell as a string, with pandas' NaN flattened to ''."""
    value = row.get(column)
    if value is None:
        return ''
    text = str(value)
    return '' if text.strip().lower() in ('nan', 'none') else text


def parsed_from_meta_row(row) -> dict:
    """
    Rebuild the nested shape the original .spold checks expect from one row
    of the DataX `meta` sheet, so those checks can be kept verbatim.
    """
    return {
        'activity': {
            'activityName':      _cell(row, 'activityName'),
            'generalComment':    _cell(row, 'generalComment'),
            'allocationComment': _cell(row, 'allocationComment'),
        },
        'geography': {
            'shortname': _cell(row, 'geography'),
            'comment':   _cell(row, 'geographyComment'),
        },
        'technology': {'comment': _cell(row, 'technologyComment')},
        'timePeriod': {'comment': _cell(row, 'timePeriodComment')},
        'representativeness': {
            'samplingProcedure': _cell(row, 'modellingAndValidation_samplingProcedure'),
            'extrapolations':    _cell(row, 'modellingAndValidation_extrapolations'),
        },
    }


def _training_form_in_text(parsed) -> str:
    """
    The training form the dataset's own prose names, or ''.

    Longest term first, so "poli-cone" is not found as "cone".  This is the
    only evidence that settles the axis: nothing else in a dataset implies
    how its orchard is trained.
    """
    body = ' '.join((
        parsed.get('activity', {}).get('generalComment') or '',
        _flat_text(parsed.get('technology', {}).get('comment')),
        _flat_text(parsed.get('geography', {}).get('comment')),
        parsed.get('representativeness', {}).get('samplingProcedure') or '',
    )).lower()
    for term in sorted(TRAINING_SYSTEM_TERMS, key=len, reverse=True):
        if term in body:
            return term
    return ''


def _flat_text(value):
    """generalComment-style fields come back as a dict of indexed texts from
    parse_spold.py; other comment fields as a plain string or None. Flatten
    either shape into one string for scanning."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(v for v in value.values() if v)
    return str(value)


def _crop_word_in(crop: str, text: str) -> bool:
    """
    Whether `crop` appears in `text` as a whole word, not merely a substring.

    A plain `in` test matches "oat" inside "goat" and "grape" inside
    "grapefruit" -- both real crop names, so the false hit is silent. Every
    place that asks "is this crop named here" needs this, not just the
    geography-shortname check that already used it.
    """
    return re.search(r'\b' + re.escape(crop) + r'\b', text) is not None


def check_nomenclature(parsed):
    """
    ActivityName and Geography, as two SEPARATE findings (never merged).
    Returns a list of hotspot-shaped dicts.
    """
    findings = []
    activity_name = (parsed.get("activity", {}).get("activityName") or "")
    geography = parsed.get("geography", {})
    geo_shortname = geography.get("shortname") or ""
    geo_comment = _flat_text(geography.get("comment"))

    # --- ActivityName checks ---
    name_issues = []

    if re.search(r'\bat farm\b|\bat plant\b', activity_name, re.I):
        name_issues.append("contains a leftover 'at farm'/'at plant' phrase "
                           "(v2-style; the gerund 'production' already implies the boundary)")

    segments = [s.strip() for s in activity_name.split(",")]
    base = segments[0] if segments else activity_name
    axis_text = ", ".join(segments[1:]).lower()
    base_lower = base.lower()

    if re.search(r'\bregion\b', axis_text):
        name_issues.append("uses the word 'region' near an altitude/agro-zone term "
                           "(should be dropped, e.g. 'hill' not 'hill region')")

    # Mandatory production-system/water-regime axes are a PLANT CROP
    # convention (conventional/organic, irrigated/rainfed) and are only
    # required when the name's head is a recognised plant crop -- a
    # livestock or other non-crop family has no water regime to state and
    # is not missing anything by leaving it out.
    if any(_crop_word_in(c, base_lower) for c in _HARDCODED_CROP_NAMES):
        if not any(term in axis_text for term in PRODUCTION_SYSTEM_TERMS):
            name_issues.append(f"missing mandatory production system axis "
                               f"({'/'.join(PRODUCTION_SYSTEM_TERMS)})")

        if not any(term in axis_text for term in WATER_REGIME_TERMS):
            name_issues.append(f"missing mandatory water regime axis (irrigated/non-irrigated/rainfed)")

    # The training system is NOT mandatory.  Whether an orchard is single-cone
    # or poli-cone cannot be inferred from the crop, and a name is not wrong
    # for leaving it out -- so its absence is only raised where the dataset
    # itself names a training form, which is the one case where the right
    # value is certain.  Where nothing says it, the axis is left alone.
    matched_crop = next((c for c in ALL_CROP_NAMES if _crop_word_in(c, base_lower)), None)
    if matched_crop and matched_crop in WOODY_PERENNIAL_CROPS:
        stated = _training_form_in_text(parsed)
        if stated and not any(term in axis_text for term in TRAINING_SYSTEM_TERMS):
            name_issues.append(f"the dataset describes a '{stated}' training system, "
                               f"which the name does not carry")

    # Matched on a word boundary, not as a substring: the original's plain
    # `in` test reports every "minor citrus fruit production" dataset because
    # the shortname "IT" sits inside "citrus".
    if (geo_shortname and len(geo_shortname) > 1
            and re.search(r'\b' + re.escape(geo_shortname) + r'\b', activity_name, re.I)):
        name_issues.append(f"geography shortname/name '{geo_shortname}' appears inside the "
                           f"activityName itself (should only be in the Geography field)")

    if name_issues:
        findings.append({
            "exchange_name": "ActivityName",
            "checklist_row": "2 (agri) / 25-27 (Checks)",
            "issue": "Nomenclature format/axis issues",
            "expected": "Comma-separated axes per the nomenclature rules",
            "actual": f"'{activity_name}' - " + "; ".join(name_issues),
            "severity": "BLOCKING",
            "note": "Naming call for human review; not auto-renamed (renaming has broader "
                    "linking implications). Only case/spacing is normalised automatically.",
        })
    else:
        findings.append({
            "exchange_name": "ActivityName",
            "checklist_row": "2 (agri) / 25-27 (Checks)",
            "issue": "Nomenclature format/axis check",
            "expected": "-", "actual": "No format/axis issues found.",
            "severity": "PASS",
        })

    # --- Geography checks (always separate from ActivityName) ---
    geo_issues = []
    if not geo_comment.strip():
        geo_issues.append("Geography Comment is empty - no explanation of geographic "
                          "representativeness/scope")

    if geo_issues:
        findings.append({
            "exchange_name": "Geography",
            "checklist_row": "47-48 (Checks) / 2 (agri)",
            "issue": "Geography metadata issues",
            "expected": "Populated Geography Comment explaining representativeness",
            "actual": "; ".join(geo_issues),
            "severity": "ACCEPTABLE_FOR_NOW",
            "note": "Shortname itself is not flagged as leaking into the name "
                    "(see ActivityName finding above if it does).",
        })
    else:
        findings.append({
            "exchange_name": "Geography",
            "checklist_row": "47-48 (Checks) / 2 (agri)",
            "issue": "Geography metadata check",
            "expected": "-", "actual": "Geography Comment populated; no issues found.",
            "severity": "PASS",
        })

    return findings


def _same_crop_word(a: str, b: str) -> bool:
    """
    True when two crop names denote the same crop for contamination purposes:
    one contains the other as a whole word ("wheat" / "durum wheat").

    Not in the original; without it every wheat dataset is reported for
    mentioning "durum wheat".
    """
    if a == b:
        return True
    return (re.search(r'\b' + re.escape(a) + r'\b', b) is not None or
            re.search(r'\b' + re.escape(b) + r'\b', a) is not None)


def check_cross_contamination(parsed):
    """
    Scan every metadata text field for a DIFFERENT crop's name than the one
    this dataset is actually about - the single highest-risk, repeatedly
    confirmed failure mode. Returns one finding per field/foreign-crop pair.
    """
    findings = []
    activity_name = (parsed.get("activity", {}).get("activityName") or "").lower()
    own_crop = next((c for c in ALL_CROP_NAMES if _crop_word_in(c, activity_name)), None)
    if not own_crop:
        return findings  # can't determine the dataset's own crop - skip rather than guess

    fields_to_scan = {
        "generalComment":    _flat_text(parsed.get("activity", {}).get("generalComment")),
        "allocationComment": _flat_text(parsed.get("activity", {}).get("allocationComment")),
        "geographyComment":  _flat_text(parsed.get("geography", {}).get("comment")),
        "technologyComment": _flat_text(parsed.get("technology", {}).get("comment")),
        "timePeriodComment": _flat_text(parsed.get("timePeriod", {}).get("comment")),
        "samplingProcedure": _flat_text((parsed.get("representativeness") or {}).get("samplingProcedure")),
        "extrapolations":    _flat_text((parsed.get("representativeness") or {}).get("extrapolations")),
    }

    for field_name, text in fields_to_scan.items():
        if not text:
            continue
        text_lower = text.lower()
        for other_crop in ALL_CROP_NAMES:
            if _same_crop_word(other_crop, own_crop):
                continue
            if re.search(r'\b' + re.escape(other_crop) + r's?\b', text_lower):
                findings.append({
                    "exchange_name": field_name,
                    "checklist_row": "28-54 (Checks)",
                    "issue": "Cross-contamination: wrong crop name mentioned",
                    "expected": f"Only '{own_crop}' should be mentioned",
                    "actual": f"'{other_crop}' found in {field_name}",
                    "severity": "BLOCKING",
                    "note": "Same bug class as a real, confirmed issue found 3x in one citrus "
                            "dataset family - likely a copy-pasted template not fully adapted.",
                })

    return findings


def contaminating_crop(hotspot) -> str:
    """
    The foreign crop name a contamination finding is about.

    Read back out of the finding's own text ("'clementine' found in
    technologyComment"), so the proposed correction and the reported issue can
    never name different crops.
    """
    match = re.search(r"'([^']+)' found in", str(hotspot.get('actual', '')))
    return match.group(1) if match else ''


def check_metadata_completeness(parsed):
    """
    Presence/emptiness across the metadata fields, plus the yield line's
    format in generalComment. Does NOT judge content quality - just whether
    each field has something in it, and whether the yield line is locked.
    """
    findings = []
    activity = parsed.get("activity", {})
    fields = {
        "generalComment":    _flat_text(activity.get("generalComment")),
        "geographyComment":  _flat_text(parsed.get("geography", {}).get("comment")),
        "technologyComment": _flat_text(parsed.get("technology", {}).get("comment")),
        "timePeriodComment": _flat_text(parsed.get("timePeriod", {}).get("comment")),
        "samplingProcedure": _flat_text((parsed.get("representativeness") or {}).get("samplingProcedure")),
        "extrapolations":    _flat_text((parsed.get("representativeness") or {}).get("extrapolations")),
    }

    for field_name, text in fields.items():
        if not text.strip():
            findings.append({
                "exchange_name": field_name,
                "checklist_row": "28-59 (Checks)",
                "issue": "Metadata field empty",
                "expected": "Populated per the metadata template",
                "actual": "(empty)",
                "severity": "BLOCKING" if field_name == "generalComment" else "ACCEPTABLE_FOR_NOW",
                "note": "Generate from the standardized template; ask the data provider only "
                        "for what's not derivable from the dataset.",
            })

    general_comment_text = fields["generalComment"]
    if general_comment_text:
        # Delegated to meta_spec so scientific notation, European decimals and
        # the long "kg per hectare" unit are all understood; the original's
        # bare regex reads those as missing.
        status, canonical, detail = spec.yield_line_status(general_comment_text)
        if status == 'missing':
            findings.append({
                "exchange_name": "generalComment",
                "checklist_row": "28-34 (Checks) / 3 (agri)",
                "issue": "Yield line missing or wrong format",
                "expected": "Yield: [value] kg/ha. (period separator, no thousands separator, "
                            "always 2 decimals (9000.00))",
                "actual": detail or "No matching 'Yield: [...] kg/ha' pattern found",
                "severity": "BLOCKING",
                "note": "See meta_spec.format_yield() for the locked format.",
            })
        elif status == 'unit':
            findings.append({
                "exchange_name": "generalComment",
                "checklist_row": "28-34 (Checks) / 3 (agri)",
                "issue": "Yield stated in a unit other than kg/ha",
                "expected": "Yield: [value] kg/ha.",
                "actual": detail or "",
                "severity": "BLOCKING",
                "note": "Converting the unit changes the figure, so it is left to a human.",
            })
        elif status == 'reformat':
            findings.append({
                "exchange_name": "generalComment",
                "checklist_row": "28-34 (Checks) / 3 (agri)",
                "issue": "Yield number format doesn't match the locked standard",
                "expected": canonical,
                "actual": detail or "",
                "severity": "BLOCKING",
                "note": "Reformatted automatically by meta_spec.format_yield().",
            })

    return findings


# The real one, shared with the review skill.
from generate_metadata import find_short_form_citations   # noqa: E402


def check_citations(parsed):
    """
    Flags free-text 'Author, Year' short-form citations found in metadata
    fields, for a human to confirm a full reference is documented somewhere.
    """
    findings = []
    activity = parsed.get("activity", {})
    fields = {
        "generalComment":    _flat_text(activity.get("generalComment")),
        "technologyComment": _flat_text(parsed.get("technology", {}).get("comment")),
        "samplingProcedure": _flat_text((parsed.get("representativeness") or {}).get("samplingProcedure")),
        "extrapolations":    _flat_text((parsed.get("representativeness") or {}).get("extrapolations")),
        "allocationComment": _flat_text(activity.get("allocationComment")),
    }
    all_citations = set()
    for field_name, text in fields.items():
        if text:
            all_citations.update(find_short_form_citations(text))

    if all_citations:
        findings.append({
            "exchange_name": "(metadata, various fields)",
            "checklist_row": "56-60 (Checks)",
            "issue": "Short-form citations found - verify full references are documented",
            "expected": "A traceable full reference for each short-form citation",
            "actual": f"{len(all_citations)} short-form citation(s): {sorted(all_citations)}",
            "severity": "INFO",
            "note": "Not verified against a sources registry in this pass - confirm full "
                    "references exist (title/publisher/URL/DOI) for each.",
        })

    return findings


def check_all(parsed):
    """Runs every check in this module and returns one combined list."""
    findings = []
    findings.extend(check_nomenclature(parsed))
    findings.extend(check_cross_contamination(parsed))
    findings.extend(check_metadata_completeness(parsed))
    findings.extend(check_citations(parsed))
    return findings


def check_meta_row(row) -> list:
    """Convenience wrapper: run every check against one DataX meta row."""
    return check_all(parsed_from_meta_row(row))


if __name__ == "__main__":
    import pandas as pd
    from config_new_datasets import INPUT_PROJECT_FOLDER, BASE_DIR

    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=INPUT_PROJECT_FOLDER)
    ap.add_argument("--severity", default=None,
                    help="only print findings of this severity")
    args = ap.parse_args()

    project_dir = os.path.join(BASE_DIR, 'output', args.project)
    from config_new_datasets import is_review_workbook
    files = [p for p in sorted(glob.glob(os.path.join(project_dir, '*.xlsx')))
             if not os.path.basename(p).startswith('~')
             and 'master_data' not in os.path.basename(p)
             and 'meta_model_report' not in os.path.basename(p)
             and not is_review_workbook(p)]

    all_findings = []
    for path in files:
        try:
            meta = pd.read_excel(path, sheet_name='meta')
        except Exception:
            continue
        for _, row in meta.iterrows():
            for finding in check_meta_row(row):
                finding['dataset'] = os.path.basename(path)
                all_findings.append(finding)

    counts = collections.Counter(f['severity'] for f in all_findings)
    print(f"{len(all_findings)} findings across {len(files)} dataset(s): "
          + ', '.join(f'{n} {s}' for s, n in counts.most_common()) + '\n')
    for finding in all_findings:
        if args.severity and finding['severity'] != args.severity:
            continue
        if finding['severity'] == 'PASS':
            continue
        print(f"[{finding['severity']}] {finding.get('dataset', '')[:40]} "
              f"{finding['exchange_name']}: {finding['issue']}")
        print(f"    {finding['actual'][:200]}")
