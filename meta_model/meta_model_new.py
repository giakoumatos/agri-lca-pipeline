"""
Meta Model for New Datasets

Keeps the `meta` sheet and the activityName nomenclature consistent across every
dataset in a project.

    python meta_model/meta_model_new.py                    # normalise
    python meta_model/meta_model_new.py --report-only      # just look
    python meta_model/meta_model_new.py --project "X"      # another project

Fixes are written by default, because this runs as a pipeline step and the
steps after it -- the .spold output above all -- must see canonical names.
A findings report is always written, whether or not anything was changed.

What it does, per dataset:

  1. Normalises the activityName -- lowercase, ', ' separated, single spaces --
     and propagates any rename to every sheet that carries an activityName,
     since the name is the dataset's identity and must not change in one sheet
     alone.  Formatting is the ONLY thing rewritten; an axis missing or a value
     never seen in this crop family is a naming call and is reported.
  2. Checks every meta column against the project profile (meta_profile.py):
     constant columns hold the project's value, always-empty columns are blank,
     ISIC matches the crop family's, and the prose columns match one of the
     house wordings induced for that family.
  3. Fills a column that is missing or blank from the spec, where the spec
     determines it.  A column the spec cannot derive is reported, never invented.

Deterministic deviations are fixed; anything needing judgement (a changed
figure, prose that diverges beyond its slots) is reported for a human -- once.
A human answer to a recurring issue (a mis-typed flow name, an off-template
sentence) is remembered in meta_model_decisions.json and reused automatically
on every other dataset that carries the exact same issue, so a project of
hundreds of datasets needs one decision per DISTINCT issue, not one per
dataset. A missing value that genuinely differs by dataset (a yield, a blank
comment with nothing to derive it from) is never cached -- see
is_cacheable_value().

Nothing about crops, regions, ISIC codes or constant values is hardcoded. All
of it is induced per project into meta_profile.py by build_meta_spec.py, so the
model works on any project; run that builder once per project (or point it at a
reviewed reference project whose style later projects should follow). Without a
profile only the universal rules apply, and the model says so.

PIPELINE POSITION:
    run_pipeline.py runs this model in batch twice: first with --fresh,
    which starts each working copy in output/ afresh from input datasets/
    and corrects it -- so the three calculators build on well-mapped
    exchanges -- and again after them.  Everything it decides is reproduced
    on every run (its rules, the fix-sheet answers, the remembered decisions,
    and no correction marked 'no'), so it does not matter whether this model
    or the calculators run first.  Run on its own, it works on the same
    working copy; a dataset with none yet is brought in directly
    (ingest_missing()), and then carries none of the calculators' enrichment
    (pesticide emissions, nutrients, diesel) until they run.

Author: Andreas Giakoumatos
Last Updated: 2026-09-30
"""

import os
import re
import sys
import json
import glob
import argparse
import collections
import contextlib
import io
from datetime import datetime

import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')

# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_new_datasets import (
    BASE_DIR, META_OUTPUT_DIR, META_OUTPUT_DIRNAME,
    META_APPLY_IN_PIPELINE, describe_row, describe_cell, deleted_note, deleted_items,
    record_deletions, PIPELINE_DIR)
# The runner's parts the meta model uses: the chooser, verify's pesticide list.
sys.path.insert(0, PIPELINE_DIR)
import meta_spec as spec

try:
    import meta_profile
except ImportError:                      # not induced yet
    meta_profile = None

import check_metadata_nomenclature as checks
import generate_metadata as gen
import meta_facts


PROFILES = dict(getattr(meta_profile, 'PROFILES', {}) or {}) if meta_profile else {}


def profile_for(project: str) -> dict:
    """
    The recorded profile of one project, or {} when it has never been
    profiled.  Keyed by project so a single run can judge them all: nothing
    here is tied to one project, and an unprofiled project still gets every
    universal check.
    """
    return PROFILES.get(project) or {}


def families_of(profile: dict) -> dict:
    return profile.get('families') or {}


# ── Findings ──────────────────────────────────────────────────────────────────

class Finding:
    """One deviation from the spec.  `fixed` says whether it was corrected."""

    __slots__ = ('project', 'dataset', 'activity', 'column', 'found', 'expected',
                 'kind', 'fixed', 'severity', 'note', 'checklist_row', 'proposal',
                 'options')

    def __init__(self, dataset, activity, column, found, expected, kind, fixed=False,
                 severity='WARNING', note='', checklist_row='', project='',
                 proposal='', options=()):
        self.project       = project
        self.dataset       = dataset
        self.activity      = activity
        self.column        = column
        self.found         = found
        self.expected      = expected
        self.kind          = kind
        self.fixed         = fixed
        # BLOCKING: the dataset cannot ship until a human supplies the value.
        # WARNING : a deviation the model fixed, or one needing review.
        self.severity      = severity
        self.note          = note
        self.checklist_row = checklist_row
        # The correction the model would suggest for a finding it will not
        # decide.  Shown beside the finding in the fix sheet so the choice is
        # yours: accept it, or type something else.
        # `options` carries the alternatives as [(label, value)] when there is
        # more than one defensible answer -- a missing name axis can be filled
        # several ways, and the pick is yours.  The first option is the one
        # offered as `proposal`, so a row with options needs no other handling.
        self.options       = list(options or ())
        self.proposal      = proposal or (self.options[0][1] if self.options else '')

    def as_row(self) -> dict:
        def clip(v, n=300):
            s = '' if v is None else str(v)
            return s if len(s) <= n else s[:n] + ' ...'
        return {
            'project':       self.project,
            'dataset':       self.dataset,
            'activityName':  self.activity,
            'column':        self.column,
            'checklist_row': self.checklist_row,
            'issue':         self.kind,
            'severity':      self.severity,
            'action':        'fixed' if self.fixed else 'review',
            'found':         clip(self.found),
            'expected':      clip(self.expected),
            'note':          self.note,
        }


# ── activityName ──────────────────────────────────────────────────────────────

PRODUCTION_NAME_KIND = 'activity name lacks "production"'


def reference_product_of(exchanges, activity: str):
    """The reference product of one activity of the dataset, or None."""
    if exchanges is None or exchanges.empty or 'group' not in exchanges.columns:
        return None
    rows = exchanges[exchanges['group'].astype(str).str.strip() == 'ReferenceProduct']
    if 'activityName' in rows.columns and rows['activityName'].notna().any():
        mine = rows[rows['activityName'].map(lambda v: spec.same_activity_name(v, activity))]
        rows = mine if not mine.empty else rows
    names = rows['name'].dropna().astype(str) if 'name' in rows.columns else []
    return names.iloc[0] if len(names) else None


def parse_activity_name(name):
    """
    Split an activityName into (family, axes) using the project profile.

    Nothing about crops is hardcoded: the family is simply the first segment,
    whatever it is, so a project of tomatoes profiles as readily as one of
    grapes.  A family the profile has never seen still parses -- it just
    cannot be checked against a shape.
    """
    segments = spec.canonical_segments(name)
    if not segments:
        return None, []
    return segments[0], segments[1:]


def canonical_activity_name(name, families=None, project=None):
    """
    Return (canonical_name, problems).

    The canonical name is only ever a formatting normalisation -- lowercase,
    ', ' separated, single spaces.  Everything else (an axis missing, a value
    never seen in this family) is a naming call and is reported, never
    rewritten.
    """
    segments = spec.canonical_segments(name)
    if not segments:
        return None, ['activityName is empty']

    canonical = spec.join_segments(segments)
    family, axes = segments[0], segments[1:]
    info = (families or {}).get(family)

    if info is None:
        where = f' of {project!r}' if project else ''
        return canonical, [
            f'crop family {family!r} is not in the profile{where}, so its name shape '
            f'and wording cannot be checked -- run build_meta_spec.py --project '
            f'"{project or "<project>"}" --write'
        ]

    problems = []
    allowed = set(info.get('segment_counts') or ())
    if allowed and len(axes) not in allowed:
        problems.append(f'{len(axes)} axes after the crop; this family uses '
                        f'{sorted(allowed)} ({info["datasets"]} datasets)')
        return canonical, problems

    vocabulary = info.get('vocabulary') or []
    open_axes  = info.get('open_axes') or []
    fixed      = info.get('fixed_segments', len(vocabulary))

    for index, value in enumerate(axes[:fixed]):
        if index >= len(vocabulary):
            break
        if index < len(open_axes) and open_axes[index]:
            continue                      # an open identifier, e.g. the variety
        allowed_values = vocabulary[index]
        if allowed_values and value not in allowed_values:
            problems.append(f'axis {index + 1} = {value!r} is not one of '
                            f'{sorted(allowed_values)} for this family')

    if info.get('optional_last') and len(axes) > fixed:
        last  = axes[-1]
        known = info.get('optional_vocabulary') or []
        if known and last not in known:
            problems.append(f'final axis {last!r} is not one of {sorted(known)} '
                            f'for this family')

    return canonical, problems


# ── Template text ─────────────────────────────────────────────────────────────

_SLOT_RE = re.compile(r'\{(\d+)\}')


def _flatten(text) -> str:
    """Whitespace-insensitive comparison form: every run becomes one space."""
    return ' '.join(str(text).split())


def match_one_template(value: str, template: str) -> dict | None:
    """
    Match a value against one template, returning its slot values or None.

    Done by scanning rather than by regex.  A template holds many {n} slots and
    a regex of `.*?` between fixed chunks backtracks catastrophically on prose
    this long; walking the fixed chunks in order and taking the leftmost
    occurrence of each is linear and cannot blow up.  A slot may be empty,
    which is how a dataset that simply omits an optional phrase still matches.
    """
    text  = _flatten(value)
    parts = _SLOT_RE.split(template)   # fixed, slot_no, fixed, slot_no, ...

    slots: dict[str, str] = {}
    position = 0
    pending_slot: str | None = None

    for index, part in enumerate(parts):
        if index % 2:                                   # a slot number
            pending_slot = 's' + part
            continue

        chunk = _flatten(part)
        if not chunk:
            continue

        if pending_slot is None:                        # template opens with fixed text
            if not text.startswith(chunk):
                return None
            position = len(chunk)
            continue

        found = text.find(chunk, position)
        if found < 0:
            return None
        slots[pending_slot] = text[position:found].strip()
        position = found + len(chunk)
        pending_slot = None

    if pending_slot is not None:                        # template ends with a slot
        slots[pending_slot] = text[position:].strip()
    elif position != len(text):                         # trailing text not in the template
        return None

    return slots


def match_template(value: str, templates: list[str]) -> tuple[bool, dict]:
    """Try each template for the column; return (matched, slot values)."""
    for template in templates:
        slots = match_one_template(value, template)
        if slots is not None:
            return True, slots
    return False, {}


def describe_divergence(value: str, templates: list[str]) -> str:
    """
    Say where the text departs from the house wording.

    Far more use in a report than the template itself: it names the phrase the
    house style expects at the point the dataset stops following it.
    """
    text  = _flatten(value)
    best  = None
    for template in templates:
        parts    = _SLOT_RE.split(template)
        position = 0
        pending  = None
        reached  = 0
        problem  = None
        for index, part in enumerate(parts):
            if index % 2:
                pending = part
                continue
            chunk = _flatten(part)
            if not chunk:
                continue
            if pending is None:
                if not text.startswith(chunk):
                    problem = f'should open with: "{chunk[:90]}"'
                    break
                position = len(chunk)
            else:
                found = text.find(chunk, position)
                if found < 0:
                    problem = (f'after "...{text[max(0, position - 45):position][-45:]}" '
                               f'the house wording continues "{chunk[:90]}" '
                               f'but this dataset has "{text[position:position + 90]}"')
                    break
                position = found + len(chunk)
                pending  = None
            reached = position
        if problem is None and pending is None and position != len(text):
            problem = f'extra text not in the house wording: "{text[position:position + 90]}"'
        if problem is None:
            return ''                       # it actually matches
        if best is None or reached > best[0]:
            best = (reached, problem)
    return best[1] if best else 'no house wording recorded for this crop family'


# Canonical text form lives in the spec so the builder and the model agree.
tidy_text      = spec.tidy_text
ends_with_stop = spec.ends_with_stop


# ── Validity period ─────────────────────────────────────────────────────────

def review_period(row, apply_fixes: bool) -> list:
    """
    Check startDate/endDate against the years in timePeriodComment, both ways.

    Returns a list of (column, found, expected, kind, fixed, severity, note,
    proposal).  An endDate after the current year is an error rather than a
    difference of opinion, so where the prose supplies a usable year the date
    is corrected from it; a plain disagreement is reported with the prose's
    years offered, because either side could be the wrong one.
    """
    out = []
    this_year = spec.current_year()
    start, end = spec.year_of(row.get('startDate')), spec.year_of(row.get('endDate'))
    said_start, said_end = spec.period_years(row.get('timePeriodComment'))

    # 1. A period that has not happened yet.
    for column, year, said in (('startDate', start, said_start),
                               ('endDate', end, said_end)):
        if year is None or year <= this_year:
            continue
        if said and said <= this_year:
            corrected = spec.set_year(row.get(column), said)
            out.append((column, row.get(column), corrected,
                        f'{column} is later than {this_year}', apply_fixes,
                        'WARNING' if apply_fixes else 'BLOCKING',
                        f'A dataset cannot be valid to {year}. timePeriodComment '
                        f'says {said}, so the date is taken from it.', '', ()))
            if apply_fixes:
                _set_cell_value(row, column, corrected)
        else:
            out.append((column, row.get(column), f'a year no later than {this_year}',
                        f'{column} is later than {this_year}', False, 'BLOCKING',
                        'timePeriodComment gives no usable year to correct it from, '
                        'so the right period has to be supplied.', '', ()))

    # 2. start after end.
    if start and end and start > end:
        out.append(('startDate', f'{start} - {end}', 'startDate before endDate',
                    'the period runs backwards', False, 'BLOCKING',
                    'startDate is later than endDate.', '', ()))

    # 3. The dates and the prose disagree, both being plausible.
    start, end = spec.year_of(row.get('startDate')), spec.year_of(row.get('endDate'))
    if (said_start and said_end and start and end
            and (said_start, said_end) != (start, end)
            and end <= this_year and said_end <= this_year):
        # Either side could be the wrong one, so both repairs are offered:
        # the comment reworded to the dates, or the dates moved to the
        # comment's years.  Picking one settles it; the model picks neither.
        options = [
            (f'reword timePeriodComment to the dates ({start} - {end})',
             spec.set_period_years(row.get('timePeriodComment'), start, end)),
            (f'change startDate/endDate to the comment '
             f'({said_start} - {said_end})',
             f'dates: {said_start} - {said_end}'),
        ]
        out.append(('timePeriodComment', f'dates {start} - {end}',
                    f'timePeriodComment says {said_start} - {said_end}',
                    'the dates and timePeriodComment disagree', False,
                    'ACCEPTABLE_FOR_NOW',
                    'Both are possible, so neither is assumed right. Either correct '
                    'the dates, or reword the comment -- whichever is wrong.', '',
                    options))

    # 4. The period sentence the model writes, with the wrong count: counted
    # in calendar years (2021-08 to 2022-08 called "a two-year time period"),
    # or leaving out the averaging period the generalComment states.  Only
    # the model's own sentence is rewritten -- a provider's text never is.
    text = '' if _blank(row.get('timePeriodComment')) else tidy_text(row.get('timePeriodComment'))
    if text and start and end and end >= start:
        seasons = spec.agricultural_seasons(row.get('startDate'), row.get('endDate'))
        average = spec.stated_average_years(row.get('generalComment'))
        right = gen.generate_time_period_comment(start, end, n_years=seasons,
                                                 average_years=average)
        ours = {tidy_text(gen.generate_time_period_comment(start, end, n_years=k, average_years=a))
                for k in range(1, 31) for a in (None, *range(2, 31))}
        if text in ours and text != tidy_text(right):
            why = (f'the generalComment states a {average}-year average' if average
                   and average != seasons else
                   f'{row.get("startDate")} to {row.get("endDate")} is '
                   f'{"one agricultural season" if seasons == 1 else f"{seasons} agricultural seasons"}')
            out.append(('timePeriodComment', text, right,
                        'period counted in calendar years, not agricultural seasons'
                        if not (average and average != seasons) else
                        'averaging period of the generalComment missing',
                        apply_fixes, 'WARNING' if apply_fixes else 'ACCEPTABLE_FOR_NOW',
                        f'Rewritten: {why}.', right, ()))
            if apply_fixes:
                _set_cell_value(row, 'timePeriodComment', right)

    # 5. The generalComment states a production period the dates do not
    # cover (an alfalfa stand's five years, dates of one season).  The
    # comment follows the stated period; the dates are flagged, since which
    # years they should be is not in the dataset.
    if start and end and end >= start:
        seasons = spec.agricultural_seasons(row.get('startDate'), row.get('endDate'))
        average = spec.stated_average_years(row.get('generalComment'))
        if average and seasons and average != seasons:
            span = ('one agricultural season' if seasons == 1
                    else f'{seasons} agricultural seasons')
            out.append(('startDate', f'{row.get("startDate")} - {row.get("endDate")} ({span})',
                        f'a {average}-year period, as the generalComment states',
                        'the dates do not cover the generalComment period', False,
                        'ACCEPTABLE_FOR_NOW',
                        f'The dates span {span}, but the generalComment states a '
                        f'{average}-year production period, which the yield describes. '
                        f'Check the dates: they should normally cover the {average} years. '
                        f'Type the period as "dates: YYYY - YYYY" to set both.', '', ()))
    return out


def _set_cell_value(row, column, value) -> None:
    """Write into one cell of a row taken from iterrows (object dtype)."""
    row[column] = value


# ── Generating a missing column from the metadata template ───────────────────

def name_facts(row, families=None) -> dict:
    """
    What the activityName and the meta row tell us, by axis MEANING rather
    than by position -- {crop, production_system, water_regime, region, ...}.

    Position is a per-project accident; meaning is not, which is why the
    profile labels each axis by the vocabulary it holds.
    """
    family, axes = parse_activity_name(row.get('activityName'))
    facts = {'crop': None, 'family': family}
    if not family:
        return facts

    facts['crop'] = re.sub(r'\s+production$', '', family).strip() or family
    info = (families or {}).get(family)
    if not info:
        # No profile for this family yet.  An axis still says what it is by
        # what it holds -- 'conventional' is a production system on any
        # project -- so label them one by one rather than giving up.
        for value in axes:
            label = spec.label_axis([value])
            if label and label not in facts:
                facts[label] = value
        return facts

    labels = list(info.get('axis_labels') or [])
    fixed  = info.get('fixed_segments', len(labels))
    for index, value in enumerate(axes[:fixed]):
        if index < len(labels) and labels[index]:
            facts[labels[index]] = value
    if info.get('optional_last') and len(axes) > fixed and info.get('optional_label'):
        facts[info['optional_label']] = axes[-1]
    return facts


# ── Nomenclature: candidate names to choose from ────────────────────────────
# A naming call stays yours, but the model should not hand you a problem with
# no way forward.  For every axis a name is missing it offers the values that
# axis can take -- the one the dataset's own prose implies first -- so the
# decision is a pick from a list rather than free typing.

_AT_PHRASE_RE   = re.compile(r',?\s*\b(?:at farm|at plant)\b', re.I)
_REGION_WORD_RE = re.compile(r'\s*\bregion\b', re.I)

# The axes every name must carry.  The training system is NOT one of them:
# single-cone or poli-cone cannot be deduced from the crop, so it is only
# ever offered where the dataset's own prose names the form (see below).
_REQUIRED_AXES = ('production_system', 'water_regime')

# Where an axis value is looked for when the name omits it: the dataset's own
# description usually says whether it is organic, or irrigated.
_INFERENCE_COLUMNS = ('generalComment', 'technologyComment', 'geographyComment',
                      'includedActivitiesStart',
                      'modellingAndValidation_samplingProcedure')


def dataset_prose(row) -> str:
    """Everything the dataset says about itself, for inferring an axis value."""
    return ' '.join(str(row.get(column) or '') for column in _INFERENCE_COLUMNS)


# ── What the exchanges say about the dataset ────────────────────────────────
# Two facts the meta sheet cannot state on its own but the inventory can:
# whether the crop is irrigated, and what it yields.  Both are read from the
# exchanges of the SAME activity -- a workbook can hold several.

# Not 'non-irrigated': the land-use flows of a rainfed crop say that too.
_IRRIGATION_RE     = re.compile(r'(?<!non-)(?<!non )irrigat', re.IGNORECASE)
# The land that produces the reference product: cropland for a crop, grazing
# land (pasture, grassland) for livestock.  Traffic areas, buildings and the
# like are used but not farmed, and counting them would understate the yield.
_OCCUPATION_RE     = re.compile(r'^\s*occupation,\s*(?:(?:annual|permanent)\s+crop\b'
                                r'|pasture\b|grassland\b)',
                                re.IGNORECASE)

# Land-occupation units the yield can be read from, as m2*year per unit.
_OCCUPATION_UNITS = {'m2*year': 1.0, 'm2a': 1.0, 'm2*a': 1.0,
                     'ha*year': 10000.0, 'ha*a': 10000.0, 'ha_a': 10000.0, 'haa': 10000.0}

# What a yield can plausibly be, in kg/ha.  A crop yields hundreds to tens of
# thousands; livestock on rangeland yields a few kg of fibre, or less.
# Anything outside the range is a unit mix-up, not a yield.
_CROP_YIELD_RANGE      = (100.0, 300_000.0)
_LIVESTOCK_YIELD_RANGE = (0.01, 300_000.0)


def _activity_rows(row, exchanges):
    """The exchange rows belonging to this meta row's activity."""
    if exchanges is None or getattr(exchanges, 'empty', True):
        return None
    name = str(row.get('activityName') or '').strip()
    if name and 'activityName' in exchanges.columns:
        same = exchanges[exchanges['activityName'].astype(str).str.strip() == name]
        if not same.empty:
            return same
    return exchanges


def has_irrigation_exchange(row, exchanges=None) -> bool:
    """True when the inventory actually buys irrigation for this activity."""
    rows = _activity_rows(row, exchanges)
    if rows is None or 'name' not in rows.columns:
        return False
    hits = rows[rows['name'].astype(str).str.contains(_IRRIGATION_RE, na=False)]
    if hits.empty:
        return False
    if 'amount' in hits.columns:
        amounts = pd.to_numeric(hits['amount'], errors='coerce').fillna(0)
        return bool((amounts != 0).any())
    return True


def water_regime_of(row, exchanges=None) -> str:
    """
    The water regime of a dataset, decided rather than guessed.

    What the dataset says about itself comes first.  Failing that, the
    inventory settles it: a crop that buys no irrigation is rainfed -- there
    is no third possibility, so this is never a choice to put to a human.
    """
    stated = spec.axis_value_in_text(dataset_prose(row), 'water_regime')
    if stated:
        return stated
    return 'irrigated' if has_irrigation_exchange(row, exchanges) else 'rainfed'


def derive_yield(row, exchanges=None):
    """
    Yield in kg/ha from the land occupation exchange, or None.

    Occupation is the land-YEARS a unit of product uses, so a hectare-year
    divided by it is the yield per hectare per year -- for an orchard as
    much as for a field crop.  Transformation is deliberately not used: on a
    perennial it is amortised over the life of the plantation, and reading a
    yield out of it overstates it by a factor of the orchard's lifetime
    (it made a mandarin dataset yield 638 t/ha).

    Only the cultivated categories count.  Occupation of a traffic area or a
    building is land the activity uses but does not crop, and adding it in
    would understate the yield.
    """
    rows = _activity_rows(row, exchanges)
    if rows is None or 'name' not in rows.columns or 'amount' not in rows.columns:
        return None

    land = rows[rows['name'].astype(str).str.contains(_OCCUPATION_RE, na=False)]
    if land.empty:
        return None
    if 'unitName' in land.columns:
        factor = land['unitName'].map(
            lambda u: _OCCUPATION_UNITS.get(
                '' if pd.isna(u) else str(u).replace(' ', '').lower()))
    else:
        factor = pd.Series(1.0, index=land.index)
    amounts = pd.to_numeric(land['amount'], errors='coerce')
    usable = factor.notna() & amounts.notna() & (amounts > 0)
    if not usable.any():
        return None
    area_years = float((amounts[usable] * factor[usable]).sum())
    if not area_years:
        return None
    grazing = land.loc[usable, 'name'].astype(str).str.contains(
        r'pasture|grassland', case=False).all()

    # The occupation is per reference product, so a functional unit other
    # than 1 kg has to be divided out before the hectare is.
    product = 1.0
    if 'group' in rows.columns:
        reference = rows[rows['group'].astype(str).str.contains('ReferenceProduct',
                                                                na=False)]
        if 'unitName' in reference.columns:
            reference = reference[
                reference['unitName'].astype(str).str.strip().str.lower() == 'kg']
        amounts = (pd.to_numeric(reference['amount'], errors='coerce').dropna()
                   if not reference.empty else [])
        amounts = [a for a in amounts if a > 0]
        if not amounts:
            return None            # not measured in kg: the yield is not kg/ha
        product = amounts[0]

    yield_per_ha = 10000.0 * product / area_years
    low, high = _LIVESTOCK_YIELD_RANGE if grazing else _CROP_YIELD_RANGE
    if not low <= yield_per_ha <= high:
        return None
    return yield_per_ha


def stated_training_form(row) -> str:
    """
    The training form the dataset's prose names, or '' when it names none.

    A woody perennial whose text says nothing about training is not missing
    anything the model can supply, so the axis is left out of the name
    entirely rather than guessed at from a list.
    """
    base = spec.canonical_segments(row.get('activityName'))
    if not base:
        return ''
    crop = next((c for c in checks.ALL_CROP_NAMES if c in base[0].lower()), None)
    if not crop or crop not in checks.WOODY_PERENNIAL_CROPS:
        return ''
    return spec.axis_value_in_text(dataset_prose(row), 'training_system')


def mechanical_name_repairs(row) -> tuple:
    """(segments, notes) after the repairs that need no decision from you."""
    name = str(row.get('activityName') or '')
    notes = []
    if _AT_PHRASE_RE.search(name):
        name = _AT_PHRASE_RE.sub('', name)
        notes.append("dropped the v2 'at farm'/'at plant' phrase")

    segments = spec.canonical_segments(name)
    cleaned = []
    for index, segment in enumerate(segments):
        # "hill region" is an agro-zone, and the nomenclature writes the zone
        # alone.  Only the axes are touched; the crop base keeps its words.
        if index and _REGION_WORD_RE.search(segment):
            segment = _REGION_WORD_RE.sub('', segment).strip()
            notes.append("dropped the word 'region'")
        cleaned.append(segment)
    segments = [segment for segment in cleaned if segment]

    # The geography belongs in the Geography field, never in the name.
    geography = str(row.get('geography') or '').strip()
    if len(geography) > 1 and segments:
        kept = [segments[0]] + [s for s in segments[1:]
                                if s.lower() != geography.lower()]
        if len(kept) != len(segments):
            notes.append(f"removed the geography '{geography}' from the name")
            segments = kept
    return segments, notes


# ── The name the dataset's own evidence gives ───────────────────────────────
# Set automatically, not asked: the inventory says whether the crop is
# irrigated, and the dataset's own text which production system it follows.
# The previous name is the starting point -- its product, its other axes, its
# wording -- and only what the evidence settles is changed.

AUTO_NAME_KIND = 'activity name set from the dataset'
_DRY_WORDS = ('rainfed', 'non-irrigated', 'non irrigated')


def _axis_index(segments, words) -> int | None:
    """The position of the segment that is one of `words`, if any (not the base)."""
    return next((i for i, s in enumerate(segments) if i and s.lower() in words), None)


def evidence_name(row, exchanges=None, add_production: bool = True) -> tuple:
    """
    (name, reasons, notes): the activityName the evidence gives, why each
    change was made, and what the evidence disagrees on (for a reviewer).

      - the v2 'at farm', the word 'region' and the geography come out;
      - "production" goes after the product (unless `add_production` is off);
      - the water regime follows the inventory: irrigated when it has an
        irrigation exchange, else rainfed (or the dry wording the name had);
      - the production system is the one the text clearly states, put in or
        corrected; a text that does not clearly state one leaves it alone;
      - a woody perennial's training form, where the text names it.
    """
    current = spec.join_segments(spec.canonical_segments(row.get('activityName')))
    segments, reasons = mechanical_name_repairs(row)
    if not segments:
        return current, [], []
    notes = []
    prose = dataset_prose(row)

    joined = spec.join_segments(segments)
    if add_production and spec.lacks_production_word(joined):
        best = spec.production_name_options(
            joined, reference_product_of(exchanges, row.get('activityName')))[0][1]
        if best != joined:
            segments = spec.canonical_segments(best)
            reasons.append('"production" put after the product')

    # Water regime: the inventory decides.
    irrigated = has_irrigation_exchange(row, exchanges)
    index = _axis_index(segments, ('irrigated',) + _DRY_WORDS)
    word = segments[index] if index is not None else None
    want = 'irrigated' if irrigated else (word if word and word.lower() in _DRY_WORDS
                                          else 'rainfed')
    evidence = ('the inventory has an irrigation exchange' if irrigated
                else 'the inventory has no irrigation exchange')
    if word is None:
        segments = spec.insert_axis_value(segments, 'water_regime', want)
        reasons.append(f'water regime "{want}" added: {evidence}')
    elif (word.lower() == 'irrigated') != irrigated:
        segments[index] = want
        reasons.append(f'"{word}" changed to "{want}": {evidence}')
    stated = spec.dominant_axis_value(prose, 'water_regime')
    if stated and (stated == 'irrigated') != irrigated:
        notes.append(f'the text says "{stated}", but {evidence} -- the name follows the '
                     f'inventory; check the text or the inventory')

    # Production system: the text decides, where it is clear.
    systems = spec.AXIS_VOCABULARIES['production_system']
    index = _axis_index(segments, systems)
    stated = spec.dominant_axis_value(prose, 'production_system')
    if stated and index is None:
        segments = spec.insert_axis_value(segments, 'production_system', stated)
        reasons.append(f'production system "{stated}" added: the dataset text states it')
    elif stated and index is not None and segments[index].lower() != stated:
        reasons.append(f'"{segments[index]}" changed to "{stated}": the dataset text states '
                       f'"{stated}"')
        segments[index] = stated

    training = stated_training_form(row)
    if training and _axis_index(segments, spec.AXIS_VOCABULARIES['training_system']) is None:
        segments = spec.insert_axis_value(segments, 'training_system', training)
        reasons.append(f'training form "{training}" added: the dataset text names it')

    return spec.join_segments(segments), reasons, notes


def name_options(row, exchanges=None) -> list:
    """
    Candidate corrected activityNames, best guess first: [(label, name)].

    Empty when the model has nothing to suggest, in which case the finding is
    reported exactly as before.
    """
    current = spec.join_segments(spec.canonical_segments(row.get('activityName')))
    segments, notes = mechanical_name_repairs(row)
    if not segments:
        return []

    axis_text = ' '.join(segments[1:]).lower()
    prose = dataset_prose(row)

    required = list(_REQUIRED_AXES)
    # The water regime is never a guess: the dataset says so itself, or its
    # inventory does.  A crop that buys no irrigation is rainfed.
    certain = {'water_regime': water_regime_of(row, exchanges)}
    training = stated_training_form(row)
    if training:
        required.append('training_system')
        certain['training_system'] = training

    missing = [axis for axis in required
               if not any(re.search(r'\b' + re.escape(term) + r'\b', axis_text)
                          for term in spec.AXIS_VOCABULARIES.get(axis, ()))]

    if not missing:
        repaired = spec.join_segments(segments)
        if repaired != current:
            return [('; '.join(notes) or 'formatting repaired', repaired)]
        return []

    inferred = {axis: spec.axis_value_in_text(prose, axis) for axis in missing}
    # Where the value is certain -- the dataset states it -- the alternatives
    # are not offered: there is nothing to choose between.
    choices  = {axis: ([certain[axis]] if axis in certain
                       else spec.axis_choices(axis, inferred[axis]))
                for axis in missing}
    best     = {axis: values[0] for axis, values in choices.items() if values}

    def build(picked):
        out = list(segments)
        for axis in required:
            if axis in picked:
                out = spec.insert_axis_value(out, axis, picked[axis])
        return spec.join_segments(out)

    def label(picked):
        parts = []
        for axis in missing:
            if axis not in picked:
                continue
            hint = ' (from the dataset text)' if inferred[axis] == picked[axis] else ''
            parts.append(f'{axis.replace("_", " ")} = {picked[axis]}{hint}')
        return '; '.join(notes + parts)

    options, seen = [], set()
    for axis in [None] + missing:
        values = [best.get(axis)] if axis is None else choices.get(axis, ())
        for value in values:
            picked = dict(best) if axis is None else dict(best, **{axis: value})
            name = build(picked)
            if name in seen:
                continue
            seen.add(name)
            options.append((label(picked), name))

    options.append(('leave the name unchanged (the finding returns next run)',
                    current))
    return options


def _year(value):
    match = re.search(r'(19|20)\d{2}', str(value or ''))
    return int(match.group(0)) if match else None


# ── Included activities, built from the exchanges ───────────────────────────
# The review checklist (rows 35-38) fixes what the two fields say:
#   Start  the starting point, by the nature of the inputs and where they are
#          received; an input is named as "the input of X is included", never
#          "X is included", which reads as if producing X were part of it.
#   End    the last activity or point of delivery, and whatever is
#          intentionally left out -- consistent with the exchanges.
# The house wording opens them "This activity starts ..." / "This activity
# ends ...".  Everything below is read off the exchanges of the activity, so
# the text says what the inventory actually contains.

INCLUDED_END_COLUMN = 'includedActivitiesEnd'

# What a purchased input IS, by its name.  First match wins, so the specific
# categories come before the broad ones ("diesel used in ..." is fuel for a
# field operation, not the operation itself).
_INPUT_CATEGORIES = (
    ('seeds and seedlings',      r'\bseeds?\b|\bseedlings?\b'),
    ('feed',                     r'\bfeed\b|\bsilage\b|\bhay\b|\bfodder\b|\bconcentrate\b|\bforage\b'),
    ('mineral fertilisers',      r'fertili[sz]er|\burea\b|\bammonium\b|\bphosphate\b|potassium (?:sulfate|chloride)|\bnitrate\b|\blime\b|\bcalcium carbonate\b'),
    ('organic fertilisers',      r'\bmanure\b|\bcompost\b|\bslurry\b|\bdigestate\b'),
    # Before fuels: "transport, passenger, car, petrol" is transport.
    ('transport',                r'\btransport\b'),
    ('fuels',                    r'\bdiesel\b|\bpetrol\b|\bgasoline\b|\bnatural gas\b|\bliquefied petroleum\b'),
    ('electricity',              r'\belectricity\b'),
    ('irrigation water',         r'\birrigation\b|\btap water\b|\bwater\b'),
    ('packaging',                r'\bpackaging\b'),
    ('machinery and buildings',  r'\bmachine\b|\bmachinery\b|\btractor\b|\bbuilding\b|\bshed\b'),
)
_ESTABLISHMENT_RE = re.compile(r'\bplanting\b|\bestablishing orchard\b|\bfor planting\b', re.I)
_SOIL_START_RE    = re.compile(r'\btillage\b|\bplough|\bharrow|\bsowing\b|\bsoil preparation\b', re.I)

_PESTICIDES: set | None = None
_FIELD_OPERATIONS: set | None = None


def _pesticide_names() -> set:
    """The OLCApest list of pesticides, loaded once; empty if unavailable."""
    global _PESTICIDES
    if _PESTICIDES is None:
        try:
            import verify_output
            _PESTICIDES = verify_output.pesticide_names()
        except Exception:
            _PESTICIDES = set()
    return _PESTICIDES


def _field_operations() -> set:
    """Every field operation the diesel step knows, lowercased."""
    global _FIELD_OPERATIONS
    if _FIELD_OPERATIONS is None:
        try:
            from config_new_datasets import FO_DIESEL_INTENSITIES
            _FIELD_OPERATIONS = {str(key[0] if isinstance(key, tuple) else key).strip().lower()
                                 for key in FO_DIESEL_INTENSITIES}
        except Exception:
            _FIELD_OPERATIONS = set()
    return _FIELD_OPERATIONS


# A purchased input named as a plant protection product rather than as a
# substance -- "pesticide, unspecified" is how the citrus datasets record it.
_PLANT_PROTECTION_RE = re.compile(
    r'\b(?:pesticides?|fungicides?|herbicides?|insecticides?|acaricides?|nematicides?'
    r'|molluscicides?|rodenticides?|plant protection products?)\b', re.IGNORECASE)


def uses_pesticides(row, exchanges) -> bool:
    """
    Whether the activity uses any plant protection product, read off its
    exchanges: an input that is a pesticide substance or is named as one, or
    a pesticide emission (which OLCApest only writes where one is applied).
    The spraying operation and pesticide packaging are not pesticides.
    """
    rows = _activity_rows(row, exchanges)
    if rows is None or 'name' not in rows.columns or 'group' not in rows.columns:
        return False
    group = rows['group'].astype(str).str.strip()
    names = rows['name'].map(lambda v: '' if pd.isna(v) else str(v).strip())
    known = _pesticide_names()
    operations = _field_operations()
    for name in names[group == 'FromTechnosphere']:
        low = name.lower()
        if not low or low in operations or low.startswith(('diesel used in', 'packaging')):
            continue
        if low in known or _PLANT_PROTECTION_RE.search(low):
            return True
    return any(name.lower() in known for name in names[group == 'ToEnvironment'] if name)


def _join_words(items) -> str:
    """'a, b and c' -- or 'a; b and c' when an item carries its own commas,
    as field operations do ("tillage, harrowing, by tandem disk")."""
    items = list(dict.fromkeys(i for i in items if i))
    if len(items) <= 1:
        return ''.join(items)
    separator = '; ' if any(',' in i for i in items) else ', '
    return separator.join(items[:-1]) + ' and ' + items[-1]


def exchange_contents(row, exchanges) -> dict:
    """
    What the activity's exchanges contain, in the terms the two fields use:
    input categories, field operations, whether an orchard is established,
    whether soil is cultivated, and what it delivers.
    """
    rows = _activity_rows(row, exchanges)
    out = {'inputs': [], 'operations': [], 'establishment': False,
           'soil_start': False, 'products': [], 'has_pesticides': False}
    if rows is None or 'name' not in rows.columns or 'group' not in rows.columns:
        return out
    group = rows['group'].astype(str).str.strip()
    names = rows['name'].map(lambda v: '' if pd.isna(v) else str(v).strip())
    operations_known = _field_operations()
    pesticides = _pesticide_names()
    categories = []
    for name in names[group == 'FromTechnosphere']:
        low = name.lower()
        if not low:
            continue
        if low.startswith('diesel used in'):
            categories.append('fuels')
            continue
        if low in operations_known:
            out['operations'].append(name)
            continue
        if _ESTABLISHMENT_RE.search(low):
            out['establishment'] = True
            continue
        if low in pesticides or (_PLANT_PROTECTION_RE.search(low)
                                 and not low.startswith('packaging')):
            categories.append('plant protection products')
            continue
        for label, pattern in _INPUT_CATEGORIES:
            if re.search(pattern, low):
                categories.append(label)
                break
        else:
            categories.append('other materials')
    order = ['seeds and seedlings', 'feed', 'mineral fertilisers', 'organic fertilisers',
             'plant protection products', 'fuels', 'electricity', 'irrigation water',
             'transport', 'packaging', 'machinery and buildings', 'other materials']
    out['inputs'] = sorted(set(categories), key=order.index)
    out['operations'] = list(dict.fromkeys(out['operations']))
    out['soil_start'] = any(_SOIL_START_RE.search(o) for o in out['operations'])
    out['has_pesticides'] = uses_pesticides(row, exchanges)
    for g in ('ReferenceProduct', 'ByProduct'):
        out['products'] += list(names[group == g])
    return out


def build_included_start(row, exchanges, facts: dict) -> str:
    """includedActivitiesStart, from the exchanges (checklist rows 35-36)."""
    info = exchange_contents(row, exchanges)
    crop = facts.get('crop') or 'the crop'
    if info['establishment']:
        opening = ('This activity starts with the fruit tree seedling, planting a '
                   'tree and establishing an orchard.')
    elif info['soil_start']:
        opening = f'This activity starts with soil preparation before sowing the {crop}.'
    elif info['operations']:
        opening = f'This activity starts with the first field operation for the {crop}.'
    else:
        # No field operation at all -- livestock, or an activity the inventory
        # describes only through its inputs: the starting point is where
        # those inputs are received.
        opening = 'This activity starts with the reception of the inputs at the farm.'
    sentences = [opening]
    if info['inputs']:
        sentences.append(f'The input of {_join_words(info["inputs"])} is included.')
    if info['operations']:
        sentences.append(f'The activities of {_join_words(info["operations"])} are included.')
    return ' '.join(sentences)


def included_end_opening(row, exchanges, facts: dict) -> str:
    """The standard first sentence of includedActivitiesEnd."""
    crop = facts.get('crop') or 'the crop'
    if exchange_contents(row, exchanges)['operations']:
        return f'This activity ends after the harvesting of the {crop}.'
    return f'This activity ends with the delivery of the {crop} products at the farm gate.'


def build_included_end(row, exchanges, facts: dict, keep: str = '') -> str:
    """
    includedActivitiesEnd, from the exchanges (checklist rows 37-38).

    `keep` is the dataset's own end-of-boundary text, when it has one: it
    already says what is left out, so it follows the standard opening rather
    than being replaced by a generic exclusion.
    """
    opening = included_end_opening(row, exchanges, facts)
    keep = tidy_text(keep)
    if keep:
        return opening + ' ' + keep
    return (opening + ' All activities after the farm gate, including transport, '
            'processing, storage and use of the products, are not included.')


# ISIC Rev.4, division 01, for when neither this project's profile nor any
# other project's gives the family a code.  Matched on whole words of the
# activity's first segment (the crop or animal), specific classes first.
_ISIC_REV4 = (
    (r'grapes?|vine|wine', '0121:Growing of grapes'),
    (r'oranges?|lemons?|mandarins?|clementines?|citrus|grapefruits?|limes?|citrons?|tangerines?',
     '0123:Growing of citrus fruits'),
    (r'olives?|oil palm|coconuts?', '0126:Growing of oleaginous fruits'),
    (r'apples?|pears?|quinces?|peach(?:es)?|apricots?|cherr(?:y|ies)|plums?|nectarines?',
     '0124:Growing of pome fruits and stone fruits'),
    (r'almonds?|walnuts?|hazelnuts?|pistachios?|chestnuts?|strawberr(?:y|ies)|raspberr(?:y|ies)'
     r'|blueberr(?:y|ies)|kiwis?|berr(?:y|ies)|figs?',
     '0125:Growing of other tree and bush fruits and nuts'),
    (r'bananas?|mangoes|mangos?|avocados?|pineapples?|papayas?|dates?',
     '0122:Growing of tropical and subtropical fruits'),
    (r'coffee|tea|cocoa|cacao', '0127:Growing of beverage crops'),
    (r'rice', '0112:Growing of rice'),
    (r'wheat|barley|maize|corn|oats?|rye|sorghum|millet|triticale|soybeans?|soya|rapeseed'
     r'|sunflower|chickpeas?|lentils?|beans?|peas?|lupins?|faba',
     '0111:Growing of cereals (except rice), leguminous crops and oil seeds'),
    (r'potato(?:es)?|carrots?|onions?|tomato(?:es)?|lettuce|cabbages?|melons?|vegetables?'
     r'|garlic|peppers?|artichokes?|spinach|zucchini|courgettes?|cucumbers?|beets?',
     '0113:Growing of vegetables and melons, roots and tubers'),
    (r'sugar ?cane', '0114:Growing of sugar cane'),
    (r'tobacco', '0115:Growing of tobacco'),
    (r'cotton|flax|hemp|jute', '0116:Growing of fibre crops'),
    (r'cattle|cows?|dairy|beef|buffalo(?:es)?|calves|calf', '0141:Raising of cattle and buffaloes'),
    (r'horses?', '0142:Raising of horses and other equines'),
    (r'camels?', '0143:Raising of camels and camelids'),
    (r'sheep|goats?|cashmere|wool|lambs?', '0144:Raising of sheep and goats'),
    (r'pigs?|swine|hogs?', '0145:Raising of swine/pigs'),
    (r'chickens?|poultry|broilers?|eggs?|turkeys?|ducks?', '0146:Raising of poultry'),
    (r'seedlings?|nursery', '0130:Plant propagation'),
)
_ISIC_CODE_RE = re.compile(r'^\s*\d{4}\b')


def isic_for(family: str, families: dict) -> str | None:
    """
    The ISIC code for a family: this project's profile, else any other
    project's profile for the same family, else ISIC Rev.4 by what it grows
    or raises.
    """
    code = (families.get(family) or {}).get('isic')
    if code:
        return code
    for other in PROFILES.values():
        code = ((other.get('families') or {}).get(family) or {}).get('isic')
        if code:
            return code
    head = re.sub(r'\s+production$', '', str(family or '')).lower()
    for pattern, code in _ISIC_REV4:
        if re.search(rf'\b(?:{pattern})\b', head):
            return code
    return None


def house_wording(column: str, families: dict, family: str) -> str:
    """
    The family's house wording for a column, when there is exactly one and it
    needs no per-dataset value.  Then it IS the canonical text and can be
    offered as it stands.
    """
    templates = (families.get(family) or {}).get('templates', {}).get(column) or []
    if len(templates) == 1 and not _SLOT_RE.search(templates[0]):
        return templates[0]
    return ''


def propose_text(column: str, row, families: dict, family: str) -> str:
    """
    The canonical text for one comment column, or '' when it cannot be built.

    The same two routes every column gets: build it from the dataset's own
    facts where the template needs values (region, dates, crop), else take
    the family's house wording when that is a fixed string.
    """
    built = generate_column(column, row, families)
    if built:
        return built
    return house_wording(column, families, family)


# ── Economic allocation (allocationComment) ──────────────────────────────────
# The review skill's template states the allocation factor of the reference
# product and of each by-product, their prices in €/t, the reference period
# and two sources.  Factors, prices and period follow from the dataset (factor
# = amount x price / the sum over all products); the sources do not, so they
# stay as placeholders a human replaces -- an answer still carrying one is
# refused rather than written into the dataset.
ALLOCATION_SOURCE_PLACEHOLDERS = ('«yield source»', '«price source»')
_ALLOCATION_PRICE_UNITS = ('eur2005',)
_STATED_FACTORS_RE = re.compile(r'factor (?:of|is) (\d+(?:\.\d+)?)', re.I)


def economic_allocation(activity: str, exchanges, properties):
    """
    ([(group, name, factor, price_text), ...] reference product first, None)
    for an activity with at least one priced by-product, or (None, reason).
    The reason is '' when there is simply no economic allocation to describe.
    """
    if exchanges is None or exchanges.empty or not {'group', 'name', 'amount'} <= set(exchanges.columns):
        return None, ''
    rows = exchanges[exchanges['group'].isin(_PRODUCT_GROUPS)]
    if 'activityName' in rows.columns:
        rows = rows[_text_series(rows['activityName']) == activity]
    if not (rows['group'] == 'ByProduct').any():
        return None, ''
    prices = {}
    if properties is not None and not properties.empty and \
            {'name', 'propertyName', 'amount'} <= set(properties.columns):
        price_rows = properties[_text_series(properties['propertyName']).str.lower() == 'price']
        if 'activityName' in price_rows.columns:
            price_rows = price_rows[_text_series(price_rows['activityName']) == activity]
        for _, p in price_rows.iterrows():
            prices[str(p['name']).strip()] = (pd.to_numeric(p['amount'], errors='coerce'),
                                             str(p.get('unitName', '') or '').strip())
    products = []
    for _, r in rows.sort_values('group', key=lambda g: g != 'ReferenceProduct').iterrows():
        name = str(r['name']).strip()
        amount = pd.to_numeric(r['amount'], errors='coerce')
        price, unit = prices.get(name, (None, ''))
        products.append((r['group'], name, amount, price, unit,
                         str(r.get('unitName', '') or '').strip()))
    # A by-product with no price (or a price of 0) takes no share: nothing is
    # allocated to it, so the activity has no economic allocation to state.
    priced = [p[:5] for p in products if p[3] is not None and not pd.isna(p[3]) and p[3] > 0]
    unit_of = {p[1]: p[5] for p in products}
    if not any(p[0] == 'ByProduct' for p in priced):
        return None, ''
    priced_names = {p[1] for p in priced}
    unpriced_ref = [p[1] for p in products
                    if p[0] == 'ReferenceProduct' and p[1] not in priced_names]
    if unpriced_ref:
        return None, f'the reference product {unpriced_ref[0]!r} has no price'
    wrong_unit = [f'{p[1]} ({p[4] or "no unit"})' for p in priced
                  if p[4].lower() not in _ALLOCATION_PRICE_UNITS]
    if wrong_unit:
        return None, ('prices must be in EUR2005 before the factors can be stated: '
                      + ', '.join(wrong_unit))
    revenue = [(g, n, float(a) * float(p)) for g, n, a, p, _ in priced if not pd.isna(a)]
    total = sum(v for _, _, v in revenue)
    if total <= 0:
        return None, 'the products carry no revenue'
    def price_text(name, price):
        # A price per kg is quoted per tonne, as the template does; a product
        # in another unit is quoted per that unit rather than converted.
        if unit_of.get(name, 'kg').lower() == 'kg':
            return f'{price * 1000.0:.0f} €/t'
        return f'{price:.4g} €/{unit_of[name]}'
    price_of = {n: float(p) for _, n, _, p, _ in priced}
    return [(g, n, v / total, price_text(n, price_of[n])) for g, n, v in revenue], None


def allocation_comment_text(crop: str, shares: list, start_year, end_year,
                            yield_source: str = ALLOCATION_SOURCE_PLACEHOLDERS[0],
                            price_source: str = ALLOCATION_SOURCE_PLACEHOLDERS[1]) -> str:
    ref = shares[0]
    # The crop where the reference product is named after it ('wheat' for
    # 'wheat grain', as the existing comments write it); otherwise -- an
    # animal product like 'raw cashmere' -- the product itself.
    if not crop or crop.lower() not in ref[1].lower():
        crop = ref[1]
    by_products = [(n, f'{f:.3f}', p) for g, n, f, p in shares[1:]]
    years = f'{start_year}-{end_year}' if start_year and end_year else '«reference period»'
    return gen.generate_allocation_comment_multi(
        crop, f'{ref[2]:.3f}', ref[3], by_products,
        yield_source, years, price_source)


def stated_factors_mismatch(text: str, shares: list) -> str:
    """'' when the factors a comment states match the computed ones (to the
    three decimals they are written with), else what differs."""
    stated = [float(x) for x in _STATED_FACTORS_RE.findall(str(text))]
    if not stated:
        return ''
    computed = [f for _, _, f, _ in shares]
    if len(stated) == len(computed) and all(abs(s - c) < 0.0015 for s, c in zip(stated, computed)):
        return ''
    return (f"states factor(s) {', '.join(f'{s:.3f}' for s in stated)}, but the "
            f"prices and amounts in this dataset give "
            + ', '.join(f'{n} {f:.3f}' for _, n, f, _ in shares))


def generate_column(column: str, row, families=None) -> str | None:
    """
    Build a missing prose column from generate_metadata's template.

    Only where every input the template needs can be derived from the dataset
    -- nothing is invented.  generalComment is deliberately NOT generated: its
    template needs the yield, and the yield lives in the generalComment, so a
    blank one cannot supply it.  That stays BLOCKING for a human.
    """
    facts = name_facts(row, families)
    if not facts.get('crop'):
        return None

    given = meta_facts.values(row)

    if column == 'geographyComment':
        region = facts.get('region')
        system = facts.get('production_system')
        if not (region and system):
            return None
        return gen.generate_geography_comment(
            crop_name=facts['crop'], production_system=system,
            region=spec.region_phrase(region, spec.country_name(row.get('geography'))),
            percent_national_production=meta_facts.whole(given.get('share_of_national_production')),
            subzone_detail=given.get('region_detail'))

    if column == 'timePeriodComment':
        start, end = _year(row.get('startDate')), _year(row.get('endDate'))
        if not (start and end and end >= start):
            return None
        # Seasons from the length of the period, not the calendar years it
        # touches (2021-08 to 2022-08 is one), and the averaging period the
        # generalComment states, so the two comments say the same.
        return gen.generate_time_period_comment(
            start_year=start, end_year=end, crop_name=facts['crop'],
            field_collected=given.get('data_source') == 'field',
            n_years=spec.agricultural_seasons(row.get('startDate'), row.get('endDate')),
            average_years=spec.stated_average_years(row.get('generalComment')))

    if column == 'modellingAndValidation_samplingProcedure':
        return gen.generate_sampling_procedure(
            crop_name=facts['crop'],
            production_system=facts.get('production_system'),
            region=spec.region_phrase(facts.get('region'),
                                      spec.country_name(row.get('geography'))),
            data_source=given.get('data_source', ''),
            start_year=_year(row.get('startDate')),
            end_year=_year(row.get('endDate')),
            n_farms=meta_facts.whole(given.get('farms_sampled')),
            source_reference=given.get('data_source_reference'))

    if column == spec.EXTRAPOLATIONS_COLUMN:
        return extrapolations_text(row, None)

    return None


def extrapolations_text(row, exchanges) -> str:
    """The extrapolations field from the facts sheet, followed by the locked
    pesticide-emission sentence where the dataset uses pesticides."""
    stated = str(meta_facts.provided(row).get('extrapolations', '') or '').strip()
    parts = [stated.rstrip('.') + '.'] if stated else []
    if exchange_contents(row, exchanges)['has_pesticides']:
        parts.append(spec.PESTICIDE_EMISSION_SENTENCE)
    return ' '.join(parts)


# ── Validation / normalisation of one meta row ───────────────────────────────

def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) \
        or str(value).strip().lower() in ('', 'nan', 'none')


def process_meta_row(row: pd.Series, dataset: str, apply_fixes: bool,
                     profile: dict | None = None, project: str = '',
                     exchanges=None, decisions: dict | None = None,
                     properties=None
                     ) -> tuple[pd.Series, list[Finding], str | None]:
    """
    Check one meta row against the spec.

    Returns (row, findings, new_activity_name).  When apply_fixes is False the
    row comes back untouched and every finding is reported as 'review'.
    """
    row      = row.copy()
    findings: list[Finding] = []
    original = str(row.get('activityName', '') or '')
    decisions = decisions if decisions is not None else {}
    original_cells = row.to_dict()
    # Whether plant protection products are used at all: the locked
    # pesticide-emission sentence is only true of a dataset that uses them.
    pesticides = uses_pesticides(row, exchanges)

    def note(column, found, expected, kind, fixed=False,
             severity='WARNING', detail='', checklist_row='', proposal='',
             options=()):
        # Reused only when `found` is the WHOLE cell, not a fragment of it
        # (an opening phrase, a yield declaration): the answer replaces the
        # whole cell, so it is only right where the whole cell was the same.
        # activityName is never reused here: a rename has to propagate to
        # every sheet, which only apply_fix does.
        if (severity in NEEDS_HUMAN and column in row.index
                and column != 'activityName'
                and spec.canonical_column_text(column, found, pesticides)
                == spec.canonical_column_text(column, original_cells.get(column), pesticides)):
            cached = decisions.get(decision_signature(column, kind, found) or '')
            if cached:
                value = cached['value']
                _set_cell_value(row, column, value)
                findings.append(Finding(
                    dataset, original, column, found, value, kind, True, 'WARNING',
                    f'Remembered decision reused from {cached["first_seen"]} -- '
                    f'delete it from config/review/{DECISIONS_FILENAME} to ask again. {detail}'.strip(),
                    checklist_row, project, value, options))
                return
        findings.append(Finding(dataset, original, column, found, expected, kind,
                                fixed, severity, detail, checklist_row, project,
                                proposal, options))

    # 1. activityName ---------------------------------------------------------
    profile   = profile or {}
    families  = families_of(profile)
    canonical, problems = canonical_activity_name(original, families, project)
    renamed_to = None
    for problem in problems:
        note('activityName', original, problem, 'nomenclature', False,
             'INFO' if 'not in the profile' in problem else 'WARNING')
    if canonical and canonical != original:
        note('activityName', original, canonical, 'non-canonical name', apply_fixes)
        if apply_fixes:
            row['activityName'] = canonical
            renamed_to = canonical

    # A productive activity carries "production" after its product.  Where
    # the word goes is a naming call, so the options are offered, best first;
    # an answer given once is reused for the same name everywhere, a "keep"
    # included.  Not a remembered-decision note(): a rename has to reach every
    # sheet, so a reused answer goes out through renamed_to like the above.
    name_now = str(row.get('activityName', '') or '')
    add_production = True
    if spec.lacks_production_word(name_now):
        cached = decisions.get(decision_signature('activityName', PRODUCTION_NAME_KIND,
                                                  name_now) or '')
        if cached and spec.same_activity_name(cached['value'], name_now):
            add_production = False                   # answered: not a production
        elif cached and apply_fixes:
            new = spec.join_segments(spec.canonical_segments(cached['value']))
            findings.append(Finding(
                dataset, original, 'activityName', name_now, new, PRODUCTION_NAME_KIND,
                True, 'WARNING',
                f'Remembered decision reused from {cached["first_seen"]} -- delete it '
                f'from config/review/{DECISIONS_FILENAME} to ask again.', '', project, new))
            row['activityName'] = new
            renamed_to = new
            name_now = new

    # The name the dataset's evidence gives -- "production" after the product,
    # the water regime from the inventory, the production system from the
    # text -- set automatically.  apply = no in Corrections keeps the name.
    built, reasons, disagreements = evidence_name(row, exchanges, add_production)
    activity_part = ACTIVITY_SEP + original if str(original or '').strip() else ''
    rejected_name = _fix_rejected(f'{project}||{dataset}{activity_part}|activityName|'
                                  f'{AUTO_NAME_KIND}')
    if built and built != spec.join_segments(spec.canonical_segments(name_now)) \
            and not rejected_name:
        note('activityName', name_now, built, AUTO_NAME_KIND, apply_fixes,
             'WARNING' if apply_fixes else 'BLOCKING',
             'Set from the dataset: ' + '; '.join(reasons) + '.'
             + (' Note: ' + '; '.join(disagreements) + '.' if disagreements else '')
             + ' Set apply = no in Corrections to keep the previous name.',
             '', built)
        if apply_fixes:
            row['activityName'] = built
            renamed_to = built
    elif disagreements:
        note('activityName', name_now, name_now, 'name and dataset text disagree', False,
             'INFO', '; '.join(disagreements) + '.')

    family, _axes = parse_activity_name(original)

    # 2. constant columns -----------------------------------------------------
    for column, expected in (profile.get('constants') or {}).items():
        if column not in row.index:
            continue
        found = row[column]
        # Compared case-sensitively: 'current' is not the canonical 'Current',
        # and the capitalisation is part of what the constant specifies.
        same = (str(found).strip() == str(expected).strip())
        if _blank(found):
            note(column, found, expected, 'missing', apply_fixes)
            if apply_fixes:
                row[column] = expected
        elif not same:
            note(column, found, expected, 'unexpected value', apply_fixes)
            if apply_fixes:
                row[column] = expected

    # 3. columns that must be empty -------------------------------------------
    for column in (profile.get('always_empty') or ()):
        if column in row.index and not _blank(row[column]):
            note(column, row[column], '', 'should be empty')

    # 4. ISIC, keyed on the crop family ---------------------------------------
    # This project's profile is the standard and is enforced.  Where it has no
    # code, another project's profile or ISIC Rev.4 fills a blank or a code
    # that is not four digits (checklist rows 45-46) -- but never overrides a
    # valid code someone chose, since the table is coarser than a person.
    if family and 'ISIC' in row.index:
        expected = (families.get(family) or {}).get('isic')
        found    = row['ISIC']
        if expected and str(found).strip() != expected:
            kind = 'missing' if _blank(found) else 'unexpected value'
            note('ISIC', found, expected, kind, apply_fixes)
            if apply_fixes:
                row['ISIC'] = expected
        elif not expected and (_blank(found) or not _ISIC_CODE_RE.match(str(found))):
            derived = isic_for(family, families)
            if derived:
                note('ISIC', found, derived,
                     'missing' if _blank(found) else 'not a 4-digit ISIC code',
                     apply_fixes, 'WARNING', f'ISIC Rev.4 for {family!r}.',
                     '45-46 (Checks)', derived)
                if apply_fixes:
                    row['ISIC'] = derived

    # 5. free columns: presence only ------------------------------------------
    for column in spec.FREE_COLUMNS:
        if column in row.index and _blank(row[column]):
            note(column, row[column], '(any)', 'missing')

    # 5b. the validity period, checked against the prose in both directions
    for (column, found_value, expected, kind, fixed, severity,
         detail, proposal, period_options) in review_period(row, apply_fixes):
        note(column, found_value, expected, kind, fixed, severity, detail,
             '28-59 (Checks)', proposal, period_options)

    # 5c. includedActivitiesStart / End, built from the exchanges -------------
    # Two different things (checklist rows 35-38): where the activity starts,
    # and where it ends plus what it leaves out.  An empty field is built from
    # the exchanges.  The SAME text in both is kept in the field it describes
    # -- a "This activity starts ..." text stays the start, anything else
    # (a boundary, an exclusion list) is the end -- and the other is built.
    start_col, end_col = spec.INCLUDED_START_COLUMN, INCLUDED_END_COLUMN
    if start_col in row.index and end_col in row.index:
        # A blank cell reads back as NaN, which tidy_text would turn into "nan".
        start = '' if _blank(row[start_col]) else tidy_text(row[start_col])
        end = '' if _blank(row[end_col]) else tidy_text(row[end_col])
        facts = name_facts(row, families)
        duplicated = bool(start) and start == end
        opens_start = spec.included_start_status(start)[0] in ('ok', 'reformat')
        # The family's reviewed house wording is the standard where it has
        # one; the exchanges are what a field is built from where it has none.
        start_text = (house_wording(start_col, families, family)
                      or build_included_start(row, exchanges, facts))
        end_text = (house_wording(end_col, families, family)
                    or build_included_end(row, exchanges, facts))
        rebuild = []
        if not start or (duplicated and not opens_start):
            rebuild.append((start_col, start_text,
                            'same text as the other included-activities field'
                            if duplicated else 'empty -- built from the exchanges'))
        if not end or (duplicated and opens_start):
            rebuild.append((end_col, end_text,
                            'same text as the other included-activities field'
                            if duplicated else 'empty -- built from the exchanges'))
        elif not re.match(r'\s*This activity ends?\b', end, re.IGNORECASE):
            # The dataset's own end text is kept -- it says what is left out --
            # behind the standard opening the field must start with.
            rebuild.append((end_col, build_included_end(row, exchanges, facts, keep=end),
                            'includedActivitiesEnd opening not standard'))
        for column, built, kind in rebuild:
            was = row[column]
            note(column, '(empty)' if _blank(was) else was, built, kind, apply_fixes,
                 'WARNING' if apply_fixes else 'ACCEPTABLE_FOR_NOW',
                 'Built from the exchanges of this activity, following the '
                 'review checklist (rows 35-38).', '35-38 (Checks)', built)
            if apply_fixes:
                _set_cell_value(row, column, built)

    # An empty extrapolations field in a dataset with pesticide inputs gets the
    # locked pesticide-emission sentence -- the one statement it must make.
    # The facts sheet's extrapolations come first, where the provider gave them.
    extrapolations = spec.EXTRAPOLATIONS_COLUMN
    built = (extrapolations_text(row, exchanges)
             if extrapolations in row.index and _blank(row[extrapolations]) else '')
    if built:
        stated = bool(meta_facts.provided(row).get('extrapolations'))
        note(extrapolations, '(empty)', built,
             'empty -- extrapolations filled from the facts sheet' if stated else
             'empty -- pesticide-emission sentence added', apply_fixes,
             'WARNING' if apply_fixes else 'ACCEPTABLE_FOR_NOW',
             ('From the facts sheet of the input dataset'
              + (', with the OLCA-Pest sentence for its pesticide inputs.'
                 if spec.PESTICIDE_EMISSION_SENTENCE in built else '.')) if stated else
             'The dataset has pesticide inputs, whose emissions this pipeline '
             'calculates with OLCA-Pest.', '28-59 (Checks)', built)
        if apply_fixes:
            _set_cell_value(row, extrapolations, built)

    # 6. templated text -------------------------------------------------------
    family_templates = (families.get(family) or {}).get('templates', {})
    for column in spec.TEMPLATE_COLUMNS:
        if column not in row.index:
            continue
        templates = family_templates.get(column) or []
        found     = row[column]

        if _blank(found):
            if column == 'allocationComment':
                # Only an activity whose by-products carry a price has an
                # economic allocation to state (review skill template).
                shares, reason = economic_allocation(original, exchanges, properties)
                if shares:
                    text = allocation_comment_text(
                        name_facts(row, families).get('crop'), shares,
                        _year(row.get('startDate')), _year(row.get('endDate')))
                    note(column, '(empty)', text,
                         'empty -- allocation stated, sources needed', False,
                         'ACCEPTABLE_FOR_NOW',
                         'Factors, prices (EUR2005 -> €/t) and the reference period '
                         'are computed from this dataset. The two sources are not '
                         'in it: replace ' + ' and '.join(ALLOCATION_SOURCE_PLACEHOLDERS)
                         + ' with the real references.', '42 (Checks)', text)
                elif reason:
                    note(column, '(empty)', '(an allocation comment)',
                         'empty -- allocation cannot be stated yet', False,
                         'ACCEPTABLE_FOR_NOW', f'The dataset has a priced by-product, '
                         f'but {reason}.', '42 (Checks)')
                continue
            if column in spec.OPTIONAL_COLUMNS:
                continue
            # Built from this dataset's own facts, or from the family's house
            # wording -- there is exactly one candidate and nothing existing
            # to lose, so it is filled in outright rather than queued for a
            # decision that is not really a decision.
            proposal = propose_text(column, row, families, family)
            if proposal:
                note(column, '(empty)', proposal, 'empty -- value filled in',
                     apply_fixes, 'WARNING' if apply_fixes else 'ACCEPTABLE_FOR_NOW',
                     'Built from this dataset\'s own facts, or from the house wording '
                     'for its crop family.', '28-59 (Checks)', proposal)
                if apply_fixes:
                    row[column] = proposal
            else:
                note(column, '(empty)', '(needs values that cannot be derived)',
                     'empty', False,
                     'BLOCKING' if column == 'generalComment' else 'ACCEPTABLE_FOR_NOW',
                     'Not invented: the template for this column needs a value this '
                     'dataset does not carry.', '28-59 (Checks)')
            continue

        tidied = spec.canonical_column_text(column, found, pesticides)

        # The yield line is a locked format, checked before the wording is
        # compared so the template sees the canonical line.  The figure itself
        # is never invented: a comment without one is BLOCKING.
        if column == 'generalComment':
            _, yield_status, yield_detail = spec.apply_yield_format(tidy_text(found))
            if yield_status == 'missing':
                # The figure is not invented -- but it need not be asked for
                # either when the inventory states it.  Land transformation
                # is the area per unit of product, so a hectare over that
                # area IS the yield; the line is then added outright.
                derived = derive_yield(row, exchanges)
                if derived is not None:
                    added = spec.set_yield_line(tidy_text(found), derived)
                    note(column, yield_detail or '(no yield line)',
                         spec.format_yield(derived), 'yield line added',
                         apply_fixes, 'WARNING' if apply_fixes else 'BLOCKING',
                         f'No yield line; {spec.format_yield(derived)} kg/ha follows '
                         f'from the land occupation exchange '
                         f'(1 ha / {10000.0 / derived:.6g} m2*year per kg), so the '
                         f'locked line is added.',
                         '28-34 (Checks) / 3 (agri)',
                         proposal=added)
                    if apply_fixes:
                        _set_cell_value(row, column, added)
                        found = added
                        tidied = spec.canonical_column_text(column, added, pesticides)
                else:
                    note(column, yield_detail or '(no yield line)',
                         spec.YIELD_TEMPLATE.format(value='26299.94'),
                         'yield line missing', False, 'BLOCKING',
                         'The yield is stated nowhere in the dataset and cannot be '
                         'derived from its land use, so it has to be supplied. See '
                         'meta_spec.format_yield() for the locked format.',
                         '28-34 (Checks) / 3 (agri)')
            elif yield_status == 'unit':
                converted = spec.convert_yield_unit(tidy_text(found))
                if converted is not None:
                    canonical_yield = spec.yield_line_status(converted)[1]
                    note(column, yield_detail, canonical_yield,
                         'yield stated in another unit', apply_fixes,
                         'WARNING' if apply_fixes else 'BLOCKING',
                         'tonnes/ha -> kg/ha is an exact, universal factor (x1000), '
                         'not a judgement call, so it is converted outright.',
                         '28-34 (Checks) / 3 (agri)', proposal=converted)
                    if apply_fixes:
                        _set_cell_value(row, column, converted)
                        found = converted
                        tidied = spec.canonical_column_text(column, converted, pesticides)
                else:
                    note(column, yield_detail, f'yield in {spec.YIELD_UNIT}',
                         'yield stated in another unit', False, 'BLOCKING',
                         'Converting the unit changes the figure, so it is left to '
                         'a human.', '28-34 (Checks) / 3 (agri)')
            elif yield_status == 'reformat':
                canonical_yield = spec.yield_line_status(tidy_text(found))[1]
                note(column, yield_detail, canonical_yield,
                     'yield line not in the locked format', apply_fixes,
                     'WARNING' if apply_fixes else 'BLOCKING',
                     'Locked: Yield: [value] kg/ha -- period separator, no thousands '
                     'separator, always 2 decimals (9000.00).',
                     '28-34 (Checks) / 3 (agri)')
            elif yield_status == 'reformat_inline':
                # The figure is there but stated mid-sentence. The splice only
                # ever touches the bare "Yield: value unit" declaration itself
                # -- never the words around it -- so it is exactly as safe as
                # the plain 'reformat' case above and is applied the same way
                # (canonical_text below already carries it either way; this
                # just makes the finding say so instead of reporting BLOCKING
                # on a cell that was, in fact, already being rewritten).
                note(column, yield_detail, spec.yield_line_status(tidy_text(found))[1],
                     'yield line not in the locked format (stated mid-sentence)',
                     apply_fixes, 'WARNING' if apply_fixes else 'BLOCKING',
                     'Locked: Yield: [value] kg/ha -- period separator, no thousands '
                     'separator, always 2 decimals (9000.00). The figure sat '
                     'inside a longer sentence; only the declaration itself was spliced, '
                     'the rest of the sentence is untouched.',
                     '28-34 (Checks) / 3 (agri)')
        # ── Mechanical repairs, applied whether or not the prose matches ────
        # Whitespace, the final stop and the locked yield line are safe and
        # independent of the wording.  Writing them only inside the branch
        # that happened to fire left the tidying on the floor for a dataset
        # whose prose diverges -- exactly the datasets most in need of it.
        # Both repairs go in together: doing one and leaving the other to the
        # next run would mean the model never settles in a single pass.
        # canonical_column_text already carries the final stop, so what each
        # repair was for is read off the ORIGINAL rather than the result.
        canonical_text = tidied
        issues = []
        if tidy_text(found) != str(found):
            issues.append('whitespace')
        if not ends_with_stop(tidy_text(found)):
            issues.append('missing final stop')
        if issues:
            # Severity MINOR: applied, never queued for review.
            note(column, found, canonical_text, ' + '.join(issues), apply_fixes,
                 MINOR, 'Cosmetic tidy-up, applied automatically.')
        if apply_fixes and canonical_text != str(found):
            row[column] = canonical_text

        # The locked pesticide-emission sentence.  How those emissions were
        # calculated is fixed by the pipeline (OLCA-Pest), so any wording of
        # it is replaced and a column that never mentioned it gains it.
        if column == spec.EXTRAPOLATIONS_COLUMN and not pesticides:
            if spec.remove_pesticide_sentence(tidy_text(found))[1] == 'removed':
                note(column, spec.PESTICIDE_EMISSION_SENTENCE, '(sentence removed)',
                     'pesticide-emission sentence removed (no pesticides used)',
                     apply_fixes, 'WARNING',
                     'The exchanges contain no plant protection product and no '
                     'pesticide emission, so the statement that pesticide '
                     'emissions were calculated is not true of this dataset.',
                     '28-59 (Checks)')
        elif column == spec.EXTRAPOLATIONS_COLUMN:
            _, pest_status = spec.apply_pesticide_sentence(tidy_text(found))
            if pest_status in ('replaced', 'added'):
                note(column,
                     'replaced another wording' if pest_status == 'replaced'
                     else 'was not mentioned',
                     spec.PESTICIDE_EMISSION_SENTENCE,
                     f'pesticide-emission sentence {pest_status}', apply_fixes,
                     'WARNING' if apply_fixes else 'BLOCKING',
                     'This pipeline calculates pesticide emissions with OLCA-Pest, '
                     'so the statement is the same for every dataset and is '
                     'stated the same way. Only sentences about pesticide '
                     'EMISSIONS are touched.',
                     '28-59 (Checks)')

        # The standard opening of includedActivitiesStart.  "begins with" is
        # rewritten to "starts with"; any other opening gets the standard one
        # -- the provider's own "begins with ..." sentence moved to the front,
        # or one built from the exchanges -- with every other sentence kept.
        if column == spec.INCLUDED_START_COLUMN:
            start_status, _canon, opening = spec.included_start_status(tidy_text(found))
            if start_status == 'reformat':
                note(column, opening, spec.INCLUDED_START_PREFIX + '...',
                     'includedActivitiesStart opening not standard', apply_fixes,
                     'WARNING' if apply_fixes else 'BLOCKING',
                     'Standard opening: "This activity starts with ..." (or '
                     '"... starts after ..."), naming what prepares this cultivation.',
                     '28-59 (Checks)')
            elif start_status == 'nonstandard':
                built = build_included_start(row, exchanges, name_facts(row, families))
                standard = spec.standard_included_start(
                    canonical_text, spec.split_sentences(built)[0] if built else '')
                note(column, canonical_text, standard,
                     'includedActivitiesStart opening not standard', apply_fixes,
                     'WARNING' if apply_fixes else 'BLOCKING',
                     'Must open "This activity starts with ..." or "... starts after ...". '
                     'The sentence saying where the activity starts is moved to the front in '
                     'that form (or, with none, one built from the exchanges is put first); '
                     'every other sentence is kept. Set apply = no in Corrections to keep '
                     'the original.',
                     '28-59 (Checks)', standard)
                if apply_fixes:
                    _set_cell_value(row, column, standard)
                    canonical_text = standard

        if not templates:
            continue

        matched, _ = match_template(canonical_text, templates)
        if not matched:
            # Same treatment as the yield line: where the canonical text can
            # be built, offer it instead of only pointing at the difference.
            proposal = propose_text(column, row, families, family)
            note(column, found, describe_divergence(canonical_text, templates),
                 'does not match the house wording', False,
                 'ACCEPTABLE_FOR_NOW' if proposal else 'WARNING',
                 'The house wording for this crop family is proposed -- check it '
                 'before taking it, since your text may say more than the template.'
                 if proposal else
                 'No canonical text can be built for this column, so this one is '
                 'for you to reword.',
                 '28-59 (Checks)', proposal)

    # 7. allocation factors stated vs computed -------------------------------
    # The review skill: the factor an allocationComment states must be the one
    # the dataset's own prices and amounts give.  Which side is wrong (the
    # prose or the prices) is not knowable here, so a mismatch is reported,
    # never rewritten.
    stated = row.get('allocationComment') if 'allocationComment' in row.index else None
    if not _blank(stated):
        shares, _reason = economic_allocation(original, exchanges, properties)
        mismatch = stated_factors_mismatch(stated, shares) if shares else ''
        if mismatch:
            note('allocationComment', stated,
                 ', '.join(f'{n} {f:.3f}' for _, n, f, _ in shares),
                 'stated allocation factor differs from the computed one', False,
                 'ACCEPTABLE_FOR_NOW',
                 f'The comment {mismatch}. Correct the comment, or the price '
                 f'properties if they are the wrong side.', '42 (Checks)')

    # 8. the fields against each other --------------------------------------
    meta_facts.record(dataset, row, exchanges)
    for column, found_value, expected, kind, severity, detail, checklist in \
            meta_facts.cross_field_checks(row, exchanges, families):
        note(column, found_value, expected, kind, False, severity, detail, checklist)

    # ── The uniform nomenclature / metadata / contamination checks ───────────
    # Ported from the .spold review skill; these run on EVERY dataset so the
    # two categories can never be silently skipped.  They only ever report --
    # nothing here is auto-fixed, because each is a judgement call.
    own_crop = name_facts(row, families).get('crop')
    for hotspot in checks.check_meta_row(row):
        # The yield is handled above, where it is also corrected, so drop the
        # checks module's own yield findings rather than reporting the same
        # thing twice.  Matched on the issue STARTING with "Yield": a
        # substring test for "ield" also matches "Metadata field empty",
        # which silently dropped that finding for generalComment.
        if (hotspot['exchange_name'] == 'generalComment'
                and hotspot['issue'].lower().startswith('yield')):
            continue

        # Cross-contamination is the one BLOCKING class the model CAN suggest
        # a correction for: swapping the other crop's name for this dataset's
        # own is mechanical.  It is proposed, never assumed -- the suggestion
        # goes to the corrections sheet with apply=no, for you to approve.
        hotspot_column = hotspot['exchange_name']
        proposal = ''
        options = []

        # A nomenclature call: offer the candidate names rather than only
        # saying what is missing.  The finding then targets the activityName
        # cell itself, so an accepted option renames the dataset everywhere.
        if (hotspot_column == 'ActivityName'
                and hotspot['issue'].startswith('Nomenclature format')):
            options = name_options(row, exchanges)
            if options:
                hotspot_column = 'activityName'

        # An empty metadata field the spec can fill: generate the text and
        # offer it, rather than only reporting that it is missing.  Columns the
        # template loop above already reported are skipped here -- an empty
        # column noticed by both checks should still be ONE row to act on.
        if hotspot['issue'] == 'Metadata field empty':
            meta_column = checks.META_FIELD_MAP.get(hotspot_column, hotspot_column)
            if meta_column in spec.TEMPLATE_COLUMNS:
                continue
            built = propose_text(meta_column, row, families, family)
            if built:
                proposal = built
                hotspot_column = meta_column
                # Built from facts the dataset states -- how its data were
                # obtained, which sources -- or the provider's facts sheet:
                # not a guess, so it is filled in like the other built fields.
                # The Facts sheet of the review says where each fact came from.
                facts = meta_facts.facts_for(row, exchanges)
                if (apply_fixes and meta_column == 'modellingAndValidation_samplingProcedure'
                        and meta_column in row.index and 'data_source' in facts):
                    _set_cell_value(row, meta_column, built)
                    origin = '; '.join(f'{fact} from {where}'
                                       for fact, (_v, where) in sorted(facts.items()))
                    findings.append(Finding(
                        dataset, original, meta_column, '(empty)', built,
                        'empty -- value filled in', True, 'WARNING',
                        f'Built from the dataset\'s facts ({origin}).',
                        '56-58 (Checks)', project, built))
                    continue

        if hotspot['issue'].startswith('Cross-contamination') and own_crop:
            # The checks report the field by its short name ('extrapolations');
            # the meta sheet spells it in full. Without the mapping the column
            # was never found and these rows were offered nothing at all.
            meta_column = checks.META_FIELD_MAP.get(hotspot_column, hotspot_column)
            wrong_crop = checks.contaminating_crop(hotspot)
            if wrong_crop and meta_column in row.index:
                suggestion = spec.replace_crop_word(row[meta_column],
                                                    wrong_crop, own_crop)
                if suggestion != str(row[meta_column]):
                    proposal = suggestion
                    hotspot_column = meta_column

        findings.append(Finding(
            dataset, original, hotspot_column,
            hotspot.get('actual', ''), hotspot.get('expected', ''),
            hotspot['issue'], False, hotspot['severity'],
            hotspot.get('note', ''), hotspot.get('checklist_row', ''), project,
            proposal, options))

    return row, findings, renamed_to


# ── Proposed corrections: review before anything is saved ───────────────────
# The model never writes a dataset on the strength of its own judgement alone.
# A run PROPOSES every correction into the review workbook's Corrections sheet
# -- one row per changed cell, with the text before and after.  You set
# `apply` to no on anything you disagree with; --commit (or the pipeline)
# saves only what is still marked yes, logged in Changes Applied.

NO_ANSWERS = ('no', 'n', 'false', '0', 'skip', 'reject')

# Typed into your_fix, these mean "use the proposed_fix on this row".
ACCEPT_ANSWERS = ('yes', 'y', 'ok', 'accept', 'approved', 'use proposed', 'proposed')


def meta_output_dir(project: str, create: bool = True) -> str:
    """
    Where the model's own outputs go: the project's own folder, output/<project>/,
    beside its datasets -- the review workbook and its verdict.  With no
    project, output/ itself, where the decisions shared by every project live.
    """
    path = os.path.join(META_OUTPUT_DIR, project) if project else META_OUTPUT_DIR
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def correction_key(project: str, dataset: str, column: str) -> str:
    """One approval per changed cell, not per finding: several rules can touch
    the same cell (a yield fix and a whitespace fix both land in
    generalComment), and what a reviewer judges is the resulting text."""
    return f'{project}||{dataset}|{column}'


def answered_columns(applied: dict) -> set:
    """
    The meta columns changed by YOUR typed answers, not by the model.

    Those changes are not corrections awaiting approval -- you already made
    the call -- so they must be kept out of the approve/revert machinery.
    Left in, a key absent from the approved set reverted the very answer that
    had just been applied.
    """
    columns = set()
    for key in applied:
        parts = key.split('|')
        if len(parts) >= 2:
            columns.add(parts[-2])
    return columns


def diff_corrections(original: dict, corrected: dict, dataset: str,
                     findings: list, project: str = '',
                     skip_columns=()) -> list:
    """Every meta cell the model would change, with what caused each change."""
    before_meta = original.get('meta')
    after_meta  = corrected.get('meta')
    if before_meta is None or after_meta is None or before_meta.empty:
        return []

    reasons = collections.defaultdict(list)
    for finding in findings:
        if finding.fixed:
            reasons[finding.column].append(finding.kind)

    rows = []
    for column in after_meta.columns:
        if column not in before_meta.columns or column in skip_columns:
            continue
        old_value = before_meta.iloc[0].get(column)
        new_value = after_meta.iloc[0].get(column)
        if _blank(old_value) and _blank(new_value):
            continue
        if str(old_value) == str(new_value):
            continue
        causes = list(dict.fromkeys(reasons.get(column, [])))
        rows.append({
            'key':      correction_key(project, dataset, column),
            'project':  project,
            'dataset':  dataset,
            'column':   column,
            # Minor only if nothing substantive also touched this cell: a
            # generalComment fixed for whitespace AND a wrong yield still
            # needs a decision.
            'minor':    bool(causes) and all(is_minor(cause) for cause in causes),
            'issues':   '; '.join(causes) or 'normalisation',
            'before':   '' if _blank(old_value) else str(old_value),
            'after':    '' if _blank(new_value) else str(new_value),
            'apply':    'yes',
        })
    return rows


def load_approvals(project: str) -> dict:
    """Read the reviewer's yes/no decisions: {key: bool} -- from the review
    workbook, where one `apply` covers the same change in every dataset."""
    import review_workbook
    return review_workbook.load_approvals(project)


def revert_unapproved(original: dict, corrected: dict, corrections: list,
                      approvals: dict) -> list:
    """
    Undo the corrections the reviewer said no to, and report what remains.

    An activityName that is put back must be put back everywhere it was
    propagated, or the dataset ends up named one thing in `meta` and another
    in `exchanges`.
    """
    kept = []
    for correction in corrections:
        # Minor tidy-ups are accepted outright -- they are never offered for
        # review, so there is no decision to honour.  For everything else the
        # row carries its own default: 'yes' for an ordinary correction, 'no'
        # for a suggestion against a BLOCKING finding.
        default = str(correction.get('apply', 'yes')).strip().lower() not in NO_ANSWERS
        if correction.get('minor') or approvals.get(correction['key'], default):
            kept.append(correction)
            continue
        column = correction['column']
        _set_cell(corrected['meta'], column, original['meta'].iloc[0].get(column))
        if column == 'activityName':
            rename_activity(corrected, correction['after'], correction['before'])
    return kept


# ── What needs a human, and their answers ────────────────────────────────────
# A report you can only read does not get acted on.  Every finding the model
# will not settle itself is a row of the review workbook's Action_list; you
# type the correction in `your_fix`; the next run applies it.  Typed answers
# are preserved across runs -- the sheet is rebuilt around them, never over
# them.

# Findings a human has to resolve.  Everything else the model repairs itself.
NEEDS_HUMAN = ('BLOCKING', 'ACCEPTABLE_FOR_NOW')

# ── Remembered decisions ─────────────────────────────────────────────────────
# One project can hold hundreds of datasets that repeat the exact same
# judgement call -- the same mis-typed flow name, the same off-template
# sentence copied into several activities. Answering it once here and reusing
# the answer everywhere it recurs is both less work and MORE consistent than
# a human re-deciding it dataset by dataset, which can drift. Project-
# independent by design: the same wrong flow name means the same fix
# regardless of which project's dataset carries it.
DECISIONS_FILENAME = 'meta_model_decisions.json'

_PLACEHOLDER_RE = re.compile(r'^\(.*\)$')


def is_cacheable_value(found) -> bool:
    """
    Whether `found` is real, dataset-specific content worth remembering, as
    opposed to a generic placeholder such as "(empty)" or "(no yield line)".
    Two datasets can show the same placeholder while needing entirely
    different values (two different missing yields), so a placeholder is
    never a safe reuse key -- only literal, repeated content is.
    """
    text = str(found or '').strip()
    return bool(text) and not _PLACEHOLDER_RE.match(text)


# Only issues whose right answer depends on the flagged value ALONE are ever
# reused.  A yield, a date or a period answer depends on other cells of the
# dataset -- two datasets can carry the same unfilled "Yield: [VALORE] kg/ha"
# and need different figures -- so reusing one would copy dataset A's number
# into dataset B.  An undeclared custom flow is not fixed by renaming it,
# which is what a reused exchange-name answer does.
REUSABLE_KINDS = frozenset({
    'exchange name is not an ecoinvent elementary flow',
    'exchange name differs from the master data in case',
    'exchange name uses a qualifier ecoinvent does not have',
    'exchange name is in SimaPro format, not ecoinvent',
    'exchange name is not an ecoinvent product',
    'exchange is in the wrong group',
    # Keyed on the flow AND the pair it sits in (see placement_column), and
    # applied as a move of exactly those rows -- never as a rename.
    'exchange is in a compartment the flow does not have',
    'agricultural air emission not in non-urban air',
    'product name entered as a resource',
    'does not match the house wording',
    'includedActivitiesStart opening not standard',
    # Keyed on the whole name: the same name gets the same answer, including
    # "keep" for an activity that is not a production.
    PRODUCTION_NAME_KIND,
})


def decision_signature(column: str, kind: str, found) -> str | None:
    """
    A key identifying one recurring issue across datasets, or None when the
    issue is not safe to reuse (see REUSABLE_KINDS and is_cacheable_value).
    """
    if kind not in REUSABLE_KINDS or not is_cacheable_value(found):
        return None
    return f'{column}||{kind}||{str(found).strip()}'


def decisions_path() -> str:
    # config/review/: team knowledge shared by every project, kept with the
    # other reference files rather than among the outputs.
    from config_new_datasets import DECISIONS_FILE
    return DECISIONS_FILE


def load_decisions() -> dict:
    """Every remembered decision, keyed by decision_signature()."""
    path = decisions_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def remember_decision(column: str, kind: str, found, value: str,
                      project: str, dataset: str) -> None:
    """
    Save one human answer so every OTHER dataset with the exact same issue
    reuses it automatically instead of asking again. Does nothing for a
    finding decision_signature() refuses to cache.
    """
    signature = decision_signature(column, kind, found)
    value = str(value or '').strip()
    if not signature or not value:
        return
    decisions = load_decisions()
    decisions[signature] = {
        'value':      value,
        'column':     column,
        'kind':       kind,
        'found':      str(found),
        'first_seen': f'{project}/{dataset}',
        'when':       datetime.now().isoformat(timespec='seconds'),
    }
    path = decisions_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(decisions, fh, indent=2, sort_keys=True, ensure_ascii=False)

# ── Minor tidy-ups: accepted, not reported ───────────────────────────────────
# Whitespace and a missing final stop carry no meaning and there is nothing to
# decide about them, so putting them in a review queue only buries the
# findings that do need a decision.  They are applied silently, kept out of
# the corrections sheet and out of the report, and counted in one console
# line so they are not invisible.
MINOR = 'MINOR'
MINOR_ISSUES = ('whitespace', 'missing final stop')


def is_minor(kind: str) -> bool:
    """True when every part of a finding's reason is a cosmetic tidy-up."""
    parts = [part.strip() for part in re.split(r'[;+]', str(kind or '')) if part.strip()]
    return bool(parts) and all(part in MINOR_ISSUES for part in parts)


def split_key_rest(rest: str) -> tuple:
    """
    (column, kind) from what follows the dataset in a finding/correction key.

    Split on the LAST '|': a SimaPro flow name ("Urea {CN}| urea production |
    Cut-off, S") carries '|' itself, while an issue kind never does.  A
    correction key has no kind at all, just the (meta) column.
    """
    if '|' not in rest:
        return rest, ''
    column, _, kind = rest.rpartition('|')
    return column, kind


# Between the dataset and its activity in a finding key.  A workbook may
# hold several activities, each with its own name, period and comments, so
# an answer has to say which one it is for.
ACTIVITY_SEP = ' @@ '


def finding_key(finding) -> str:
    """A stable id for one finding, so a typed answer survives re-runs.  The
    project is part of it: one universal sheet covers every project, and two
    projects may hold a dataset of the same name.  So is the activity: in a
    workbook of several, the same issue on two activities is two answers."""
    dataset = finding.dataset + (ACTIVITY_SEP + str(finding.activity).strip()
                                 if str(finding.activity or '').strip() else '')
    return f'{finding.project}||{dataset}|{finding.column}|{finding.kind}'


def merge_text_edit(original: str, answer: str, current: str) -> str:
    """
    `answer` is `original` edited; apply that same edit to `current`, which an
    earlier answer may already have changed.  Each changed stretch is carried
    over where its original wording still stands, exactly once, in `current`;
    if any cannot be placed, the answer is taken as it is (the later answer
    wins, as before).
    """
    import difflib
    if current == original or not isinstance(current, str):
        return answer
    a, b = re.findall(r'\S+|\s+', original), re.findall(r'\S+|\s+', answer)
    result = current
    for tag, i1, i2, j1, j2 in reversed(difflib.SequenceMatcher(None, a, b, autojunk=False)
                                        .get_opcodes()):
        if tag == 'equal':
            continue
        old, new = ''.join(a[i1:i2]), ''.join(b[j1:j2])
        # anchor the change on its neighbouring words, so a short word is found
        left, right = ''.join(a[max(0, i1 - 2):i1]), ''.join(a[i2:i2 + 2])
        needle = left + old + right
        if not needle.strip() or result.count(needle) != 1:
            return answer
        result = result.replace(needle, left + new + right)
    return result


def merge_text_answer(meta_before, meta_now, column: str, activity: str, value):
    """The answer for a meta text cell, merged onto what earlier answers made
    of it (see merge_text_edit).  Anything that is not plain text -- a name, a
    directive, a pick -- is returned untouched."""
    if (meta_before is None or meta_now is None or column not in meta_now.columns
            or column == 'activityName' or not isinstance(value, str)
            or _DATES_DIRECTIVE_RE.match(value)):
        return value
    rows = (meta_now.index[meta_now['activityName'].astype(str).str.strip() == activity]
            if activity and 'activityName' in meta_now.columns else meta_now.index[:1])
    if not len(rows) or rows[0] not in meta_before.index:
        return value
    original, current = meta_before.at[rows[0], column], meta_now.at[rows[0], column]
    if not isinstance(original, str) or not isinstance(current, str):
        return value
    return merge_text_edit(original, value, current)


def split_key_dataset(part: str) -> tuple:
    """(dataset, activity) from the dataset part of a key; the activity is ''
    in a key written before keys named it (the first activity is meant)."""
    dataset, _, activity = str(part).partition(ACTIVITY_SEP)
    return dataset, activity.strip()


def how_to_fix(finding) -> str:
    """What to type in `your_fix` for this kind of finding."""
    if finding.options:
        return (f'{len(finding.options)} option(s) are listed in `choices` -- type the '
                f'number of the one you want, or your own text to use that instead.')
    if finding.proposal and '«' in str(finding.proposal):
        return ('Copy proposed_fix into your_fix and replace each «…» placeholder '
                'with the real value. An answer still carrying one is not applied.')
    if finding.proposal:
        return ('A correction is proposed in proposed_fix -- type yes to take it, '
                'or type your own text to use that instead.')
    column = finding.column
    if finding.kind == 'price missing':
        return ('Type the price per unit of the product: a number in EUR2005 (0.63), or with '
                'its currency and year (0.70 USD2022) -- converted to EUR2005 for you.')
    if finding.kind == 'mandatory properties missing':
        return ('Type the missing properties as "property = value", separated by ";", e.g. '
                'dry mass = 1; wet mass = 6.667; water in wet mass = 5.667; water content = '
                '5.667; carbon content, fossil = 0; carbon content, non-fossil = 0.45')
    if 'yield' in finding.kind.lower():
        return ('Type the yield in kg/ha (just the number, e.g. 8604,16 or 8604.16). '
                'It is written into generalComment in the locked format.')
    if column == 'activityName':
        return ('Type the full corrected activityName. It is renamed here and in '
                'every sheet that carries it (exchanges, properties, review, parameters).')
    if column in spec.TEMPLATE_COLUMNS:
        return f'Type the corrected full text for {column}. It replaces the current value.'
    if column in ('ActivityName', 'Geography', '(metadata, various fields)'):
        return 'Review only -- fix at the source, or correct the named column directly.'
    return f'Type the corrected value for {column}. It replaces the current value.'


def render_choices(finding) -> str:
    """The options as a numbered list, one per line, for the fix sheet.

    Typing the number in `your_fix` is the same as picking the option in the
    review window, so the sheet and the UI offer exactly the same choices.
    """
    return '\n'.join(f'{index} = {value}   [{label}]'
                     for index, (label, value) in enumerate(finding.options, 1))


_CHOICE_LINE_RE = re.compile(r'^\s*(\d+)\s*=\s*(.*?)\s*(?:\[[^\]]*\])?\s*$')


def parse_choices(text) -> dict:
    """{number: value} read back from a rendered choices cell."""
    picks = {}
    for line in str(text or '').splitlines():
        match = _CHOICE_LINE_RE.match(line)
        if match and match.group(2):
            picks[match.group(1)] = match.group(2)
    return picks


def load_fixes(project: str) -> dict:
    """Read the typed answers: {key: your_fix} -- from the project's review
    workbook, where one `your_fix` answers the same error in every dataset on
    its row."""
    import review_workbook
    return review_workbook.load_answers(project)


def rename_activity(sheets: dict, old: str, new: str) -> int:
    """
    Rename an activity in every sheet that names it, and return how many
    cells changed.  Matched as a NAME, not as a string: a copy in exchanges
    that differs from meta only in case or spacing ('Alfalfa-grass silage ')
    is the same activity and must follow, or the dataset ends up named one
    thing in meta and another in its exchanges.
    """
    changed = 0
    for sheet_name in spec.RENAME_SHEETS:
        sheet = sheets.get(sheet_name)
        if sheet is None or sheet.empty:
            continue
        for column in spec.RENAME_COLUMNS:
            if column not in sheet.columns:
                continue
            same = sheet[column].map(lambda v: isinstance(v, str)
                                     and spec.same_activity_name(v, old) and v != new)
            if same.any():
                sheet[column] = sheet[column].astype(object)
                sheet.loc[same, column] = new
                changed += int(same.sum())
    return changed


def _set_cell(frame, column, value, index=None) -> None:
    """
    Write one cell, whatever the column's dtype.

    A column empty in every dataset comes back from Excel as float64, and
    pandas refuses to put text in it ("Invalid value ... for dtype
    'float64'").  Every column the model generates text for -- an empty
    samplingProcedure above all -- is exactly that case, so the column is
    widened to object first.
    """
    if frame[column].dtype != object:
        frame[column] = frame[column].astype(object)
    frame.loc[frame.index[0] if index is None else index, column] = value


_DATES_DIRECTIVE_RE = re.compile(
    r'^\s*dates\s*:\s*((?:19|20)\d\d)\s*(?:-|to|–)\s*((?:19|20)\d\d)\s*$', re.I)


def _first_text(frame, column) -> str:
    """The first non-blank value of a column, as text ('' when there is none)."""
    if column not in frame.columns:
        return ''
    for value in frame[column]:
        if not _blank(value):
            return str(value).strip()
    return ''


# Findings answered with a property VALUE (skill_checks), not a flow name.
PROPERTY_ANSWER_KINDS = ('price missing', 'mandatory properties missing')
# The unit each mandatory property is given in when the answer names none.
PROPERTY_UNITS = {'wet mass': 'kg', 'dry mass': 'kg', 'water in wet mass': 'kg',
                  'water content': 'dimensionless', 'carbon content, fossil': 'dimensionless',
                  'carbon content, non-fossil': 'dimensionless'}
_PROPERTY_PAIR_RE = re.compile(r'^\s*([a-z][a-z ,\-]*?)\s*[=:]\s*([-+]?[\d.,]+(?:[eE][-+]?\d+)?)'
                               r'\s*([A-Za-z]+\d*)?\s*$')
_PRICE_ANSWER_RE = re.compile(r'^\s*([-+]?[\d.,]+(?:[eE][-+]?\d+)?)\s*([A-Za-z]{3}\s*\d{4})?\s*$')


def set_property(sheets: dict, flow: str, prop: str, amount: float, unit: str,
                 comment: str) -> int:
    """Give every exchange row of `flow` the property: the existing row's
    value replaced, or a row added.  Returns the rows written."""
    from config_new_datasets import PROPERTIES_COLS
    exchanges = sheets.get('exchanges')
    if exchanges is None or exchanges.empty:
        return 0
    properties = sheets.get('properties')
    if properties is None or properties.empty:
        properties = pd.DataFrame(columns=PROPERTIES_COLS)
    for column in ('amount', 'unitName', 'comment'):
        if column in properties.columns and properties[column].dtype != object:
            properties[column] = properties[column].astype(object)
    written, added = 0, []
    owners = exchanges[exchanges['name'].astype(str).str.strip() == flow]
    for _, row in owners.drop_duplicates(subset=[c for c in ('activityName', 'group')
                                                 if c in owners.columns]).iterrows():
        same = ((_text_series(properties['name']) == flow)
                & (_text_series(properties['propertyName']).str.lower() == prop)
                & (_text_series(properties['group']) == _first_text(owners.loc[[row.name]], 'group')))
        if same.any():
            properties.loc[same, ['amount', 'unitName', 'comment']] = [amount, unit, comment]
        else:
            new = {c: None for c in PROPERTIES_COLS}
            new.update({c: row.get(c) for c in ('activityName', 'geography', 'group', 'name',
                                                 'compartment', 'subcompartment',
                                                 'activityLink_activityName',
                                                 'activityLink_geography')})
            new.update({'propertyName': prop, 'amount': amount, 'unitName': unit,
                        'comment': comment})
            added.append(new)
        written += 1
    sheets['properties'] = (pd.concat([properties, pd.DataFrame(added)], ignore_index=True)
                            if added else properties)
    return written


def answer_properties(sheets: dict, flow: str, kind: str, value) -> tuple:
    """A typed price ('0.63', '0.70 USD2022') or properties ('dry mass = 1;
    wet mass = 6.667') for one flow.  A price in another currency is
    converted to EUR2005 by the price step that follows."""
    text = str(value).strip()
    if kind == 'price missing':
        match = _PRICE_ANSWER_RE.match(text)
        if not match:
            return False, f'{text!r}: type the price, e.g. 0.63 (EUR2005) or 0.70 USD2022'
        amount = spec.parse_number_loose(match.group(1))
        unit = re.sub(r'\s+', '', match.group(2) or PRICE_TARGET_UNIT).upper()
        rows = set_property(sheets, flow, 'price', amount, unit, 'Price given in the review.')
        return (rows > 0, f'price of {flow!r} set to {amount:g} {unit}' if rows
                else f'no exchange is named {flow!r} any more')
    pairs = [p for p in re.split(r'[;\n]', text) if p.strip()]
    parsed = []
    for pair in pairs:
        match = _PROPERTY_PAIR_RE.match(pair.lower())
        prop = match.group(1).strip() if match else ''
        if not match or prop not in PROPERTY_UNITS:
            return False, (f'{pair.strip()!r}: type one "property = value" per line or '
                           f'separated by ";", e.g. dry mass = 1; wet mass = 6.667')
        parsed.append((prop, spec.parse_number_loose(match.group(2)),
                       match.group(3) or PROPERTY_UNITS[prop]))
    for prop, amount, unit in parsed:
        if not set_property(sheets, flow, prop, amount, unit, 'Value given in the review.'):
            return False, f'no exchange is named {flow!r} any more'
    return True, f'{flow!r}: ' + '; '.join(f'{p} = {a:g} {u}' for p, a, u in parsed)


def apply_fix(sheets: dict, finding_column: str, kind: str, value: str,
              activity: str = '') -> tuple:
    """
    Apply one typed answer to a dataset's sheets.

    Returns (applied, description).  Renames propagate to every sheet, exactly
    as an automatic rename does -- the name is the dataset's identity.
    """
    meta = sheets.get('meta')
    if meta is None or meta.empty:
        return False, 'no meta sheet'
    # The meta row the answer is for: its activity's, in a workbook of several.
    row = meta.index[0]
    if activity and 'activityName' in meta.columns:
        rows = meta.index[meta['activityName'].astype(str).str.strip() == activity]
        if len(rows):
            row = rows[0]
        else:
            return False, f'activity {activity!r} not found in the meta sheet'

    # An answer of "dates: 2015 - 2020" moves the period itself.  It is how a
    # timePeriodComment finding can be settled by correcting the DATES
    # instead of the prose -- one answer cell, either repair.
    directive = _DATES_DIRECTIVE_RE.match(str(value))
    if directive:
        first, last = directive.group(1), directive.group(2)
        for column, year in (('startDate', first), ('endDate', last)):
            if column in meta.columns:
                _set_cell(meta, column, spec.set_year(
                    meta.loc[row, column], int(year)), row)
        return True, f'period set to {first} - {last}'

    if '«' in str(value):
        return False, 'replace the «…» placeholders with the real values first'

    if finding_column == ZERO_AMOUNT_COLUMN and kind == ZERO_AMOUNT_KIND:
        answer = str(value).strip().lower()
        if answer in _ZERO_DROP_ANSWERS:
            names = remove_zero_exchanges(sheets)
            return True, (f'dropped {len(names)} zero-amount row(s). ' + deleted_note(names)
                          if names else 'no zero-amount exchanges left')
        if answer in _ZERO_KEEP_ANSWERS:
            return True, 'zero-amount exchanges kept'
        return False, f'{value!r}: answer drop or keep'

    if 'yield' in kind.lower():
        if spec.format_yield(value) is None:
            return False, f'{value!r} is not a number'
        _set_cell(meta, 'generalComment', spec.set_yield_line(
            meta.loc[row, 'generalComment'], value), row)
        return True, f'yield set to {spec.format_yield(value)} kg/ha'

    if finding_column == 'activityName':
        old = str(meta.loc[row, 'activityName'])
        segments = spec.canonical_segments(value)
        new = spec.join_segments(segments)
        if not new:
            return False, 'empty name'
        # A name is at least a product and what is done with it ("grass
        # production"), with or without axes.  A single word -- above all a
        # bare number, which is what an unresolved option pick looks like --
        # is a typo, not a rename, and must never reach the sheets.
        if len(new.split()) < 2 or re.fullmatch(r'[\d.,]+', new):
            return False, f'{value!r} is not an activityName'
        if new == old:
            return True, f'name kept as {new!r}'
        rename_activity(sheets, old, new)
        return True, f'renamed to {new!r}'

    # Where a correctly named flow sits: move exactly the rows of that name in
    # that compartment/subcompartment -- checked against the master data, so a
    # typed answer cannot put the flow somewhere it does not exist.
    if finding_column.startswith(PLACEMENT_PREFIX):
        name, _, pair = finding_column[len(PLACEMENT_PREFIX):].rpartition(_PLACEMENT_SEP)
        old_c, old_s = parse_placement(pair)
        new_c, new_s = parse_placement(value)
        exchanges = sheets.get('exchanges')
        if exchanges is None or exchanges.empty or not {'name', 'compartment',
                                                        'subcompartment'} <= set(exchanges.columns):
            return False, 'no exchanges sheet with compartments'
        allowed = master_data_names()['elementary'].get(name, set())
        if allowed and (new_c, new_s) not in allowed:
            return False, (f'{name!r} does not exist in {placement_value(new_c, new_s)!r}; '
                           f'pick one of: ' + ', '.join(placement_value(c, s)
                                                       for c, s in sorted(allowed)))

        def text(column):
            return exchanges[column].map(lambda v: '' if pd.isna(v) else str(v).strip())

        mask = ((text('name') == name) & (text('compartment') == old_c)
                & (text('subcompartment') == old_s))
        if not mask.any():
            return False, f'no {name!r} row in {placement_value(old_c, old_s)!r} any more'
        moved = pd.to_numeric(exchanges.loc[mask, 'amount'], errors='coerce').sum() \
            if 'amount' in exchanges.columns else 0.0
        # Flagged in the comment of the row that now carries the amount, so
        # the move is visible in the dataset itself, not only in the review.
        flag = (f'Includes {moved:.4g} {_first_text(exchanges.loc[mask], "unitName")} moved from '
                f'{placement_value(old_c, old_s)}: the master data has no {name} there.')
        for column in ('compartment', 'subcompartment', 'comment'):
            if column in exchanges.columns and exchanges[column].dtype != object:
                exchanges[column] = exchanges[column].astype(object)
        target = ((text('name') == name) & (text('compartment') == new_c)
                  & (text('subcompartment') == new_s)
                  & (text('group') == _first_text(exchanges.loc[mask], 'group')
                     if 'group' in exchanges.columns else True))
        if target.any() and 'amount' in exchanges.columns:
            # The flow is already there: the amount joins that row -- a second
            # row of the same flow in the same place would be a duplicate.
            keep = target[target].index[0]
            exchanges.at[keep, 'amount'] = float(pd.to_numeric(
                exchanges.at[keep, 'amount'], errors='coerce') or 0.0) + float(moved)
            if 'comment' in exchanges.columns:
                exchanges.at[keep, 'comment'] = ' '.join(
                    p for p in (_first_text(exchanges.loc[[keep]], 'comment'), flag) if p)
            gone = [describe_row(r) for _, r in exchanges.loc[mask].iterrows()]
            sheets['exchanges'] = exchanges.drop(index=exchanges.index[mask]).reset_index(drop=True)
            return True, (f'{name!r} in {placement_value(old_c, old_s)!r} ({moved:.4g}) added to '
                          f'its row in {placement_value(new_c, new_s)!r}, flagged in its comment. '
                          + deleted_note(f'{g} (amount added to the {placement_value(new_c, new_s)} row)'
                                         for g in gone))
        for column, new in (('compartment', new_c), ('subcompartment', new_s)):
            exchanges.loc[mask, column] = new
        if 'comment' in exchanges.columns:
            exchanges.loc[mask, 'comment'] = exchanges.loc[mask, 'comment'].map(
                lambda c: ' '.join(p for p in ('' if pd.isna(c) else str(c).strip(), flag) if p))
        return True, (f'{name!r} moved from {placement_value(old_c, old_s)!r} to '
                      f'{placement_value(new_c, new_s)!r} ({int(mask.sum())} row(s)), '
                      f'flagged in its comment')

    # A group or compartment value: replace it on every row that carries it.
    for prefix, column in ((GROUP_PREFIX, 'group'), (COMPARTMENT_PREFIX, 'compartment')):
        if finding_column.startswith(prefix):
            old_value = finding_column[len(prefix):]
            exchanges = sheets.get('exchanges')
            if exchanges is None or exchanges.empty or column not in exchanges.columns:
                return False, 'no exchanges sheet'
            allowed = (VALID_GROUPS if column == 'group' else
                       {c for pairs in master_data_names()['elementary'].values()
                        for c, _sub in pairs})
            if str(value).strip() not in allowed:
                return False, f'{value!r} is not a valid {column}'
            mask = exchanges[column].map(
                lambda v: '' if pd.isna(v) else str(v).strip()) == old_value
            if not mask.any():
                return False, f'no exchange has {column} {old_value!r} any more'
            exchanges[column] = exchanges[column].astype(object)
            exchanges.loc[mask, column] = str(value).strip()
            return True, f'{column} {old_value!r} -> {value!r} ({int(mask.sum())} row(s))'

    # A product name under FromEnvironment: "FromTechnosphere" moves the rows
    # to the technosphere (a bought product has no compartment); an
    # elementary flow name keeps them a resource, under that name.
    if finding_column.startswith(RESOURCE_PREFIX):
        old_name = finding_column[len(RESOURCE_PREFIX):]
        exchanges = sheets.get('exchanges')
        if exchanges is None or exchanges.empty or not {'name', 'group'} <= set(exchanges.columns):
            return False, 'no exchanges sheet'
        mask = ((exchanges['name'].astype(str).str.strip() == old_name)
                & (_text_series(exchanges['group']) == 'FromEnvironment'))
        if not mask.any():
            return False, f'no resource is named {old_name!r} any more'
        answer = str(value).strip()
        for column in ('group', 'name', 'compartment', 'subcompartment'):
            if column in exchanges.columns and exchanges[column].dtype != object:
                exchanges[column] = exchanges[column].astype(object)
        if answer == 'FromTechnosphere':
            cleared = [describe_cell(r, column) for _, r in exchanges.loc[mask].iterrows()
                       for column in ('compartment', 'subcompartment')
                       if column in exchanges.columns and not _blank(r.get(column))]
            exchanges.loc[mask, 'group'] = 'FromTechnosphere'
            for column in ('compartment', 'subcompartment'):
                if column in exchanges.columns:
                    exchanges.loc[mask, column] = None
            return True, (f'{old_name!r} moved to FromTechnosphere ({int(mask.sum())} row(s)), '
                          f'a product has no compartment. ' + deleted_note(cleared)
                          if cleared else
                          f'{old_name!r} moved to FromTechnosphere ({int(mask.sum())} row(s))')
        if answer not in master_data_names()['elementary']:
            return False, (f'{answer!r} is neither FromTechnosphere nor an elementary flow '
                           f'of the master data')
        exchanges.loc[mask, 'name'] = answer
        return True, f'{old_name!r} kept as a resource, renamed to {answer!r} ({int(mask.sum())} row(s))'

    # A property of a flow: the answer IS the value -- a price, or the
    # missing properties -- set on the flow's property rows, never its name.
    if finding_column.startswith(EXCHANGE_PREFIX) and kind in PROPERTY_ANSWER_KINDS:
        return answer_properties(sheets, finding_column[len(EXCHANGE_PREFIX):], kind, value)

    # A flow name: rename it wherever it appears in the exchanges.  The name
    # is the link to the master data, so one answer fixes every row of it.
    if finding_column.startswith(EXCHANGE_PREFIX):
        old_name = finding_column[len(EXCHANGE_PREFIX):]
        if re.fullmatch(r'\s*[-+]?[\d.,]+\s*', str(value)):
            return False, f'{value!r} is a number, not a flow name'
        exchanges = sheets.get('exchanges')
        if exchanges is None or exchanges.empty or 'name' not in exchanges.columns:
            return False, 'no exchanges sheet'
        mask = exchanges['name'].astype(str).str.strip() == old_name
        if not mask.any():
            return False, f'no exchange is named {old_name!r} any more'
        if exchanges['name'].dtype != object:
            exchanges['name'] = exchanges['name'].astype(object)
        exchanges.loc[mask, 'name'] = value
        return True, f'{int(mask.sum())} exchange row(s) renamed to {value!r}'

    if finding_column in meta.columns:
        _set_cell(meta, finding_column, value, row)
        return True, f'{finding_column} replaced'

    return False, f'{finding_column} is not a meta column'


# ── Dataset / project processing ─────────────────────────────────────────────

# ── Exchange names against the ecoinvent master data ────────────────────────
# Every exchange has to name a flow that exists.  A name that does not -- a
# spelling ('Pyriproxifen' for 'Pyriproxyfen'), a British ending ('Sulphur'),
# a non-breaking hyphen that looks identical on screen -- links to nothing:
# the converter cannot find an id for it, self-generates one, and the dataset
# reaches ecoEditor referring to a flow nobody else has.
#
# The lookups are the authority the converter itself uses, so a name that
# passes here is a name the .spold can carry a real id for.

EXCHANGE_PREFIX = 'exchange:'          # marks a finding about a flow name

# The groups whose flows are ELEMENTARY (an emission or a resource); the rest
# are intermediate products.
_ELEMENTARY_GROUPS = ('ToEnvironment', 'FromEnvironment')

# A dataset's own products are new by definition -- ecoinvent cannot know them
# yet, and they are self-declared in the .spold -- so they are noted, never
# failed.
_OWN_PRODUCT_GROUPS = ('ReferenceProduct', 'ByProduct')

# Every group spold_converter_new.py knows.  Anything else it silently ships
# as a purchased intermediate input, so a misspelled "FromEnvionrment" water
# row would reach the .spold as something bought rather than a resource.
VALID_GROUPS = ('ReferenceProduct', 'Products', 'ByProduct', 'FromTechnosphere',
                'FromEnvironment', 'ToEnvironment')

GROUP_PREFIX       = 'exchange group:'        # a finding about a group value
COMPARTMENT_PREFIX = 'exchange compartment:'  # a finding about a compartment
# A finding about WHERE a correctly named flow sits: its compartment AND
# subcompartment together, since a subcompartment only means something inside
# its compartment.  The column names the flow and the pair it sits in now, so
# a fix moves exactly those rows and nothing else of the same name.
PLACEMENT_PREFIX   = 'exchange placement:'
# A product name entered as a resource, with a compartment that says
# resource: whether it is bought or taken from nature is your call.
RESOURCE_PREFIX    = 'exchange as resource:'
_PLACEMENT_SEP     = ' :: '
_PAIR_SEP          = ' / '


def placement_value(compartment, subcompartment) -> str:
    return f'{compartment}{_PAIR_SEP}{subcompartment}'


def placement_column(name, compartment, subcompartment) -> str:
    return (PLACEMENT_PREFIX + str(name) + _PLACEMENT_SEP
            + placement_value(compartment, subcompartment))


def parse_placement(text) -> tuple:
    """(compartment, subcompartment) from 'compartment / subcompartment'."""
    compartment, _, subcompartment = str(text).partition(_PAIR_SEP)
    return compartment.strip(), subcompartment.strip()


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def closed_vocabulary_match(value, vocabulary) -> str | None:
    """
    The one vocabulary entry `value` is a misspelling of, or None.

    For a CLOSED list (a group, a compartment) a typo has exactly one right
    reading when a single entry is within a couple of edits and every other is
    clearly further away -- "FromEnvionrment" is FromEnvironment, "ait" is
    air.  A bracketed gloss is set aside first ("natural resource (input
    of)").  Anything less clear-cut is left for a human.
    """
    text = str(value or '').strip()
    if text in vocabulary:
        return text

    def key(s):
        return re.sub(r'\s*\([^)]*\)', '', str(s)).replace(' ', '').lower()

    wanted = key(text)
    if not wanted:
        return None
    exact = [v for v in vocabulary if key(v) == wanted]
    if len(exact) == 1:
        return exact[0]
    # A typo keeps the first letter; a value that lost a whole leading word
    # ("Environment") is not a typo of either FromEnvironment or
    # ToEnvironment, and guessing which would be a modelling call.
    scored = sorted((_edit_distance(wanted, key(v)), v) for v in vocabulary
                    if key(v)[:1] == wanted[:1])
    if not scored:
        return None
    best_distance, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else None
    allowed = max(1, len(wanted) // 5)
    if best_distance <= allowed and (runner_up is None or runner_up > best_distance + 1):
        return best
    return None


# ── Corrections you rejected ────────────────────────────────────────────────
# A correction set to apply = no in the review workbook's Corrections sheet
# stays rejected on every later run: each automatic fix checks its key here
# before it changes anything, and leaves the issue open for you instead.
_REJECTED_FIXES: set = set()


def _fix_rejected(key: str) -> bool:
    return key in _REJECTED_FIXES


def apply_wording_to_comments(sheets: dict, dataset: str, activity: str,
                              project: str, applied: dict) -> list:
    """
    The house wording (meta_spec.WORDING) in the comments of the exchanges,
    properties and parameters too -- one spelling per word, and "by-product"
    never "co-product", everywhere, not only in the meta sheet.  Cosmetic,
    like the meta tidy-ups: applied and logged, not queued for review.
    """
    findings = []
    for sheet_name in ('exchanges', 'properties', 'parameters'):
        frame = sheets.get(sheet_name)
        if frame is None or frame.empty:
            continue
        for column in ('comment', 'uncertainty comment', 'PV_comment', 'PV_uncertainty comment'):
            if column not in frame.columns:
                continue
            key = f'{project}||{dataset}|{sheet_name}.{column}|house wording'
            if _fix_rejected(key):
                continue
            before = frame[column]
            after = before.map(lambda v: spec.apply_house_wording(v)
                               if isinstance(v, str) else v)
            changed = before.map(lambda v: isinstance(v, str)) & (before != after)
            if not changed.any():
                continue
            if frame[column].dtype != object:
                frame[column] = frame[column].astype(object)
            frame.loc[changed, column] = after[changed]
            applied[key] = (f'{int(changed.sum())} comment(s) in the house wording', dataset)
            findings.append(Finding(
                dataset, activity, f'{sheet_name}.{column}', f'{int(changed.sum())} comment(s)',
                'the house wording', 'house wording', True, MINOR,
                'One spelling per word, "by-product" for co-product.', '', project))
    return findings


def clear_production_volume_off_products(sheets: dict, dataset: str, activity: str,
                                        project: str, applied: dict) -> list:
    """
    A production volume belongs to what the activity produces -- its
    reference product and by-products -- and to nothing else.  A PV comment
    or figure on an input or an emission is a leftover (a stray number, the
    template's "leave empty") that the .spold has no place for; emptied.
    """
    exchanges = sheets.get('exchanges')
    if exchanges is None or exchanges.empty or 'group' not in exchanges.columns:
        return []
    columns = [c for c in exchanges.columns if str(c).startswith('PV_')]
    if not columns:
        return []
    off_product = ~_text_series(exchanges['group']).isin(_OWN_PRODUCT_GROUPS)
    findings = []
    for column in columns:
        filled = off_product & exchanges[column].map(lambda v: not _blank(v))
        key = f'{project}||{dataset}|exchanges.{column}|production volume off a product'
        if not filled.any() or _fix_rejected(key):
            continue
        names = sorted({str(n) for n in exchanges.loc[filled, 'name']}) if 'name' in exchanges.columns else []
        cells = [describe_cell(r, column) for _, r in exchanges.loc[filled].iterrows()]
        exchanges[column] = exchanges[column].astype(object)
        exchanges.loc[filled, column] = None
        applied[key] = (f'{int(filled.sum())} cell(s) emptied. ' + deleted_note(cells), dataset)
        findings.append(Finding(
            dataset, activity, f'exchanges.{column}', f'{int(filled.sum())} row(s)', '(empty)',
            'production volume off a product', True, MINOR,
            f'{column} is only for the reference product and by-products; emptied on '
            + ', '.join(names[:6]) + '.', '', project))
    return findings


def normalise_exchange_vocabulary(sheets: dict, dataset: str, activity: str,
                                  project: str, applied: dict) -> list:
    """
    Correct misspelled group and compartment values in the exchanges sheet.

    Returns the findings.  A value with one clear reading is corrected
    outright (and recorded in `applied`, so it is written); one without is
    reported with the allowed values offered to pick from.
    """
    exchanges = sheets.get('exchanges')
    if exchanges is None or exchanges.empty:
        return []
    findings = []
    compartments = sorted({c for pairs in master_data_names()['elementary'].values()
                           for c, _sub in pairs})

    def text_of(series):
        return series.map(lambda v: '' if pd.isna(v) else str(v).strip())

    # A row with no group at all.  Its compartment says what it is -- the
    # reading spold_converter_new itself falls back on -- an emission, or a
    # resource when the compartment is 'natural resource'.  Left blank, the
    # model took the row for a product, the converter for an emission, and
    # the name was never checked as what it is.  Without a compartment there
    # is nothing to read it from, and it is left for you.
    if {'group', 'name'} <= set(exchanges.columns):
        groups, names = text_of(exchanges['group']), text_of(exchanges['name'])
        places = (text_of(exchanges['compartment']) if 'compartment' in exchanges.columns
                  else pd.Series('', index=exchanges.index))
        for name in sorted(set(names[(groups.str.lower().isin(('', 'nan', 'none')))
                                     & (names != '')])):
            mask = groups.str.lower().isin(('', 'nan', 'none')) & (names == name)
            seen = set(places[mask]) - {''}
            resource = {('natural resource' in c.lower()) for c in seen}
            key = f'{project}||{dataset}|{EXCHANGE_PREFIX}{name}|exchange group missing'
            if seen and len(resource) == 1 and not _fix_rejected(key):
                group = 'FromEnvironment' if resource == {True} else 'ToEnvironment'
                exchanges['group'] = exchanges['group'].astype(object)
                exchanges.loc[mask, 'group'] = group
                applied[key] = (f'{name!r}: no group -> {group}, from its compartment '
                                f'{", ".join(sorted(seen))} ({int(mask.sum())} row(s))', dataset)
                findings.append(Finding(
                    dataset, activity, EXCHANGE_PREFIX + name, f'{name} with no group', group,
                    'exchange group missing', True, 'WARNING',
                    f'{int(mask.sum())} row(s) had no group. The compartment '
                    f'({", ".join(sorted(seen))}) makes it an '
                    f'{"input from nature" if group == "FromEnvironment" else "emission"}, '
                    f'so it is entered under {group}.', '18 (agri)', project, group))
            else:
                findings.append(Finding(
                    dataset, activity, EXCHANGE_PREFIX + name, f'{name} with no group',
                    'a group', 'exchange group missing', False, 'BLOCKING',
                    f'{int(mask.sum())} row(s) have no group, and '
                    + ('their compartments disagree on what they are' if seen else
                       'no compartment to tell what they are')
                    + ' -- fill the group in the input dataset.', '18 (agri)', project))

    for column, vocabulary, prefix, rows in (
            ('group', VALID_GROUPS, GROUP_PREFIX, None),
            ('compartment', compartments, COMPARTMENT_PREFIX, 'elementary')):
        if column not in exchanges.columns:
            continue
        # Blank cells mapped to '' explicitly: on newer pandas astype(str)
        # keeps them as NaN, and a float there crashed the whole scan.
        def as_text(series):
            return series.map(lambda v: '' if pd.isna(v) else str(v).strip())

        values = as_text(exchanges[column])
        scope = pd.Series(True, index=exchanges.index)
        if rows == 'elementary' and 'group' in exchanges.columns:
            scope = as_text(exchanges['group']).isin(_ELEMENTARY_GROUPS)
        for bad in sorted(set(values[scope]) - set(vocabulary)):
            if bad.lower() in ('', 'nan', 'none'):
                continue
            mask = scope & (values == bad)
            count = int(mask.sum())
            right = closed_vocabulary_match(bad, vocabulary)
            if right and _fix_rejected(
                    f'{project}||{dataset}|{prefix}{bad}|exchange {column} misspelled'):
                right = None                # you rejected this correction: left for you
            if right:
                exchanges[column] = exchanges[column].astype(object)
                exchanges.loc[mask, column] = right
                applied[f'{project}||{dataset}|{prefix}{bad}|exchange {column} misspelled'] = (
                    f'{column} {bad!r} -> {right!r} ({count} row(s))', dataset)
                findings.append(Finding(
                    dataset, activity, prefix + bad, bad, right,
                    f'exchange {column} misspelled', True, 'WARNING',
                    f'{count} row(s). {bad!r} is not a {column} this pipeline '
                    f'knows; it has one clear reading, {right!r}, so it is '
                    f'corrected outright.', '18 (agri)', project, right))
            else:
                findings.append(Finding(
                    dataset, activity, prefix + bad, bad, ', '.join(vocabulary),
                    f'exchange {column} not recognised', False, 'BLOCKING',
                    f'{count} row(s). {bad!r} is not a {column} this pipeline '
                    f'knows, and it is not clearly a misspelling of one -- pick '
                    f'the right one.', '18 (agri)', project, '',
                    [(v, v) for v in vocabulary]))
    return findings

_MASTER_NAMES: dict | None = None


def master_data_names() -> dict:
    """
    What the master data knows, loaded once:

      intermediate  every product name, plus the custom flows this pipeline
                    declares itself (the diesel and nutrient ones)
      elementary    every emission/resource name -> the compartment pairs it
                    is allowed in
    """
    global _MASTER_NAMES
    if _MASTER_NAMES is not None:
        return _MASTER_NAMES

    import json
    from config_new_datasets import (
        STANDARD_INTERMEDIATE_EXCHANGE_LOOKUP_FILE,
        STANDARD_ELEMENTARY_EXCHANGE_LOOKUP_FILE,
        MASTER_DATA_LOOKUP_FILE)

    def load(path):
        try:
            with open(path, encoding='utf-8') as handle:
                return json.load(handle)
        except Exception:
            return {}

    standard = list(load(STANDARD_INTERMEDIATE_EXCHANGE_LOOKUP_FILE))
    intermediate = standard + list(load(MASTER_DATA_LOOKUP_FILE))

    elementary: dict = {}
    for key in load(STANDARD_ELEMENTARY_EXCHANGE_LOOKUP_FILE):
        parts = str(key).split('|')
        if len(parts) == 3:
            elementary.setdefault(parts[0], set()).add((parts[1], parts[2]))

    _MASTER_NAMES = {
        'intermediate': intermediate,
        'intermediate_lower': {n.strip().lower() for n in intermediate},
        # The converter finds a standard product's id by its EXACT name (the
        # custom flows are looked up case-blind), so this is the spelling a
        # standard product has to carry.
        'intermediate_spelling': {n.strip().lower(): n.strip() for n in standard},
        'elementary': elementary,
        'elementary_lower': {n.strip().lower(): n for n in elementary},
    }
    return _MASTER_NAMES


# A SimaPro export names a flow "Product {GEO}| activity | Cut-off, S".
# ecoinvent's own name is just the product, so a dataset provided that way
# matches nothing until the qualifier is taken off.
_SIMAPRO_NAME = re.compile(r'^\s*(?P<product>[^{|]+?)\s*\{[^}]*\}\s*\|')


def simapro_product(name: str) -> str:
    """The product a SimaPro-style name carries, or '' if it is not one."""
    match = _SIMAPRO_NAME.match(str(name))
    return match.group('product').strip().rstrip(',').strip() if match else ''


def canonical_flow_name(name: str) -> str:
    """
    The name the master data would be keyed under.

    The declared flow is "diesel used in <operation>", which is what the
    diesel calculator writes; older outputs carry a comma after "in", so one
    is dropped for the lookup if present.
    """
    return str(name).replace('diesel used in, ', 'diesel used in ', 1).strip()


# A region baked straight into the name (", CN: Nei Mongol") or a legacy
# v2/SimaPro-hybrid suffix ("at plant/GLO U/AusSD U") defeats both the
# elementary-flow lookup and the fuzzy product match, since neither compares
# against anything but the exact string. Stripped for MATCHING PURPOSES ONLY,
# below, and only ever used where what remains is an EXACT match -- a fuzzy
# guess against a shortened name is not offered, because it can be
# confidently wrong: "Chemicals organic" fuzzy-matches "chemical, inorganic"
# better than "chemical, organic, unspecified", since "inorganic" contains
# "organic" as a substring. A close-but-not-exact match on noise this
# arbitrary is worse than no suggestion at all.
_REGION_SUFFIX_RE = re.compile(r',\s*[A-Za-z]{2,3}:\s*[^,]+$')
_LEGACY_AT_SUFFIX_RE = re.compile(r',?\s+at\s+\S.*$', re.IGNORECASE)


def strip_match_noise(name: str) -> str:
    """`name` with a trailing region qualifier or legacy "at ..." suffix set
    aside; never written anywhere, only compared against the master data."""
    text = str(name or '').strip()
    text = _REGION_SUFFIX_RE.sub('', text)
    text = _LEGACY_AT_SUFFIX_RE.sub('', text)
    return text.strip().rstrip(',').strip()


def qualifier_options(name: str, pool) -> list:
    """
    The flows that share this name's head, when only the qualifier differs.

    'Carbon dioxide, biogenic' is not a misspelling of anything -- the head
    is right and the qualifier is another vocabulary's word for it.  Guessing
    which one is meant is how you end up proposing 'Carbon dioxide, in air',
    an uptake, for an emission.  So the choice is put back to the reader.
    """
    head = str(name).split(',')[0].strip().lower()
    if not head or ',' not in str(name):
        return []
    return sorted({str(p) for p in pool
                   if str(p).split(',')[0].strip().lower() == head
                   and str(p).strip().lower() != str(name).strip().lower()})


# Words that say nothing about WHICH flow it is: they are shared by the
# generic flow ('Water, unspecified natural origin') and its variants.
_GENERIC_QUALIFIER_WORDS = frozenset({
    'unspecified', 'natural', 'origin', 'in', 'use', 'of', 'and', 'the', 'a',
    'ground', 'water', 'air'})


# Qualifiers of other databases (SimaPro, ecoinvent 2) and the ecoinvent 3
# words for the same thing.  Fixed equivalences, not guesses: biogenic carbon
# is ecoinvent's non-fossil carbon, and land transformation carbon is the
# carbon released from soil or biomass stock.
_QUALIFIER_SYNONYMS = {
    'biogenic': 'non fossil',
    'land transformation': 'from soil or biomass stock',
}


def flow_from_evidence(head: str, variants: list, evidence: str) -> tuple:
    """
    Pick one of `variants` (all of which already fit the row's compartment)
    from what the data provider wrote, or ('', '') when that does not settle it.

    The provider's own words decide first: a comment or a name that says
    'well', 'river' or 'cooling' names the flow.  Where nothing points to any
    variant, the generic flow -- the one whose qualifier only says
    'unspecified ...' -- is the right one, because it is the only one that
    claims nothing the provider did not say.  Bare 'Water' as a resource is
    'Water, unspecified natural origin', not water for cooling.  When the
    words point to more than one variant, it stays a decision for a human.
    """
    text = ' ' + re.sub(r'[^a-z0-9]+', ' ', str(evidence).lower()) + ' '
    text = text.replace(' as well ', ' ')          # 'as well as' is not a well
    # Other databases' words for ecoinvent's qualifiers, added alongside.
    for word, ecoinvent_words in _QUALIFIER_SYNONYMS.items():
        if f' {word} ' in text:
            text += f'{ecoinvent_words} '
    head_words = set(re.findall(r'[a-z0-9]+', str(head).lower()))

    def distinctive(flow):
        rest = str(flow).split(',', 1)[1] if ',' in str(flow) else ''
        return {w for w in re.findall(r'[a-z0-9]+', rest.lower())
                if w not in _GENERIC_QUALIFIER_WORDS and w not in head_words}

    def said(word):
        return f' {word} ' in text or f' {word}s ' in text

    words = {flow: distinctive(flow) for flow in variants}
    # A flow is named when ALL its words are there ('non-fossil' needs 'non'
    # as well as 'fossil'); of those, the most specific wins -- 'non fossil'
    # names 'non-fossil', not 'fossil' too.
    named = [flow for flow, ws in words.items() if ws and all(said(w) for w in ws)]
    named = [flow for flow in named
             if not any(words[flow] < words[other] for other in named)]
    if not named:
        # 'ocean water' still names 'Water, salt, ocean' when nothing else
        # is named at all -- but only when it names that one flow alone.
        named = [flow for flow, ws in words.items() if ws and any(said(w) for w in ws)]
    if len(named) == 1:
        return named[0], f'the provider\'s own text names it ({", ".join(sorted(words[named[0]]))})'
    if named:
        return '', ''
    generic = [flow for flow, ws in words.items()
               if not ws and 'unspecified' in str(flow).lower()]
    if len(generic) == 1:
        return generic[0], ('nothing in the name or comment narrows it further, '
                            'so the generic flow, which adds nothing the provider '
                            'did not say')
    return '', ''


def nearest_flow(name: str, pool) -> str:
    """The master-data name this one was probably meant to be, or ''."""
    import difflib
    text = str(name).strip()
    for candidate in pool:                      # a difference of case only
        if str(candidate).strip().lower() == text.lower():
            return str(candidate)
    # 0.75 catches 'Sulphur' -> 'Sulfur' (0.77) without reaching for
    # anything unrelated; every proposal it makes is checked below before it
    # is offered, and a name it cannot place is reported with none.
    close = difflib.get_close_matches(text, [str(p) for p in pool], n=1, cutoff=0.75)
    return close[0] if close else ''


# Spelling variants that are not a different substance: British vs ecoinvent
# spelling, i/y, a trailing e, -ous/-us, singular/plural, and a hyphen typed
# as a look-alike character.  Two names with the same key are the same name
# spelled two ways -- 'Sulphur' / 'Sulfur', 'Pyriproxifen' / 'Pyriproxyfen',
# 'Abamectine' / 'Abamectin', 'Metalaxil-M' / 'Metalaxyl-M', 'Phosphorous' /
# 'Phosphorus', 'pesticide, unspecified' / 'Pesticides, unspecified'.  A key
# shared by two master-data names decides nothing.
_LOOKALIKE_HYPHENS = dict.fromkeys(map(ord, '‐‑‒–—−­'), '-')


def spelling_key(name: str) -> str:
    text = str(name).translate(_LOOKALIKE_HYPHENS).lower()
    text = text.replace('ph', 'f').replace('y', 'i')
    words = []
    for word in re.findall(r'[a-z0-9]+|[^a-z0-9\s]', text):
        if len(word) > 3 and word.isalpha():
            word = re.sub(r'ous$', 'us', word)
            word = re.sub(r'(?<=[a-z]{3})s$', '', word)
            word = re.sub(r'(?<=[a-z]{3})e$', '', word)
        words.append(word)
    return ' '.join(words)


_SPELLING_INDEX: dict = {}


def spelling_variant(name: str, pool, label: str) -> str:
    """The one master-data name that is `name` spelled another way, or ''."""
    index = _SPELLING_INDEX.get(label)
    if index is None:
        index = {}
        for candidate in pool:
            index.setdefault(spelling_key(candidate), set()).add(str(candidate).strip())
        _SPELLING_INDEX[label] = index
    hits = index.get(spelling_key(name), set())
    return next(iter(hits)) if len(hits) == 1 else ''


# Where an elementary flow goes when the master data allows several places
# and the dataset's own placement is missing or not one of them.  These are
# the agricultural conventions of ecoinvent's own crop and livestock
# datasets (review skill): emissions to air from the field go to non-urban
# air, to soil to agricultural soil, to water to surface water -- nitrate
# leaching to ground water.  A convention is only applied where the master
# data has that place for the flow; otherwise the choice is put to a human.
CONVENTIONAL_SUBCOMPARTMENT = {
    'air':   'non-urban air or from high stacks',
    'soil':  'agricultural',
    'water': 'surface water',
}
CONVENTIONAL_SUBCOMPARTMENT_BY_FLOW = {
    ('nitrate', 'water'): 'ground-',
}
# The compartment, for a field emission whose compartment is blank and that
# the master data has in more than one.
CONVENTIONAL_COMPARTMENT_BY_FLOW = {
    'ammonia': 'air', 'dinitrogen monoxide': 'air', 'nitrogen oxides': 'air',
    'methane, non-fossil': 'air', 'methane, fossil': 'air',
    'nmvoc, non-methane volatile organic compounds': 'air',
    'carbon dioxide, fossil': 'air', 'carbon dioxide, non-fossil': 'air',
    'nitrate': 'water', 'phosphate': 'water', 'phosphorus': 'water',
}


def conventional_pair(name: str, compartment: str, allowed) -> tuple | None:
    """
    The (compartment, subcompartment) a flow takes by convention, or None.
    Used only when the stated placement is missing or not in the master data.
    """
    allowed = set(allowed or ())
    compartments = sorted({c for c, _ in allowed})
    if compartment in compartments:
        candidates = [compartment]
    elif not compartment and len(compartments) == 1:
        candidates = compartments          # one compartment: only the sub is open
    elif not compartment and CONVENTIONAL_COMPARTMENT_BY_FLOW.get(
            str(name).strip().lower()) in compartments:
        candidates = [CONVENTIONAL_COMPARTMENT_BY_FLOW[str(name).strip().lower()]]
    else:
        return None                        # the compartment itself is the choice
    for comp in candidates:
        subs = sorted(sub for c, sub in allowed if c == comp)
        if len(subs) == 1:
            return comp, subs[0]
        wanted = (CONVENTIONAL_SUBCOMPARTMENT_BY_FLOW.get((str(name).strip().lower(), comp))
                  or CONVENTIONAL_SUBCOMPARTMENT.get(comp))
        if wanted in subs:
            return comp, wanted
    return None


def fix_elementary_groups(sheets: dict, dataset: str, activity: str,
                          project: str, applied: dict) -> list:
    """
    An elementary flow entered as a bought product ('Water, well, in ground'
    under FromTechnosphere) is moved to where it belongs: FromEnvironment for a
    resource, ToEnvironment for an emission, with the compartment and
    subcompartment filled in from the master data (or the convention).  Only
    an EXACT elementary name that is not also a product is moved -- that is a
    group mistake, not a judgement.
    """
    exchanges = sheets.get('exchanges')
    if exchanges is None or exchanges.empty or not {'name', 'group'} <= set(exchanges.columns):
        return []
    known = master_data_names()
    findings = []
    for column in ('group', 'name', 'compartment', 'subcompartment'):
        if column not in exchanges.columns:
            exchanges[column] = None
        if exchanges[column].dtype != object:
            exchanges[column] = exchanges[column].astype(object)
    # 'Cyprodinil ' is 'Cyprodinil': the master data has no name with a stray
    # space, and the converter looks names up as they are written.
    raw = exchanges['name']
    tidy = raw.map(lambda v: re.sub(r'\s+', ' ', v).strip() if isinstance(v, str) else v)
    spaced = raw.map(lambda v: isinstance(v, str)) & (raw != tidy)
    if spaced.any() and not _fix_rejected(
            f'{project}||{dataset}|exchanges|stray spaces removed from names'):
        exchanges.loc[spaced, 'name'] = tidy[spaced]
        names = sorted({repr(v) for v in raw[spaced]})
        applied[f'{project}||{dataset}|exchanges|stray spaces removed from names'] = (
            f'{int(spaced.sum())} row(s): ' + ', '.join(names[:8]), dataset)
    groups = _text_series(exchanges['group'])
    candidates = exchanges[~groups.isin(_ELEMENTARY_GROUPS + _OWN_PRODUCT_GROUPS)]
    moved: dict = {}
    for idx, row in candidates.iterrows():
        name = str(row['name'] or '').strip()
        if not name or name.lower() == 'nan':
            continue
        if canonical_flow_name(name).lower() in known['intermediate_lower']:
            continue                        # a real product of that name
        stripped = strip_match_noise(name)
        flow = (known['elementary_lower'].get(name.lower())
                or known['elementary_lower'].get(stripped.lower()))
        if not flow:
            continue
        allowed = known['elementary'].get(flow, set())
        compartments = {c for c, _ in allowed}
        group = ('FromEnvironment' if compartments == {'natural resource'}
                 else 'ToEnvironment' if 'natural resource' not in compartments else '')
        if not group:
            continue                        # both a resource and an emission
        if _fix_rejected(f'{project}||{dataset}|{EXCHANGE_PREFIX}{name}|'
                         f'elementary flow moved to its group'):
            continue                        # you rejected this correction
        comp = str(row.get('compartment') or '').strip()
        sub = str(row.get('subcompartment') or '').strip()
        pair = (comp, sub) if (comp, sub) in allowed else \
            (next(iter(allowed)) if len(allowed) == 1 else conventional_pair(flow, comp, allowed))
        exchanges.at[idx, 'group'] = group
        exchanges.at[idx, 'name'] = flow
        if pair:
            exchanges.at[idx, 'compartment'], exchanges.at[idx, 'subcompartment'] = pair
        moved.setdefault((name, flow, group, pair), 0)
        moved[(name, flow, group, pair)] += 1
    # The mirror image: a product entered as a resource taken from nature
    # ('pesticide, unspecified' under FromEnvironment, with its active
    # ingredients in the comment).  An EXACT product name that is no
    # elementary flow is a group mistake, not a judgement, so the row goes to
    # FromTechnosphere and loses the compartment a product does not have.
    # Renamed to the nearest elementary flow instead ('Pesticides,
    # unspecified'), it shipped a resource with no valid compartment and
    # emissions the active ingredients already counted.
    # Only when nothing says resource: a row WITH a compartment ('natural
    # resource / in ground') was placed in nature on purpose, and the product
    # name may be the wrong word for a real resource -- that is a decision,
    # offered with the elementary flows that fit its compartment.
    groups = _text_series(exchanges['group'])
    bought: dict = {}
    cleared: dict = {}                      # name -> the placement cells emptied
    placed: dict = {}
    decisions = load_decisions()
    for idx, row in exchanges[groups == 'FromEnvironment'].iterrows():
        name = str(row['name'] or '').strip()
        if not name or name.lower() == 'nan' or name.lower() in known['elementary_lower']:
            continue
        if canonical_flow_name(name).lower() not in known['intermediate_lower']:
            continue
        comp = '' if _blank(row.get('compartment')) else str(row.get('compartment')).strip()
        sub = '' if _blank(row.get('subcompartment')) else str(row.get('subcompartment')).strip()
        if comp:
            placed.setdefault((name, comp, sub), 0)
            placed[(name, comp, sub)] += 1
            continue
        if _fix_rejected(f'{project}||{dataset}|{EXCHANGE_PREFIX}{name}|product moved to its group'):
            continue                        # you rejected this correction
        if sub:
            cleared.setdefault(name, []).append(describe_cell(row, 'subcompartment'))
        exchanges.at[idx, 'group'] = 'FromTechnosphere'
        exchanges.at[idx, 'compartment'] = None
        exchanges.at[idx, 'subcompartment'] = None
        bought[name] = bought.get(name, 0) + 1
    import difflib
    for (name, comp, sub), count in placed.items():
        column, kind = RESOURCE_PREFIX + name, 'product name entered as a resource'
        found = f'{name} in {placement_value(comp, sub)}'
        fitting = sorted(flow for flow, pairs in known['elementary'].items() if (comp, sub) in pairs)
        near = difflib.get_close_matches(name.lower(), [f.lower() for f in fitting], n=4, cutoff=0.4)
        by_lower = {f.lower(): f for f in fitting}
        options = [('a bought product -> FromTechnosphere', 'FromTechnosphere')] + [
            (f'a resource -> {by_lower[n]}', by_lower[n]) for n in near]
        remembered = (decisions.get(decision_signature(column, kind, found) or '') or {}).get('value')
        key = f'{project}||{dataset}|{column}|{kind}'
        if remembered and not _fix_rejected(key):
            ok, description = apply_fix(sheets, column, kind, remembered)
            if ok:
                applied[key] = (f'{description} (your earlier answer)', dataset)
                findings.append(Finding(
                    dataset, activity, column, found, remembered, kind, True, 'WARNING',
                    f'{count} row(s). Settled as you answered before: {remembered!r}.',
                    '18 (agri)', project, remembered))
                continue
        findings.append(Finding(
            dataset, activity, column, found, 'a bought product, or a resource',
            kind, False, 'ACCEPTABLE_FOR_NOW',
            f'{count} row(s). {name!r} is an ecoinvent product, but it sits under '
            f'FromEnvironment in {placement_value(comp, sub)}: either it is bought '
            f'(FromTechnosphere), or it is a resource taken from nature under an '
            f'elementary flow name. Pick one.', '18 (agri)', project, '', options))
    for name, count in bought.items():
        applied[f'{project}||{dataset}|{EXCHANGE_PREFIX}{name}|product moved to its group'] = (
            f'{name!r}: FromEnvironment -> FromTechnosphere ({count} row(s))'
            + (f'. {deleted_note(cleared[name])}' if cleared.get(name) else ''), dataset)
        findings.append(Finding(
            dataset, activity, EXCHANGE_PREFIX + name, f'{name} as a resource',
            f'{name} (FromTechnosphere)', 'product moved to its group', True, 'WARNING',
            f'{count} row(s). {name!r} is an ecoinvent product, bought from the '
            f'technosphere, not a resource taken from nature -- entered under '
            f'FromTechnosphere.', '18 (agri)', project, name))

    for (name, flow, group, pair), count in moved.items():
        where = placement_value(*pair) if pair else '(placement to be chosen)'
        applied[f'{project}||{dataset}|{EXCHANGE_PREFIX}{name}|elementary flow moved to its group'] = (
            f'{name!r} -> {flow!r} as {group}, {where} ({count} row(s))', dataset)
        findings.append(Finding(
            dataset, activity, EXCHANGE_PREFIX + name, f'{name} as a product', f'{flow} ({group}, {where})',
            'elementary flow moved to its group', True, 'WARNING',
            f'{count} row(s). {flow!r} is an elementary flow, not a product that can be '
            f'bought, so it is entered under {group} in {where}.', '18 (agri)', project, flow))
    return findings


_GEO_CODES: dict | None = None
_GEO_NAMES: dict | None = None
# 'Water, MN: Arhangay', 'Water, CN-NM: Ordos' -- a geography tagged onto a
# flow name by the data provider.
_FLOW_REGION_RE = re.compile(r',\s*([A-Za-z]{2,3})(?:-[A-Za-z0-9]+)?\s*:\s*\S')


def geography_master_data() -> tuple:
    """({shortname lower: shortname}, {name lower: shortname}) from the master data."""
    global _GEO_CODES, _GEO_NAMES
    if _GEO_CODES is None:
        from config_new_datasets import GEOGRAPHY_ID_LOOKUP_FILE, GEOGRAPHY_NAME_LOOKUP_FILE
        codes, names = {}, {}
        for path, target in ((GEOGRAPHY_ID_LOOKUP_FILE, 'codes'),
                             (GEOGRAPHY_NAME_LOOKUP_FILE, 'names')):
            try:
                with open(path, encoding='utf-8') as fh:
                    data = json.load(fh)
            except Exception:
                data = {}
            if target == 'codes':
                codes = {str(k).lower(): str(k) for k in data}
            else:
                names = {str(k).lower(): str(v) for k, v in data.items()}
                codes.update({str(v).lower(): str(v) for v in data.values()})
        _GEO_CODES, _GEO_NAMES = codes, names
    return _GEO_CODES, _GEO_NAMES


def resolve_geography(value: str, evidence_codes=()) -> tuple:
    """
    The master-data shortname a geography cell means.

    Returns (code, how, options): code '' when it cannot be settled, `how`
    saying what settled it, `options` the candidates left for a human.
    A shortname in the wrong case, or a country written out ("Mongolia"),
    is an exact lookup.  Several written together ("Mongolia, China") name
    no single ecoinvent geography, so the dataset's own regional flows
    ('Water, MN: Arhangay') decide when they all point to one of them.
    """
    codes, names = geography_master_data()
    text = str(value or '').strip()
    if not text:
        return '', '', ()
    if text in codes.values():
        return text, 'already a master-data shortname', ()
    lower = text.lower()
    if lower in codes:
        return codes[lower], 'the master-data shortname, in its own case', ()
    if lower in names:
        return names[lower], f'the master-data shortname of {text!r}', ()
    parts = [p.strip() for p in re.split(r'\s*(?:,|;|/|\+|&|\band\b)\s*', text) if p.strip()]
    found = []
    for part in parts:
        code = codes.get(part.lower()) or names.get(part.lower())
        if not code:
            found = []
            break
        if code not in found:
            found.append(code)
    if len(found) == 1:
        return found[0], f'the master-data shortname of {text!r}', ()
    if len(found) > 1:
        tagged = {str(c).upper() for c in evidence_codes}
        pointed = [c for c in found if c.upper() in tagged]
        if len(pointed) == 1 and tagged <= {pointed[0].upper()}:
            return pointed[0], (
                f'{text!r} names {", ".join(found)}, which no single ecoinvent '
                f'geography covers; every regional flow of the dataset is tagged '
                f'{pointed[0]} (e.g. its water), so it is {pointed[0]}'), ()
        return '', '', tuple(found) + ('GLO', 'RoW')
    import difflib
    close = difflib.get_close_matches(lower, list(names), n=4, cutoff=0.75)
    return '', '', tuple(dict.fromkeys(names[c] for c in close))


def fix_geographies(sheets: dict, dataset: str, project: str, applied: dict) -> list:
    """
    Every activity's geography as a master-data shortname.

    The .spold links a geography by its master-data id, so anything else --
    'Mongolia, China', 'it', 'Italy' -- ships a self-generated id that
    ecoEditor rejects.  What can be settled from the master data and the
    dataset itself is corrected everywhere the activity carries it (meta,
    exchanges, properties); the rest is left for a human, with the
    candidates offered.
    """
    meta = sheets.get('meta')
    if meta is None or meta.empty or 'geography' not in meta.columns:
        return []
    exchanges = sheets.get('exchanges')
    evidence = set()
    if exchanges is not None and 'name' in exchanges.columns:
        for name in exchanges['name'].dropna().astype(str):
            evidence.update(m.upper() for m in _FLOW_REGION_RE.findall(name))
    findings = []
    for idx, row in meta.iterrows():
        old = str(row.get('geography') or '').strip()
        activity = str(row.get('activityName') or '').strip()
        if not old or old.lower() == 'nan':
            continue
        code, how, options = resolve_geography(old, evidence)
        if code == old:
            continue
        if code and _fix_rejected(
                f'{project}||{dataset + (ACTIVITY_SEP + activity if activity else "")}'
                f'|geography|geography is not a master-data shortname'):
            code, options = '', (code,)     # you rejected it: offered, not applied
        if code:
            meta.at[idx, 'geography'] = code
            for sheet_name in ('exchanges', 'properties', 'parameters'):
                frame = sheets.get(sheet_name)
                if frame is None or frame.empty or 'geography' not in frame.columns:
                    continue
                mask = frame['geography'].astype(str).str.strip() == old
                if 'activityName' in frame.columns and activity:
                    mask &= frame['activityName'].astype(str).str.strip().isin([activity, '', 'nan'])
                if mask.any():
                    if frame['geography'].dtype != object:
                        frame['geography'] = frame['geography'].astype(object)
                    frame.loc[mask, 'geography'] = code
            where = dataset + (ACTIVITY_SEP + activity if activity else '')
            applied[f'{project}||{where}|geography|geography is not a master-data shortname'] = (
                f'{old!r} -> {code!r}', dataset)
            findings.append(Finding(
                dataset, activity, 'geography', old, code,
                'geography is not a master-data shortname', True, 'WARNING',
                f'Set to {code!r}: {how}.', '', project, code))
        else:
            findings.append(Finding(
                dataset, activity, 'geography', old,
                ' or '.join(options) if options else 'a master-data shortname',
                'geography is not a master-data shortname', False, 'BLOCKING',
                f'{old!r} is not an ecoinvent geography, so the .spold cannot link it. '
                + (f'It names {", ".join(o for o in options if o not in ("GLO", "RoW"))}, '
                   f'which no single ecoinvent geography covers, and nothing in the '
                   f'dataset says which one it is -- pick one below.'
                   if 'GLO' in options else
                   'Type the shortname (e.g. IT, MN, CN-NM).'),
                '', project, '', tuple((o, o) for o in options)))
    return findings


def check_exchange_names(sheets: dict, dataset: str, activity: str,
                         project: str) -> list:
    """
    Every distinct flow name in the exchanges, against the master data.

    One finding per distinct problem rather than per row -- a misspelling in
    60 rows is one decision, not sixty -- with the row count in the note and
    the correct name proposed where there is one.
    """
    exchanges = sheets.get('exchanges')
    if exchanges is None or exchanges.empty or 'name' not in exchanges.columns:
        return []

    known = master_data_names()
    findings = []
    seen: set = set()

    for _, row in exchanges.iterrows():
        name = str(row.get('name', '') or '').strip()
        group = str(row.get('group', '') or '').strip()
        if not name or name.lower() == 'nan':
            continue
        compartment = str(row.get('compartment', '') or '').strip()
        subcompartment = str(row.get('subcompartment', '') or '').strip()
        compartment = '' if compartment.lower() == 'nan' else compartment
        subcompartment = '' if subcompartment.lower() == 'nan' else subcompartment
        # Whether a NAME exists has nothing to do with the compartment, so a
        # substance misspelled in four compartments is one decision, not
        # four.  Only the compartment check is per compartment.
        kind = 'elementary' if group in _ELEMENTARY_GROUPS else 'intermediate'
        if (name, kind) in seen:
            name_checked = True
        else:
            name_checked = False
            seen.add((name, kind))
        count = int((exchanges['name'].astype(str).str.strip() == name).sum())

        if group in _OWN_PRODUCT_GROUPS:
            continue          # the dataset's own product: new by definition

        if group in _ELEMENTARY_GROUPS:
            match = known['elementary_lower'].get(name.lower())
            if match is None and name_checked:
                continue
            if match is None:
                product = simapro_product(name)
                proposal = (known['elementary_lower'].get(product.lower(), '')
                            if product else '')
                # An exact SimaPro-qualifier lookup is certain; nothing below
                # that falls back to a fuzzy guess ever is.
                certain = bool(proposal)
                # Same substance, different qualifier: offer the real ones
                # rather than pick one -- unless there is only one, which is
                # not a choice at all.
                variants = [] if proposal else qualifier_options(
                    name, known['elementary'])
                # Only a flow that exists in THIS row's compartment can be the
                # right one: bare "Water" is an emission to air or water and
                # never a resource "in water", so for a resource row it is
                # not a candidate at all.  Where nothing fits the compartment
                # (itself possibly wrong), every variant stays on offer.
                here = (compartment, subcompartment)

                def fits(flow):
                    return here in known['elementary'].get(flow, set())

                fitting = [v for v in variants if fits(v)]
                variants = fitting or variants
                chosen_by = ''
                # The head on its own may BE the flow -- 'Ammonia, GR' is
                # 'Ammonia' with a geography stuck to it -- and one variant
                # is not a choice.  Either way there is a right answer, so
                # give it rather than a list -- but only when it fits here.
                head = str(name).split(',')[0].strip()
                if variants:
                    exact_head = known['elementary_lower'].get(head.lower(), '')
                    if exact_head and fits(exact_head):
                        proposal, variants, certain = exact_head, [], True
                    elif len(variants) == 1 and fits(variants[0]):
                        proposal, variants, certain = variants[0], [], True
                    elif fitting:
                        # Several fit the compartment: let the provider's own
                        # words (the rest of the name, the comments) decide.
                        rows = exchanges[exchanges['name'].astype(str).str.strip() == name]
                        evidence = ' '.join(
                            [strip_match_noise(name)]
                            + [str(v) for column in ('comment', 'uncertainty comment')
                               if column in rows.columns
                               for v in rows[column].dropna()])
                        picked, chosen_by = flow_from_evidence(head, fitting, evidence)
                        if picked:
                            proposal, variants, certain = picked, [], True
                if variants:
                    shown = '; '.join(variants[:6])
                    findings.append((
                        EXCHANGE_PREFIX + name, name, shown,
                        'exchange name uses a qualifier ecoinvent does not have',
                        'BLOCKING',
                        f'{count} row(s). ecoinvent has this substance as: '
                        f'{shown}'
                        + (f' (and {len(variants) - 6} more)'
                           if len(variants) > 6 else '')
                        + '. Which one it is is a modelling call, so none is '
                          'assumed -- pick the right one below.',
                        '', False, tuple((v, v) for v in variants)))
                    continue
                if not certain:
                    variant = spelling_variant(name, known['elementary'], 'elementary')
                    if variant:
                        proposal, certain = variant, True
                    else:
                        proposal = proposal or nearest_flow(name, known['elementary'])
                findings.append((
                    EXCHANGE_PREFIX + name, name,
                    proposal or '(a name that exists in the master data)',
                    'exchange name is not an ecoinvent elementary flow',
                    'BLOCKING',
                    f'{count} row(s) carry this name. The .spold cannot link it '
                    f'and ships a self-generated id.'
                    + (f" Did you mean '{proposal}'?" if proposal and not chosen_by else '')
                    + (f" '{proposal}' is chosen: of the flows in this row's "
                       f"compartment, {chosen_by}." if chosen_by else ''),
                    proposal, certain, ()))
                continue
            if match != name and name_checked:
                continue
            if match != name:
                # A case difference against the master data has exactly one
                # right spelling -- not a modelling call, so it is corrected
                # outright rather than queued for review.
                findings.append((
                    EXCHANGE_PREFIX + name, name, match,
                    'exchange name differs from the master data in case',
                    'BLOCKING',
                    f'{count} row(s). The master data spells it {match!r}.',
                    match, True, ()))
                continue
            allowed = known['elementary'].get(match, set())
            if (allowed and (compartment, subcompartment) not in allowed
                    and (name, compartment, subcompartment) not in seen):
                seen.add((name, compartment, subcompartment))
                # The name is right; the fix is WHERE it sits.  Usually only the
                # subcompartment is off, so the pairs in the stated compartment
                # are the candidates; only if the flow never occurs in that
                # compartment is every pair it has on offer.  One candidate is
                # not a choice and is applied outright.
                same = sorted(p for p in allowed if p[0] == compartment)
                pairs = same or sorted(allowed)
                values = [placement_value(c, s) for c, s in pairs]
                shown = ', '.join(values[:6]) + (f' (and {len(values) - 6} more)'
                                                 if len(values) > 6 else '')
                single = len(values) == 1
                convention = None if single else conventional_pair(match, compartment, allowed)
                if convention:
                    values, single = [placement_value(*convention)], True
                findings.append((
                    placement_column(name, compartment, subcompartment),
                    f'{name} in {placement_value(compartment, subcompartment)}',
                    shown, 'exchange is in a compartment the flow does not have',
                    'BLOCKING',
                    f'{count} row(s). The master data has {match!r} only in: '
                    f'{shown}. The name is right; '
                    + (f'by the agricultural convention it goes to {values[0]}.'
                       if convention else
                       'this is the only place it can go, so it is moved there.'
                       if single else 'pick where it belongs.'),
                    values[0] if single else '', single,
                    () if single else tuple((v, v) for v in values)))
            continue

        # Intermediate: a product this dataset buys.
        canonical = canonical_flow_name(name)
        if canonical.lower() in known['intermediate_lower']:
            # Known, but perhaps in another case.  The converter looks a
            # standard product's id up by its exact spelling, so "Tap water"
            # would pass here and still ship a self-generated id.  One right
            # spelling: corrected outright, like an elementary flow's case.
            exact = known['intermediate_spelling'].get(canonical.lower())
            if exact and exact != name and not name_checked:
                findings.append((
                    EXCHANGE_PREFIX + name, name, exact,
                    'exchange name differs from the master data in case',
                    'BLOCKING',
                    f'{count} row(s). The master data spells it {exact!r}.',
                    exact, True, ()))
            continue
        if name_checked:
            continue
        # A resource name used as a product is a group mistake, not a typo.
        # A region or legacy suffix baked into the name (rather than kept in
        # its own field) can hide this: "Water, CN: Nei Mongol" matches
        # nothing here until the region is set aside, even though "Water"
        # alone is exactly an elementary flow.
        stripped = strip_match_noise(name)
        elementary_name = (known['elementary_lower'].get(name.lower())
                          or (known['elementary_lower'].get(stripped.lower())
                              if stripped.lower() != name.lower() else None))
        if elementary_name:
            findings.append((
                EXCHANGE_PREFIX + name, f'{name} as {group}', 'an elementary exchange',
                'exchange is in the wrong group', 'BLOCKING',
                f'{count} row(s). {name!r} is an elementary flow (a resource or '
                f'an emission), not a product that can be bought -- its group '
                f'should be FromEnvironment or ToEnvironment.'
                + (f' Once the region/qualifier in the name is set aside, it '
                   f'matches {elementary_name!r} exactly.'
                   if stripped.lower() != name.lower() else ''),
                '', False, ()))
            continue
        # A flow this pipeline invents ("diesel used in, ...") is not meant
        # to be in ecoinvent -- it is declared in master_data_lookup.json.
        # Missing there, the answer is to DECLARE it, never to rename it to
        # whatever it resembles: the nearest name is the field operation
        # itself, and taking that would turn the fuel into the operation.
        if name.lower().startswith('diesel used in'):
            findings.append((
                EXCHANGE_PREFIX + name, name, 'a declared custom flow',
                'custom flow is not declared in the master-data lookup',
                'BLOCKING',
                f"{count} row(s). This is one of the pipeline's own flows, so "
                f'it belongs in master_data_lookup.json (and its classification '
                f'in master_data_classifications.json). Do NOT rename it -- '
                f'without the entry the .spold ships a self-generated id.',
                '', False, ()))
            continue

        # A SimaPro-style name: the ecoinvent product is the part before the
        # geography, and it is an exact lookup rather than a guess.
        product = simapro_product(name)
        if product:
            exact = next((k for k in known['intermediate']
                          if str(k).strip().lower() == product.lower()), '')
            if exact:
                findings.append((
                    EXCHANGE_PREFIX + name, name, exact,
                    'exchange name is in SimaPro format, not ecoinvent',
                    'BLOCKING',
                    f'{count} row(s). SimaPro writes "Product {{GEO}}| activity | '
                    f'Cut-off, S"; ecoinvent names the flow {exact!r} and carries '
                    f'the geography in its own field.',
                    exact, True, ()))
                continue

        variant = spelling_variant(canonical, known['intermediate'], 'intermediate')
        if variant:
            findings.append((
                EXCHANGE_PREFIX + name, name, variant,
                'exchange name is a spelling variant of an ecoinvent product', 'BLOCKING',
                f'{count} row(s). ecoinvent spells it {variant!r}.', variant, True, ()))
            continue
        proposal = nearest_flow(canonical, known['intermediate'])
        findings.append((
            EXCHANGE_PREFIX + name, name,
            proposal or '(a name that exists in the master data)',
            'exchange name is not an ecoinvent product', 'BLOCKING',
            f'{count} row(s). Neither the standard master data nor this '
            f"pipeline's declared custom flows have it, so the .spold ships a "
            f'self-generated id.'
            + (f" Did you mean '{proposal}'?" if proposal else ''),
            proposal, False, ()))

    return findings


# Meta columns of META_COLS the model never adds (see process_dataset).
_NOT_ADDED_META_COLUMNS = ('activityId', 'activityNameId')

_PRODUCT_GROUPS = ('ReferenceProduct', 'ByProduct')


def _text_series(series):
    return series.map(lambda v: '' if pd.isna(v) else str(v).strip())


def normalise_product_names(sheets: dict, dataset: str, activity: str,
                            project: str, applied: dict) -> list:
    """
    Give the dataset's own products the names its properties sheet uses.

    A source file can leave working labels on the exchanges ("Simplified
    Fibre - kg Fleece v2.1 farmed ecoinvent DB submission") while the
    properties -- written for the same products -- name them properly ("raw
    cashmere").  Paired in order within each group, and only where both
    sheets list the same number of products; otherwise nothing is guessed.
    """
    exchanges, properties = sheets.get('exchanges'), sheets.get('properties')
    needed = {'group', 'name'}
    if (exchanges is None or exchanges.empty or properties is None or properties.empty
            or not needed <= set(exchanges.columns) or not needed <= set(properties.columns)):
        return []
    findings = []
    ex_group, pr_group = _text_series(exchanges['group']), _text_series(properties['group'])
    ex_name, pr_name = _text_series(exchanges['name']), _text_series(properties['name'])
    for group in _PRODUCT_GROUPS:
        ours = list(dict.fromkeys(ex_name[ex_group == group]))
        theirs = list(dict.fromkeys(pr_name[pr_group == group]))
        if not ours or len(ours) != len(theirs) or set(ours) == set(theirs):
            continue
        for old, new in zip(ours, theirs):
            if old == new or not new or new in ours:
                continue
            if _fix_rejected(f'{project}||{dataset}|{EXCHANGE_PREFIX}{old}|'
                             f'product renamed to its properties name'):
                continue                    # you rejected this correction
            mask = (ex_group == group) & (ex_name == old)
            exchanges['name'] = exchanges['name'].astype(object)
            exchanges.loc[mask, 'name'] = new
            key = f'{project}||{dataset}|{EXCHANGE_PREFIX}{old}|product renamed to its properties name'
            applied[key] = (f'{old!r} -> {new!r} ({int(mask.sum())} row(s))', dataset)
            findings.append(Finding(
                dataset, activity, EXCHANGE_PREFIX + old, old, new,
                'product renamed to its properties name', True, 'WARNING',
                f'The {group} is named {new!r} in the properties sheet; the exchange '
                f'carried a working label.', '20 (agri)', project, new))
    return findings


# ── Prices to EUR2005 ────────────────────────────────────────────────────────
# ecoinvent prices are EUR2005 (agri row 26).  A price in another currency,
# or in EUR of another year, is converted with the review skill's price
# converter (ECB annual exchange rates + euro-area PPI, 2005=1).  The year is
# the one the price itself states -- in its unit ("USD2023") or its comment
# ("Year reference 2017") -- or else every year of the dataset's period,
# averaged, as the converter does for a price over several years.  The
# original figure is kept in the comment, so the conversion can be checked.
PRICE_TARGET_UNIT = 'EUR2005'
_PRICE_UNIT_RE = re.compile(r'^\s*([A-Za-z]{3})\s*((?:19|20)\d\d)?\s*$')
_YEAR_RE = re.compile(r'\b((?:19|20)\d\d)\b')


def _price_years(comment, unit_year, meta_row) -> tuple[list, str]:
    """(years, where they came from) for one price."""
    if unit_year:
        return [int(unit_year)], 'the year in its unit'
    years = sorted({int(y) for y in _YEAR_RE.findall(str(comment or ''))})
    if len(years) == 1:
        return years, 'the year stated in its comment'
    if len(years) > 1:
        return [], f'its comment names several years ({", ".join(map(str, years))})'
    start = _year(meta_row.get('startDate')) if meta_row is not None else None
    end = _year(meta_row.get('endDate')) if meta_row is not None else None
    if start and end and end >= start:
        return list(range(start, end + 1)), (f'the dataset period {start}' if start == end
                                             else f'the dataset period {start}-{end}, averaged')
    return [], 'no year is stated and the dataset has no period'


def convert_prices_to_eur2005(sheets: dict, dataset: str, activity: str,
                              project: str, applied: dict) -> list:
    """Convert every price property not in EUR2005, in place."""
    properties = sheets.get('properties')
    meta = sheets.get('meta')
    if properties is None or properties.empty or \
            not {'propertyName', 'unitName', 'amount'} <= set(properties.columns):
        return []
    import convert_price_to_eur2005 as price_converter
    model = price_converter.load_model()
    known = model['exchange_rates_currency_per_eur']
    metas = {}
    if meta is not None and not meta.empty and 'activityName' in meta.columns:
        metas = {str(r['activityName']).strip(): r for _, r in meta.iterrows()}
    first_meta = meta.iloc[0] if meta is not None and not meta.empty else None

    findings, done = [], []
    for column in ('unitName', 'comment', 'amount'):
        if column in properties.columns and properties[column].dtype != object:
            properties[column] = properties[column].astype(object)
    for idx, prop in properties.iterrows():
        if str(prop.get('propertyName', '')).strip().lower() != 'price':
            continue
        unit = str(prop.get('unitName', '') or '').strip()
        if unit.upper() == PRICE_TARGET_UNIT:
            continue
        name = str(prop.get('name', '') or '').strip()
        amount = pd.to_numeric(prop.get('amount'), errors='coerce')
        match = _PRICE_UNIT_RE.match(unit)
        currency = match.group(1).upper() if match else ''
        problem = ''
        if pd.isna(amount):
            problem = 'it has no amount'
        elif currency not in known:
            problem = (f'its unit {unit!r} is not a currency the converter covers '
                       f'({", ".join(sorted(known))})')
        comment = prop.get('comment')
        comment = '' if _blank(comment) else str(comment).strip()
        if not problem:
            row = metas.get(str(prop.get('activityName', '')).strip(), first_meta)
            years, source = _price_years(comment, match.group(2), row)
            if not years:
                problem = f'the price year is unclear: {source}'
        if not problem:
            try:
                result = price_converter.convert_price(
                    [{'currency': currency, 'year': y, 'amount': float(amount)} for y in years],
                    model)
            except ValueError as error:
                problem = str(error)
        if problem:
            findings.append(Finding(
                dataset, activity, 'properties', f'price of {name}: {amount} {unit}',
                f'a price in {PRICE_TARGET_UNIT}', 'price not converted to EUR2005',
                False, 'ACCEPTABLE_FOR_NOW',
                f'Not converted because {problem}. State the currency and year (e.g. '
                f'unit USD2023), or enter the price in {PRICE_TARGET_UNIT}.',
                '26 (agri)', project))
            continue
        if _fix_rejected(f'{project}||{dataset}|properties|prices converted to EUR2005'):
            continue                        # you rejected the conversion
        span = (f'{years[0]}' if len(years) == 1 else f'{years[0]}-{years[-1]}')
        original = f'Original price: {float(amount):g} {currency} ({span}).'
        properties.at[idx, 'amount'] = result['eur2005']
        properties.at[idx, 'unitName'] = PRICE_TARGET_UNIT
        properties.at[idx, 'comment'] = ' '.join(p for p in (comment, original, result['comment']) if p)
        done.append(f'{name}: {float(amount):g} {currency} ({span}, {source}) -> '
                    f'{result["eur2005"]:.4g} {PRICE_TARGET_UNIT}')
    if done:
        applied[f'{project}||{dataset}|properties|prices converted to EUR2005'] = (
            '; '.join(done), dataset)
        findings.append(Finding(
            dataset, activity, 'properties', '; '.join(done), PRICE_TARGET_UNIT,
            'prices converted to EUR2005', True, 'WARNING',
            'Converted with ECB annual exchange rates and the euro-area PPI '
            '(the review skill\'s price converter). The original price is kept in '
            'each property comment.', '26 (agri)', project))
    return findings


# ── Properties from the ecoinvent defaults ──────────────────────────────────
# Every product a dataset buys or makes that ecoinvent also has carries
# ecoinvent's properties for it (dry mass, water, carbon, price, ...), as the
# review checklist asks.  Master Data's values come first, as the standard
# (apply_master_data_properties); a property neither the dataset nor Master
# Data gives is then added with the value ecoinvent's datasets give that
# product, and a value already there is never touched here.  The defaults are reduced from
# the ecoinvent properties export by build_property_defaults.py.
_PROPERTY_GROUPS = ('ReferenceProduct', 'ByProduct', 'FromTechnosphere')
_PROPERTY_MIN_AGREEMENT = 0.5      # share of ecoinvent datasets that must agree
# Only product properties are carried over.  The export also holds what one
# ecoinvent dataset kept for its own calculation ('UVEK_link_1', 'input_kg',
# 'mass of a straw bale', fuel-consumption tiers): never a property of the
# product.  The six mandatory properties and price always qualify; a heating
# value or an element content only where most of ecoinvent's datasets of the
# product carry it.
MANDATORY_PROPERTIES = ('wet mass', 'dry mass', 'water content', 'water in wet mass',
                        'carbon content, fossil', 'carbon content, non-fossil', 'price')
_PROPERTY_OPTIONAL_RE = re.compile(r'^(?:heating value, net|[a-z]+ content)$')
_PROPERTY_OPTIONAL_SHARE = 0.5     # of the product's datasets that carry the property
_PROPERTY_DEFAULTS = None


def property_defaults() -> dict:
    """{'name|unit': {property: {amount, unitName, datasets, agree}}}, or {}."""
    global _PROPERTY_DEFAULTS
    if _PROPERTY_DEFAULTS is None:
        try:
            from config_new_datasets import PROPERTY_DEFAULTS_FILE
            with open(PROPERTY_DEFAULTS_FILE, encoding='utf-8') as handle:
                _PROPERTY_DEFAULTS = json.load(handle).get('products', {})
        except Exception:
            _PROPERTY_DEFAULTS = {}
    return _PROPERTY_DEFAULTS


MD_PROPERTIES_KEY = 'Master Data properties applied'


def _cell(value) -> str:
    return '' if _blank(value) else str(value).strip()

_MD_TOLERANCE = 1e-6               # relative: a difference below this is rounding


def apply_master_data_properties(sheets: dict, dataset: str, activity: str,
                                 project: str, applied: dict) -> list:
    """
    Master Data is the standard for a product's properties.  Wherever it
    defines one for a flow of the dataset -- an input or the dataset's own
    product -- that value is used: a missing property is added, a different
    value replaced.  Every change is listed with its old and new value, so a
    replaced measurement is seen; apply = no in Corrections keeps the
    dataset's values.  A property computed by a formula is left to it, and so
    is the price of the dataset's own products (filled only when missing).
    """
    import skill_checks
    md = skill_checks.master_data().get('properties', {})
    exchanges = sheets.get('exchanges')
    key = f'{project}||{dataset}|properties|{MD_PROPERTIES_KEY}'
    if (not md or exchanges is None or exchanges.empty or _fix_rejected(key)
            or not {'group', 'name'} <= set(exchanges.columns)):
        return []
    properties = sheets.get('properties')
    current = {}
    if properties is not None and not properties.empty and \
            {'name', 'propertyName', 'amount'} <= set(properties.columns):
        for _, p in properties.iterrows():
            current.setdefault((_cell(p.get('name')), _cell(p.get('propertyName')).lower()), p)
    changes = []
    flows = exchanges[_text_series(exchanges['group']).isin(_PROPERTY_GROUPS)]
    # The price of what the dataset itself produces is its own data -- it
    # drives the economic allocation and varies by variety and region -- so
    # Master Data only supplies it where the dataset has none.
    own_product = {n: True for n, g in zip(_text_series(flows['name']), _text_series(flows['group']))
                   if g in ('ReferenceProduct', 'ByProduct')}
    for name in dict.fromkeys(_text_series(flows['name'])):
        for prop, info in sorted((md.get(name) or {}).items()):
            value, unit = info.get('amount'), info.get('unitName')
            if value is None:
                continue
            had = current.get((name, prop.lower()))
            if had is not None and not _blank(had.get('mathematicalRelation')):
                continue                       # computed by its formula
            old = pd.to_numeric(had.get('amount'), errors='coerce') if had is not None else None
            if old is not None and pd.notna(old) and (
                    abs(old - value) <= _MD_TOLERANCE * max(abs(value), 1e-12)):
                continue                       # already the Master Data value
            if (prop.lower() == 'price' and old is not None and pd.notna(old)
                    and own_product.get(name)):
                continue                       # the dataset's own price: allocation data
            before = ('missing' if old is None or pd.isna(old)
                      else f'{old:g} {_cell(had.get("unitName"))}'.strip())
            set_property(sheets, name, prop, value, unit,
                         'Master Data value.' if before == 'missing'
                         else f'Master Data value (the dataset had {before}).')
            changes.append(f'{name}: {prop} {before} -> {value:g} {unit}')
    if not changes:
        return []
    applied[key] = (f'{len(changes)} propert(ies) set to Master Data: ' + '; '.join(changes),
                    dataset)
    return [Finding(dataset, activity, 'properties', f'{len(changes)} property value(s)',
                    'the Master Data values', MD_PROPERTIES_KEY, True, 'WARNING',
                    'Master Data defines these properties, so its values are used: '
                    + '; '.join(changes) + '.', '96, 103-109 (Checks) / 24 (agri)', project)]


def fill_properties_from_ecoinvent(sheets: dict, dataset: str, activity: str,
                                   project: str, applied: dict) -> list:
    """Add the ecoinvent default of every property a matched product lacks."""
    defaults = property_defaults()
    exchanges = sheets.get('exchanges')
    if not defaults or exchanges is None or exchanges.empty or \
            not {'group', 'name', 'unitName'} <= set(exchanges.columns):
        return []
    from config_new_datasets import PROPERTIES_COLS
    properties = sheets.get('properties')
    if properties is None or properties.empty:
        properties = pd.DataFrame(columns=PROPERTIES_COLS)
    have = set()
    if {'activityName', 'name', 'propertyName'} <= set(properties.columns):
        for _, p in properties.iterrows():
            # A property row with no activityName belongs to the dataset's
            # activity: keyed under '*', or the fill below would not see it and
            # add the same property a second time (two dry masses, two values).
            activity = '' if _blank(p.get('activityName')) else str(p.get('activityName')).strip()
            have.add((activity or '*', '' if _blank(p.get('group')) else str(p.get('group')).strip(),
                      str(p['name']).strip(), str(p['propertyName']).strip().lower()))

    added, per_product = [], collections.Counter()
    seen = set()
    for _, row in exchanges[_text_series(exchanges['group']).isin(_PROPERTY_GROUPS)].iterrows():
        act = str(row.get('activityName') or '').strip()
        group = str(row['group']).strip()
        name = str(row['name']).strip()
        unit = str(row['unitName'] or '').strip()
        if (act, group, name) in seen:
            continue
        seen.add((act, group, name))
        entry = defaults.get(f'{name}|{unit}')
        if not entry:
            continue
        carried = max((v.get('datasets') or 0) for v in entry.values()) or 1
        for prop, info in sorted(entry.items()):
            if ((act, group, name, prop.lower()) in have
                    or ('*', group, name, prop.lower()) in have):
                continue
            if prop not in MANDATORY_PROPERTIES and not (
                    _PROPERTY_OPTIONAL_RE.match(prop)
                    and (info.get('datasets') or 0) >= _PROPERTY_OPTIONAL_SHARE * carried):
                continue
            if info.get('datasets') and info.get('agree', 0) / info['datasets'] < _PROPERTY_MIN_AGREEMENT:
                continue                       # ecoinvent's own datasets disagree
            new = {c: None for c in PROPERTIES_COLS}
            new.update({
                'activityName': act, 'geography': row.get('geography'), 'group': group,
                'name': name, 'propertyName': prop, 'unitName': info.get('unitName'),
                'amount': info['amount'],
                'activityLink_activityName': row.get('activityLink_activityName'),
                'activityLink_geography': row.get('activityLink_geography'),
                'comment': (f"Default value of the ecoinvent product '{name}' "
                            f"({info.get('agree')} of {info.get('datasets')} ecoinvent "
                            f"dataset(s) give this value)."),
            })
            added.append(new)
            per_product[(group, name)] += 1
    if not added or _fix_rejected(
            f'{project}||{dataset}|properties|filled from the ecoinvent defaults'):
        return []                           # nothing to add, or you rejected it
    frame = pd.DataFrame(added)
    sheets['properties'] = (pd.concat([properties, frame], ignore_index=True)
                            if not properties.empty else frame.reindex(columns=PROPERTIES_COLS))
    applied[f'{project}||{dataset}|properties|filled from the ecoinvent defaults'] = (
        f'{len(added)} property value(s) added for {len(per_product)} exchange(s)', dataset)
    own = sorted(n for (g, n) in per_product if g in ('ReferenceProduct', 'ByProduct'))
    return [Finding(
        dataset, activity, 'properties', f'{len(per_product)} exchange(s) without some properties',
        'the ecoinvent defaults', 'properties filled from the ecoinvent defaults', True, 'WARNING',
        f'{len(added)} property value(s) added from the ecoinvent 3.12 properties export for '
        f'{len(per_product)} exchange(s)'
        + (f', among them the products {", ".join(repr(n) for n in own[:4])} -- check that '
           f'ecoinvent\'s values (above all its price) fit this dataset' if own else '')
        + '. Values the dataset already had are kept.', '96, 103-109 (Checks)', project)]


ZERO_AMOUNT_COLUMN = 'exchanges'
ZERO_AMOUNT_KIND   = 'zero-amount exchanges'
_ZERO_DROP_ANSWERS = ('drop', 'delete', 'yes', 'y')
_ZERO_KEEP_ANSWERS = ('keep', 'no', 'n')


def _zero_amount_mask(exchanges: pd.DataFrame):
    """Rows whose amount is exactly 0 -- never the reference product, and
    never one whose amount a formula computes."""
    if exchanges is None or exchanges.empty or 'amount' not in exchanges.columns:
        return None
    amount = pd.to_numeric(exchanges['amount'], errors='coerce')
    group = _text_series(exchanges['group']) if 'group' in exchanges.columns else pd.Series('', index=exchanges.index)
    formula = (_text_series(exchanges['mathematicalRelation'])
               if 'mathematicalRelation' in exchanges.columns
               else pd.Series('', index=exchanges.index))
    return (amount == 0) & (group != 'ReferenceProduct') & (formula == '')


def _zero_amount_names(exchanges: pd.DataFrame, zero) -> list:
    return [f"{r['name']} ({r.get('compartment', '')}/{r.get('subcompartment', '')})"
            for _, r in exchanges[zero].iterrows()]


def remove_zero_exchanges(sheets: dict) -> list:
    """
    Drop the zero-amount exchanges, together with any properties row that
    belonged to them alone.  Returns the names dropped.
    """
    exchanges = sheets.get('exchanges')
    zero = _zero_amount_mask(exchanges)
    if zero is None or not zero.any():
        return []
    dropped = exchanges[zero]
    names = [describe_row(r) for _, r in dropped.iterrows()]
    keys = ['group', 'name', 'compartment', 'subcompartment']
    keys = [k for k in keys if k in exchanges.columns]
    sheets['exchanges'] = exchanges[~zero].reset_index(drop=True)

    properties = sheets.get('properties')
    if properties is not None and not properties.empty and set(keys) <= set(properties.columns):
        remaining = {tuple(_text_series(sheets['exchanges'][k])[i] for k in keys)
                     for i in sheets['exchanges'].index}
        gone = {tuple(_text_series(dropped[k])[i] for k in keys) for i in dropped.index}
        orphan = properties.apply(
            lambda r: tuple('' if pd.isna(r[k]) else str(r[k]).strip() for k in keys) in (gone - remaining),
            axis=1)
        if orphan.any():
            names += [f'property row {describe_row(r)}' for _, r in properties[orphan].iterrows()]
            sheets['properties'] = properties[~orphan].reset_index(drop=True)
    return names


def drop_zero_exchanges(sheets: dict, dataset: str, activity: str,
                        project: str, applied: dict, answers: dict | None = None) -> list:
    """
    Exchanges whose amount is exactly 0 carry nothing into the inventory, but
    deleting one is a change to the inventory a reviewer should see (review
    checklist agri row 14), so it is ASKED in the fix sheet rather than done:
    `drop` removes them, `keep` leaves them.  The answer is applied with the
    other typed answers, before this runs, so a dataset already answered
    `drop` has none left here.
    """
    exchanges = sheets.get('exchanges')
    zero = _zero_amount_mask(exchanges)
    if zero is None or not zero.any():
        return []
    key = f'{project}||{dataset}|{ZERO_AMOUNT_COLUMN}|{ZERO_AMOUNT_KIND}'
    if str((answers or {}).get(key, '')).strip().lower() in _ZERO_KEEP_ANSWERS:
        return []
    names = _zero_amount_names(exchanges, zero)
    return [Finding(dataset, activity, ZERO_AMOUNT_COLUMN, '; '.join(names),
                    '(dropped, if you confirm)', ZERO_AMOUNT_KIND, False,
                    'ACCEPTABLE_FOR_NOW',
                    f'{len(names)} exchange(s) with an amount of 0 and no formula '
                    f'carry nothing into the inventory. Confirm to delete them '
                    f'(and any properties that belong to them alone), or keep them.',
                    '14 (agri)', project, 'drop',
                    (('Delete the zero-amount exchange(s)', 'drop'),
                     ('Keep them as they are', 'keep')))]


def regional_decision(decisions: dict, kind: str, found, options) -> dict | None:
    """
    A remembered answer for the same flow under another region tag.

    'Water, CN: Nei Mongol' and 'Water, MN: Arhangay' are one question --
    which ecoinvent flow is this bare 'Water' -- asked once per province.
    Once it is answered for one region it is answered for all of them,
    provided that every earlier answer for that flow agrees, and that the
    answer is one of the flows offered for THIS row (the options already fit
    its compartment, so a resource is never answered with an emission).
    """
    if kind != 'exchange name uses a qualifier ecoinvent does not have':
        return None
    name = str(found or '').strip()
    head = strip_match_noise(name).lower()
    if not head or head == name.lower():
        return None                         # no region tag to set aside
    offered = {str(value) for _label, value in options}
    matches = [d for d in decisions.values()
               if d.get('kind') == kind
               and strip_match_noise(d.get('found', '')).lower() == head
               and str(d.get('found', '')).strip().lower() != head]
    values = {d['value'] for d in matches}
    if len(values) != 1 or not values <= offered:
        return None
    return matches[0]


def _apply_exchange_finding(sheets, exchanges_sheet, decisions, applied, findings,
                            dataset, first_activity, project, column, found, expected,
                            kind, severity, note, proposal, certain, options) -> bool:
    """Apply one exchange-name or placement finding when it is certain, and
    record it.  Returns True when a flow was renamed."""
    fixed = renamed = False
    # A remembered decision is exactly as certain as a case fix against
    # the master data: some human already settled this same flow name
    # once, so every dataset that repeats it gets the same answer rather
    # than asking again.
    # The data first: when the row's own name, compartment and comments
    # already settle the answer (certain), that answer stands, and a
    # remembered decision only fills in where the data cannot decide.
    cached = None if (certain and proposal) else (
        decisions.get(decision_signature(column, kind, found) or '')
        or regional_decision(decisions, kind, found, options))
    if cached:
        proposal, certain, options = cached['value'], True, ()
        note = f'{note} (remembered decision from {cached["first_seen"]})'
    # A certain correction has exactly one right answer -- a case fix
    # against the master data, or an exact SimaPro/qualifier lookup --
    # so it is applied outright, the same as a whitespace tidy-up or the
    # locked yield line, rather than queued for a decision that is not
    # really a decision.
    if certain and proposal and _fix_rejected(f'{project}||{dataset}|{column}|{kind}'):
        # You rejected this correction: it is offered again, not applied.
        certain = False
        note = f'{note} (automatic correction rejected in the Corrections sheet)'
    if certain and proposal and column.startswith(PLACEMENT_PREFIX):
        # Where the flow sits, not what it is called: move, never rename.
        ok, description = apply_fix(sheets, column, kind, proposal)
        if ok:
            fixed, severity = True, 'WARNING'
            applied[f'{project}||{dataset}|{column}|{kind}'] = (description, dataset)
    elif certain and proposal and exchanges_sheet is not None \
            and not exchanges_sheet.empty and 'name' in exchanges_sheet.columns \
            and column.startswith(EXCHANGE_PREFIX):
        old_name = column[len(EXCHANGE_PREFIX):]
        mask = exchanges_sheet['name'].astype(str).str.strip() == old_name
        # The same spelling can be both a bought product and an emission
        # ('Abamectine'): a correction to an elementary name renames only the
        # elementary rows, a correction to a product name only the others.
        if 'group' in exchanges_sheet.columns:
            known = master_data_names()
            to_elementary = str(proposal) in known['elementary']
            to_product = canonical_flow_name(proposal).lower() in known['intermediate_lower']
            elementary_rows = _text_series(exchanges_sheet['group']).isin(_ELEMENTARY_GROUPS)
            if to_elementary and not to_product:
                mask &= elementary_rows
            elif to_product and not to_elementary:
                mask &= ~elementary_rows
        if mask.any():
            if exchanges_sheet['name'].dtype != object:
                exchanges_sheet['name'] = exchanges_sheet['name'].astype(object)
            exchanges_sheet.loc[mask, 'name'] = proposal
            fixed = renamed = True
            severity = 'WARNING'
            applied[f'{project}||{dataset}|{column}|{kind}'] = (
                f'{old_name!r} -> {proposal!r} ({int(mask.sum())} row(s))', dataset)
    # Checklist agri 20 (the flow's name exists in Master Data), agri 18 for
    # where it sits (compartment / subcompartment consistent with Master Data).
    findings.append(Finding(dataset, first_activity, column, found, expected,
                            kind, fixed, severity, note,
                            '18 (agri)' if column.startswith(PLACEMENT_PREFIX) else '20 (agri)',
                            project, proposal, options))
    return renamed


def process_dataset(path: str, apply_fixes: bool, answers: dict | None = None,
                    project: str = '', sheets: dict | None = None):
    """
    Process one dataset workbook.  Returns (sheets, findings, changed, applied).

    Typed answers from the fix sheet are applied FIRST, so everything after
    validates the corrected dataset and the finding they answer disappears
    instead of being reported all over again.  `sheets` is the workbook as
    already read by the caller (a copy is worked on); read here otherwise.
    """
    sheets    = (pd.read_excel(path, sheet_name=None) if sheets is None
                 else {name: frame.copy(deep=True) for name, frame in sheets.items()})
    from config_new_datasets import drop_template_guidance
    drop_template_guidance(sheets, path, 'meta model')
    dataset   = os.path.basename(path)
    global _REJECTED_FIXES
    meta_facts.load(project, dataset, sheets)
    try:
        import review_workbook
        _REJECTED_FIXES = review_workbook.rejected_keys(project) if project else set()
    except Exception:
        _REJECTED_FIXES = set()
    profile   = profile_for(project)
    decisions = load_decisions()
    findings: list[Finding] = []
    renames: dict[str, str] = {}
    applied: dict = {}

    # The meta cells as they were before any answer: two answers about the same
    # comment (its years, and a wrong crop name in it) are each a full text
    # built from this original, so the second must not undo the first.
    meta_before = sheets['meta'].copy() if sheets.get('meta') is not None else None
    for key, value in (answers or {}).items():
        key_project, _, after = key.partition('||')
        key_dataset, _, rest = after.partition('|')
        key_dataset, key_activity = split_key_dataset(key_dataset)
        if key_project != project or key_dataset != dataset:
            continue
        if _fix_rejected(key):
            continue                        # you set this answer to apply = no
        column, kind = split_key_rest(rest)
        value = merge_text_answer(meta_before, sheets.get('meta'), column, key_activity, value)
        ok, description = apply_fix(sheets, column, kind, value, key_activity)
        if ok:
            applied[key] = (description, dataset)
        else:
            findings.append(Finding(dataset, '', column, value, '',
                                    f'your_fix could not be applied: {description}',
                                    False, 'BLOCKING',
                                    'Correct the value in the fix sheet and re-run.',
                                    '', project))

    meta = sheets.get('meta')
    if meta is None or meta.empty:
        # Not a dataset at all -- an intermediate the pipeline left behind, say.
        # Reported as INFO and skipped, not failed: nothing is wrong with it.
        findings.append(Finding(dataset, '', 'meta', 'no meta sheet',
                                'a meta row per activity',
                                'not a dataset -- skipped', False,
                                'INFO', '', '', project))
        return sheets, findings, False, applied

    # Every column of the DataX meta schema, in its order: a column the sheet
    # lacks is never checked or filled, and ecoEditor expects the full set.
    # activityId / activityNameId are not part of it here: the .spold
    # converter generates both when they are absent, so they are never added,
    # and an EMPTY one (as the model used to add) is taken out again.  A real
    # id -- from an ecoinvent .spold read in -- is kept: dropping it would give
    # the dataset a new identity.
    from config_new_datasets import META_COLS
    wanted = [c for c in META_COLS if c not in _NOT_ADDED_META_COLUMNS]
    empty_ids = [c for c in _NOT_ADDED_META_COLUMNS
                 if c in meta.columns and meta[c].map(_blank).all()]
    if empty_ids:
        meta = meta.drop(columns=empty_ids)
        sheets['meta'] = meta
        applied[f'{project}||{dataset}|meta columns|empty id columns removed'] = (
            'removed column(s) with no value in them. '
            + deleted_note(f'meta column {c!r} (empty)' for c in empty_ids), dataset)
    missing_columns = [c for c in wanted if c not in meta.columns]
    if missing_columns:
        meta = meta.copy()
        for column in missing_columns:
            meta[column] = None
        meta = meta[[c for c in META_COLS if c in meta.columns]
                    + [c for c in meta.columns if c not in META_COLS]]
        sheets['meta'] = meta
        applied[f'{project}||{dataset}|meta columns|missing meta columns added'] = (
            f'added {len(missing_columns)} column(s): {", ".join(missing_columns)}', dataset)
        findings.append(Finding(
            dataset, str(meta.iloc[0].get('activityName', '')), 'meta columns',
            ', '.join(missing_columns), 'every column of the DataX meta schema',
            'missing meta columns added', True, 'WARNING',
            f'{len(missing_columns)} column(s) of META_COLS were missing and are '
            f'added; the ones the model can fill are filled below.', '', project))

    # Before anything reads a group or a compartment: a misspelled one makes
    # every later check (and the .spold) misread what the exchange is.
    first_name = str(meta.iloc[0].get('activityName', '') if 'activityName' in meta.columns else '')
    findings.extend(normalise_exchange_vocabulary(sheets, dataset, first_name, project, applied))
    findings.extend(apply_wording_to_comments(sheets, dataset, first_name, project, applied))
    findings.extend(clear_production_volume_off_products(sheets, dataset, first_name, project, applied))
    findings.extend(normalise_product_names(sheets, dataset, first_name, project, applied))
    findings.extend(fix_elementary_groups(sheets, dataset, first_name, project, applied))
    # Before the meta rows: the geographyComment is written from it.
    findings.extend(fix_geographies(sheets, dataset, project, applied))
    findings.extend(drop_zero_exchanges(sheets, dataset, first_name, project, applied,
                                        answers))
    # Before the meta rows: the allocationComment is built from these prices.
    findings.extend(convert_prices_to_eur2005(sheets, dataset, first_name, project, applied))


    # The exchanges: every flow name has to exist in the master data, or the
    # .spold links to nothing.  Checked once per dataset, not per meta row.
    first_activity = str(meta.loc[meta.index[0], 'activityName']
                         if 'activityName' in meta.columns else '')
    exchanges_sheet = sheets.get('exchanges')
    # A flow renamed here ('Abamectine' -> 'Abamectin') can only have its
    # placement checked under its new name, so after any rename the check
    # runs once more -- for placements only, the names being settled.
    renamed_any = False
    for name_pass in (1, 2):
        if name_pass == 2 and not renamed_any:
            break
        pass_findings = check_exchange_names(sheets, dataset, first_activity, project)
        if name_pass == 2:
            pass_findings = [f for f in pass_findings if f[0].startswith(PLACEMENT_PREFIX)]
        for (column, found, expected, kind, severity, note, proposal,
             certain, options) in pass_findings:
            renamed_any |= _apply_exchange_finding(
                sheets, exchanges_sheet, decisions, applied, findings, dataset,
                first_activity, project, column, found, expected, kind, severity,
                note, proposal, certain, options)

    # Properties once the names are ecoinvent's, so they can be matched:
    # Master Data's first, as the standard; ecoinvent's defaults for the rest.
    findings.extend(apply_master_data_properties(sheets, dataset, first_activity,
                                                 project, applied))
    findings.extend(fill_properties_from_ecoinvent(sheets, dataset, first_activity,
                                                   project, applied))

    # The review checklist's per-exchange checks, ported from the team's two
    # review skills (checkdataset, livestock-ecoinvent-check): uncertainty,
    # own-product properties and price, Master Data units and property
    # values, duplicates, template residue, formulas, livestock structure.
    import skill_checks
    findings.extend(skill_checks.run(
        sheets, dataset, first_activity, project, applied, Finding, EXCHANGE_PREFIX,
        placement_column,
        lambda column, kind, found: (decisions.get(
            decision_signature(column, kind, found) or '') or {}).get('value'),
        _fix_rejected,
        lambda activity: derive_yield({'activityName': activity}, sheets.get('exchanges'))))

    # The metadata after the exchanges: what it says depends on them -- a
    # pesticide still under its SimaPro name ('Atrazine {RoW}| ...') is not
    # recognised, and the OLCA-Pest sentence would only follow a run later.
    new_rows = []
    for _, row in meta.iterrows():
        new_row, row_findings, renamed = process_meta_row(
            row, dataset, apply_fixes, profile, project,
            exchanges=sheets.get('exchanges'), decisions=decisions,
            properties=sheets.get('properties'))
        findings.extend(row_findings)
        if renamed:
            renames[str(row.get('activityName', ''))] = renamed
        new_rows.append(new_row)
    sheets['meta'] = pd.DataFrame(new_rows, columns=meta.columns)

    # Propagate renames to every sheet carrying an activityName -- the name is
    # the dataset's identity and must stay consistent across all of them.
    for old, new in renames.items():
        rename_activity(sheets, old, new)

    changed = bool(renames) or bool(applied) or any(f.fixed for f in findings)
    return sheets, findings, changed, applied


def write_dataset(path: str, sheets: dict, applied: dict | None = None) -> None:
    # Every sheet with its full header row, however empty.
    from config_new_datasets import complete_sheet_schema, write_spold_for
    complete_sheet_schema(sheets)
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    # Every saved workbook gets its matching .spold, whoever saved it: the
    # pipeline passes, a --commit, or the review window.
    write_spold_for(path)
    # What this save deleted -- now that it is on disk -- in the project's
    # deletion log, which the review's Deleted sheet lists.
    for key, (description, _dataset) in (applied or {}).items():
        items = deleted_items(description)
        if items:
            _project, _, after = key.partition('||')
            _ds, _, rest = after.partition('|')
            column, kind = split_key_rest(rest)
            record_deletions(path, 'meta model', column.split('.')[0], items, kind)


# ── Keeping the derived outputs in step ──────────────────────────────────────
# The .spold of a dataset is rewritten every time its workbook is saved (see
# write_dataset).  The split Excels are BUILT FROM every workbook of the
# project, so the moment a correction lands they are stale.  They are rebuilt
# from the corrected datasets rather than patched: regenerating is what the
# pipeline itself does, and a derived file edited in place would drift from
# its source.

def rebuild_splits(project: str, into: str | None = None) -> tuple:
    """
    Rewrite the per-chunk split Excels for a whole project -- into the
    project's crop_datasets_new/, or the folder `into`.

    The splits combine every dataset of the project, so they are rebuilt from
    all of them, not only the ones just corrected.
    """
    import importlib
    from config_new_datasets import AGRI_DIESEL_DIR
    sys.path.insert(0, AGRI_DIESEL_DIR)
    diesel = importlib.import_module('agri_diesel_calculator_new')

    combined: dict = {}
    for path in dataset_files(project):
        try:
            for name, frame in pd.read_excel(path, sheet_name=None).items():
                combined.setdefault(name, []).append(frame)
        except Exception:
            continue
    if 'exchanges' not in combined:
        return 0, ['no exchanges sheet found']

    sheets = {name: pd.concat(frames, ignore_index=True)
              for name, frames in combined.items() if frames}
    split_dir = into or os.path.join(project_dir_of(project), 'crop_datasets_new')
    try:
        diesel.split_crop_datasets(sheets['exchanges'], sheets, split_dir, project)
    except Exception as exc:
        return 0, [f'{exc.__class__.__name__}: {exc}']
    return len(glob.glob(os.path.join(split_dir, '*.xlsx'))), []


def report_downstream_stale(project: str, changed_paths: list) -> None:
    """
    Say which derived files the corrections have just left behind.

    The .spold files were rewritten with the workbooks; the split Excels
    combine the whole project and rerun the diesel step's splitter, so they
    are named here rather than rebuilt on every save.
    """
    if not changed_paths:
        return
    print()
    print(f"  {len(changed_paths)} workbook(s) of {project!r} were corrected, "
          f"and their .spold files rewritten.  Still out of date:")
    print(f"    the split Excel")
    print(f"  Rebuild it with the diesel step, or check with:")
    print(f"    python verify_output.py --project \"{project}\" --fix")


def refresh_downstream(project: str, changed_paths: list) -> None:
    """Rebuild the split Excels from the datasets that were just corrected."""
    if not changed_paths:
        return
    print()
    print(f"  Rebuilding the split Excels of {project!r} so they carry "
          f"the corrections:")

    parts, split_failed = rebuild_splits(project)
    if split_failed:
        print(f"    split Excels  : could not rebuild -- {split_failed[0]}")
    else:
        print(f"    split Excels  : {parts} part file(s) rewritten")


# ── The project's review workbook ───────────────────────────────────────────

def write_project_review(project: str, findings: list, corrections: list = (),
                         committed: list = (), applied: dict | None = None,
                         answers: dict | None = None, covered=None,
                         with_corrections: bool = True) -> str:
    """
    Write the project's review workbook (review_workbook.py) for the WHOLE
    project, whatever part of it was just checked.  Returns its path.

    The pipeline and the review window both work on the datasets they were
    given, but the review is the project's: written from those alone, three
    new datasets would push the other 127 out of it, answers typed for them
    included.  So the project's other datasets are re-checked, read-only, and
    their findings added.  `covered` are the dataset paths already in
    `findings` (default: every dataset a finding names).
    """
    import review_workbook
    findings = [f for f in findings if f.severity != MINOR]
    corrections = [c for c in corrections if not c.get('minor')]
    done = ({os.path.basename(p) for p in covered} if covered is not None
            else {f.dataset for f in findings})
    others = [p for p in dataset_files(project) if os.path.basename(p) not in done]
    if others:
        print(f"  Review:      re-checking the other {len(others)} dataset(s) of "
              f"{project!r} (read-only) so the review covers the whole project")
        with contextlib.redirect_stdout(io.StringIO()):
            other_corrections, other_findings = scan(project, others)
        findings += [f for f in other_findings if f.severity != MINOR]
        if with_corrections:
            corrections += [c for c in other_corrections if not c.get('minor')]
    return review_workbook.write_review(project, findings, corrections, committed,
                                        applied, answers)


# ── Library API (used by meta_model_ui.py) ──────────────────────────────────

def list_projects() -> list:
    """Project folders under output/, newest first."""
    root = os.path.join(BASE_DIR, 'output')
    if not os.path.isdir(root):
        return []
    # The model's own output folder lives here too; it is not a project.
    folders = [name for name in os.listdir(root)
               if os.path.isdir(os.path.join(root, name))
               and not name.startswith('_')
               and name != META_OUTPUT_DIRNAME]
    folders.sort(key=lambda name: os.path.getmtime(os.path.join(root, name)), reverse=True)
    return folders


def project_dir_of(project: str) -> str:
    return os.path.join(BASE_DIR, 'output', project)


def dataset_files(project: str) -> list:
    """The dataset workbooks of a project, excluding the model's own outputs."""
    from config_new_datasets import is_review_workbook
    return [p for p in sorted(glob.glob(os.path.join(project_dir_of(project), '*.xlsx')))
            if not os.path.basename(p).startswith('~')
            and 'master_data' not in os.path.basename(p)
            and not is_review_workbook(p)]


def scan(project: str, files: list | None = None) -> tuple:
    """
    Work out what would change, WITHOUT touching anything.

    Returns (corrections, findings).  Each correction is one changed cell,
    carrying the text before and after and what caused it.
    """
    paths = files if files is not None else dataset_files(project)
    corrections, findings = [], []
    for path in paths:
        try:
            original = pd.read_excel(path, sheet_name=None)
        except Exception as exc:
            findings.append(Finding(os.path.basename(path), '', 'file', str(exc), '',
                                    'could not be read', False, 'BLOCKING', '', '',
                                    project))
            continue
        sheets, dataset_findings, _changed, _applied = process_dataset(
            path, True, None, project, original)
        findings.extend(dataset_findings)
        corrections.extend(diff_corrections(original, sheets, os.path.basename(path),
                                            dataset_findings, project))

    return corrections, findings


def commit_selected(project: str, approved_keys, files: list | None = None,
                    answers: dict | None = None, rebuild_derived: bool = False) -> tuple:
    """
    Save only the corrections whose key is in approved_keys, plus any typed
    fix-sheet answers.  Returns (committed, applied, files_written).
    """
    approved = set(approved_keys or ())
    paths    = files if files is not None else dataset_files(project)
    committed, applied_all, written = [], {}, 0
    written_paths: list = []

    for path in paths:
        original = pd.read_excel(path, sheet_name=None)
        sheets, dataset_findings, _changed, applied = process_dataset(
            path, True, answers, project, original)
        dataset = os.path.basename(path)
        corrections = diff_corrections(original, sheets, dataset, dataset_findings,
                                       project, answered_columns(applied))
        kept = revert_unapproved(original, sheets, corrections,
                                 {c['key']: (c['key'] in approved) for c in corrections})
        if kept or applied:
            write_dataset(path, sheets, applied)
            written += 1
            committed.extend(kept)
            applied_all.update(applied)
            written_paths.append(path)

    if committed or applied_all:
        import review_workbook
        review_workbook.append_changes(project, committed, applied_all, answers)
    if written_paths:
        # False by default: rebuilding the split Excels reprocesses every
        # dataset of the whole project through agri_diesel_calculator_new,
        # and the UI's auto-apply calls this after every scan -- doing that
        # automatically is indistinguishable from the pipeline running again.
        # Pass rebuild_derived=True only for a one-off, deliberate rebuild.
        if rebuild_derived:
            refresh_downstream(project, written_paths)
        else:
            report_downstream_stale(project, written_paths)
        # The review window stays open between commits, so write the .spold
        # inconsistency report now rather than when the process ends.
        converter = sys.modules.get('spold_converter_new')
        if converter is not None:
            converter.flush_spold_reports()
    return committed, applied_all, written


def report_unbuilt(project: str, filenames) -> list:
    """
    Name any chosen dataset that has no workbook in output/ yet.

    Reports only: this never writes anything itself.  Use ingest_missing()
    to bring the result into output/ straight from input datasets/.
    """
    import pathlib as _pl

    names = [f for f in (filenames or []) if f]
    missing = [f for f in names
               if not os.path.isfile(os.path.join(project_dir_of(project),
                                                  _pl.Path(f).stem + '.xlsx'))]
    if missing:
        print()
        print(f'  {len(missing)} of the chosen dataset(s) have no output yet:')
        for name in missing[:5]:
            print(f'      {name}')
        if len(missing) > 5:
            print(f'      ... and {len(missing) - 5} more')
    return missing


def ingest_missing(project: str, filenames: list, overwrite: bool = False) -> list:
    """
    Bring a chosen dataset into output/ straight from input datasets/, with
    none of OLCApest, Multinutrient or Agri Diesel involved.

    overwrite=True starts the working copy afresh from its source even when
    one exists: the pipeline does this before every run of the three models,
    so they never act on their own earlier output (and every correction this
    model makes is re-applied from its fix sheet, decisions and rules).

    An input file already in DataX format (.xlsx: meta/review/exchanges/
    properties/parameters) is carried across unchanged.  A raw EcoSpold2
    (.spold) is opened with spold_reader_new.read_spold() -- the same reader
    the pipeline itself uses, kept separate from the three calculators --
    which shapes it into the same sheets without adding anything the
    calculators would have computed (pesticide emissions, nutrients, diesel).
    That enrichment is simply absent from a dataset brought in this way; the
    meta model checks metadata, it does not calculate.

    Returns the output paths written.
    """
    import pathlib as _pl

    input_dir  = os.path.join(BASE_DIR, 'input datasets', project)
    output_dir = project_dir_of(project)
    os.makedirs(output_dir, exist_ok=True)

    written = []
    for name in filenames or []:
        dest = os.path.join(output_dir, _pl.Path(name).stem + '.xlsx')
        if os.path.isfile(dest) and not overwrite:
            continue
        src = os.path.join(input_dir, name)
        if not os.path.isfile(src):
            print(f'  Cannot bring in {name!r}: not found in '
                  f'input datasets/{project}/')
            continue
        try:
            if src.lower().endswith('.spold'):
                from config_new_datasets import SPOLD_DIR
                sys.path.insert(0, SPOLD_DIR)
                import spold_reader_new as reader
                sheets = reader.read_spold(src)
            else:
                sheets = pd.read_excel(src, sheet_name=None)
                # The provider template's readme sheet and explanation rows.
                from config_new_datasets import drop_template_guidance
                drop_template_guidance(sheets, dest, 'meta model (prepare)')
            write_dataset(dest, sheets)
            written.append(dest)
        except Exception as exc:
            print(f'  Cannot bring in {name!r}: {exc.__class__.__name__}: {exc}')
    if written:
        print(f'  {"Started" if overwrite else "Brought"} {len(written)} dataset(s) '
              f'{"afresh " if overwrite else ""}into output/{project}/ directly from '
              f'input datasets/.')
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default=None,
                        help='narrow to one project under output/ '
                             '(default: every project)')
    # Writing is the default: this runs as a pipeline step, and the later
    # steps and the .spold output must see canonical names.  Pass --report-only
    # to inspect a project without changing it.
    parser.add_argument('--commit', action='store_true',
                        help='save the corrections still marked yes in the '
                             'corrections sheet, and the typed fix-sheet answers')
    parser.add_argument('--apply-all', dest='apply_all', action='store_true',
                        help='save every correction without review (skips the gate)')
    parser.add_argument('--report-only', dest='report_only', action='store_true',
                        help='findings only: write no corrections sheet')
    parser.add_argument('--report', default=None,
                        help='path for the findings report '
                             '(default: output/<project>/)')
    parser.add_argument('--batch', default=None,
                        help='pipeline mode: JSON [[project, filename], ...]')
    parser.add_argument('--no-ui', dest='no_ui', action='store_true',
                        help='skip the file chooser and run on the command line')
    parser.add_argument('--no-downstream', dest='no_downstream', action='store_true',
                        help='do not report the split Excels as out of date '
                             'after saving (the .spold is always rewritten)')
    parser.add_argument('--fresh', action='store_true',
                        help='with --batch: start each working copy afresh from '
                             'input datasets/ first (the pipeline does this before '
                             'the three models)')
    args = parser.parse_args()

    # Same convention as run_pipeline.py: bare invocation opens the
    # chooser, --no-ui runs headless.  A run driven by any flag is already
    # explicit about what it wants, so it never opens a window.
    explicit = (args.batch or args.project or args.commit or args.apply_all
                or args.report_only or args.report)
    if not (args.no_ui or explicit):
        # The pipeline's own chooser, under this step's name -- one input UI
        # for the whole project rather than a second one that drifts from it.
        picked: dict = {}

        def on_chosen(project_name, filenames=None):
            picked['project'] = project_name
            picked['files'] = filenames

        # Say so before blocking on the window.  Without this the terminal
        # stays completely silent while the chooser waits, which reads as
        # "nothing happened" if the window opens behind another one.
        print('Opening the dataset chooser...')
        print('  (a window should appear -- check behind this one if you do not see it)')
        print('  Run with --no-ui to skip it and work on the command line.')
        sys.stdout.flush()
        try:
            from project_input_ui import show_ui as choose_inputs
            # The three models' input UI, exactly as the pipeline opens it.
            # Labelled for what it does here: pick the datasets to review.
            choose_inputs(run_callback=on_chosen,
                          run_label='Choose & open the review')
        except Exception as exc:
            print(f'  Could not open the chooser ({exc.__class__.__name__}: {exc}).')
            print('  Falling back to the command line: re-run with --no-ui, or')
            print('  --project "<name>" to work on one project.')
            return 1

        if not picked.get('project'):
            print('Chooser closed without a selection. Nothing to do.')
            return 0
        print(f"  chosen: {picked['project']}"
              + (f" -- {len(picked['files'])} file(s)" if picked.get('files') else ''))

        # A dataset picked straight from input datasets/ may have no output
        # yet.  Bring it in directly -- never through the three calculators,
        # which stay a separate program -- so the review always has
        # something to check.
        missing = report_unbuilt(picked['project'], picked.get('files'))
        if missing:
            ingest_missing(picked['project'], missing)

        print('Opening the review window...')
        sys.stdout.flush()

        # Hand the choice to the review UI, which is where corrections get
        # ticked; it opens already scanned on exactly these datasets.
        from meta_model_ui import show_ui as review
        review(project=picked['project'], filenames=picked.get('files'))
        return 0
    # Nothing is saved unless asked for: a plain run PROPOSES.
    #
    # The one exception is the pipeline (--batch).  A pipeline run is meant to
    # leave finished datasets behind, and every save writes the .spold straight
    # from the workbooks -- so a step that only proposed left every comment
    # exactly as it found it, which is how "the comments never change after a
    # run" came about.  In batch mode the corrections the model decides on its
    # own are applied and logged; the BLOCKING ones are still never decided
    # here, they go to the review window that opens when the pipeline ends.
    # Set META_APPLY_IN_PIPELINE = False in the config to go back to
    # proposing only.
    args.apply = bool(args.commit or args.apply_all)
    if args.batch and not args.report_only and META_APPLY_IN_PIPELINE:
        args.apply = True
        args.apply_all = True

    # A plain run covers EVERY project: one review job over the whole output
    # tree, not one per project.  --project narrows it when you want that.
    selection: dict = {}          # project -> [dataset paths]
    if args.batch:
        # Run from the pipeline: normalise exactly the datasets in this batch.
        # Applying is the point here -- the later steps and the .spold output
        # must see canonical names.
        import json as _json
        import pathlib as _pl
        batch = _json.loads(args.batch)
        # Whichever runs first -- this model or the three calculators -- works
        # on the same working copy in output/.  A dataset with none yet is
        # brought in from input datasets/; --fresh restarts it from there.
        by_project: dict = collections.defaultdict(list)
        for batch_project, filename in batch:
            by_project[batch_project].append(filename)
        for batch_project, filenames in by_project.items():
            ingest_missing(batch_project, filenames, overwrite=args.fresh)
        for batch_project, filename in batch:
            path = os.path.join(BASE_DIR, 'output', batch_project,
                                _pl.Path(filename).stem + '.xlsx')
            if os.path.isfile(path):
                selection.setdefault(batch_project, []).append(path)
    else:
        projects = [args.project] if args.project else list_projects()
        for name in projects:
            if not os.path.isdir(project_dir_of(name)):
                print(f"  ERROR: project folder not found: {project_dir_of(name)}")
                return 1
            paths = dataset_files(name)
            if paths:
                selection[name] = paths

    if not selection:
        print("  No datasets found.")
        # Running as a pipeline step, this must not be an error.  The meta
        # model checks metadata; it produces none of the results, so failing
        # here stopped the run before the other steps and cost the three
        # models their output for the sake of a check that had nothing to
        # look at.
        return 0 if args.batch else 1

    files = [path for paths in selection.values() for path in paths]
    mode = ('COMMIT' if args.commit else
            'PIPELINE (applying the automatic corrections)'
            if args.batch and args.apply_all else
            'APPLY ALL (no review)' if args.apply_all else
            'REPORT ONLY' if args.report_only else
            'PROPOSE (nothing is saved)')
    print(f"Meta model -- {mode}")
    print(f"  projects: {len(selection)}   datasets: {len(files)}")
    for name, paths in selection.items():
        known = 'profiled' if profile_for(name) else 'NOT profiled'
        print(f"    {name:<38} {len(paths):>4} dataset(s)   {known}")
    unprofiled = [name for name in selection if not profile_for(name)]
    if unprofiled:
        print("  NOTE: only the universal rules apply to the unprofiled project(s).")
        print(f"        Profile one with: build_meta_spec.py --project "
              f"\"{unprofiled[0]}\" --write")

    # --commit saves what you reviewed, so there has to be something reviewed.
    # Without this, a first --commit on a project with no corrections sheet
    # would save everything unseen -- exactly what the gate exists to prevent.
    if args.commit:
        import review_workbook
        missing = [name for name in selection
                   if not os.path.isfile(review_workbook.review_path(name))]
        if missing and not args.batch:
            print()
            print("  ERROR: nothing has been proposed yet for: " + ', '.join(missing))
            print("         Run without --commit first, review the review workbook")
            print(f"         under {os.path.join(META_OUTPUT_DIR, missing[0])}")
            print("         then re-run with --commit. Use --apply-all to skip review.")
            return 1

    # Each project keeps its own corrections sheet and fix sheet, so load per
    # project and merge -- the keys carry the project, so they cannot collide.
    approvals: dict = {}
    answers: dict = {}
    rejected_in_sheet: dict = {}   # pipeline runs: corrections explicitly marked 'no'
    for name in selection:
        if not args.apply_all:
            approvals.update(load_approvals(name))
        elif args.batch:
            rejected_in_sheet.update({k: v for k, v in load_approvals(name).items()
                                      if not v})
        answers.update(load_fixes(name))

    rejected = sum(1 for ok in approvals.values() if not ok)
    if rejected:
        print(f"  corrections sheet(s): {rejected} correction(s) marked 'no' "
              f"will be skipped")
    if answers:
        print(f"  fix sheet(s): {len(answers)} typed answer(s) to apply")

    all_findings: list[Finding] = []
    all_applied: dict = {}
    all_corrections: list = []
    committed: list = []
    changed_files = 0
    written_by_project: dict = collections.defaultdict(list)
    for project, paths in selection.items():
        for path in paths:
            original = pd.read_excel(path, sheet_name=None)
            # Always compute the corrected state; whether it reaches disk is a
            # separate decision taken below.
            sheets, findings, _changed, applied = process_dataset(
                path, True, answers if args.apply else None, project, original)
            all_findings.extend(findings)
            all_applied.update(applied)

            dataset = os.path.basename(path)
            corrections = diff_corrections(original, sheets, dataset, findings,
                                           project, answered_columns(applied))

            if args.apply_all and rejected_in_sheet:
                # A pipeline run applies everything the model decides -- except
                # what a reviewer already said no to in the corrections sheet.
                kept = revert_unapproved(
                    original, sheets, corrections,
                    {c['key']: rejected_in_sheet.get(c['key'], True) for c in corrections})
            elif args.apply_all:
                kept = corrections
            else:
                kept = revert_unapproved(original, sheets, corrections, approvals)
            all_corrections.extend(corrections)

            if args.apply and (kept or applied):
                write_dataset(path, sheets, applied)
                changed_files += 1
                committed.extend(kept)
                written_by_project[project].append(path)

    # ── Summary ──────────────────────────────────────────────────────────────
    # MINOR is left out of every count: it is accepted, not reviewed.
    reportable = [f for f in all_findings if f.severity != MINOR]
    by_kind   = collections.Counter(f.kind for f in reportable)
    by_column = collections.Counter(f.column for f in reportable)
    fixed     = sum(1 for f in reportable if f.fixed)

    print(f"\n{len(reportable)} finding(s): {fixed} fixed, "
          f"{len(reportable) - fixed} to review")
    if by_kind:
        print("\n  by issue:")
        for kind, n in by_kind.most_common():
            print(f"    {n:>5}  {kind}")
        print("\n  by column:")
        for column, n in by_column.most_common(12):
            print(f"    {n:>5}  {column}")
    if args.apply:
        print(f"\n  {changed_files} dataset file(s) rewritten.")

    if all_applied:
        print(f"  {len(all_applied)} typed answer(s) applied from the fix sheet.")

    # Each saved workbook already has its .spold rewritten (write_dataset).
    # The split Excels are built from the whole project, so a correction
    # leaves them contradicting it.  Reported only, never rebuilt here:
    # rebuilding them reruns agri_diesel_calculator_new over the whole
    # project, which is exactly what running the pipeline itself looks like
    # -- rebuild it deliberately with the diesel step, or
    # `verify_output.py --fix`.
    if args.apply and not args.no_downstream:
        for project, paths in written_by_project.items():
            report_downstream_stale(project, paths)

    minor_count = sum(1 for c in all_corrections if c.get('minor'))
    if minor_count:
        print(f"  {minor_count} minor tidy-up(s) accepted automatically "
              f"(whitespace, missing final stop) -- not listed for review.")

    # ── The correction gate, per project ────────────────────────────────────
    # One folder holds them all, with a folder per project inside, so each
    # project's sheets stay together and a project can be reviewed on its own.
    def rows_of(project, rows, attribute='project'):
        return [row for row in rows
                if (row.get(attribute) if isinstance(row, dict)
                    else getattr(row, attribute)) == project]

    # ── What still needs you ─────────────────────────────────────────────────
    blocking = [f for f in all_findings if f.severity == 'BLOCKING']
    if blocking:
        print()
        print('=' * 78)
        print(f"BLOCKING -- {len(blocking)} finding(s) the model will not decide for you")
        print('=' * 78)
        by_issue = collections.defaultdict(list)
        for finding in blocking:
            by_issue[finding.kind].append(finding)
        for kind, group in sorted(by_issue.items(), key=lambda kv: -len(kv[1])):
            print()
            print(f"  {kind}  ({len(group)})")
            for finding in group[:5]:
                print(f"    {finding.project[:22]:<22} {finding.dataset[:40]:<40} "
                      f"{finding.column}")
                print(f"        now: {str(finding.found)[:92]}")
            if len(group) > 5:
                print(f"    ... and {len(group) - 5} more")
    else:
        print()
        print("  No BLOCKING findings.")

    # ── The review workbook, per project ────────────────────────────────────
    # One file per project: every error once, with every dataset it affects,
    # and one answer cell for all of them (review_workbook.py).  It replaces
    # the report, the corrections sheet and the fix sheet.
    import review_workbook
    print()
    for project in selection:
        # MINOR never reaches it: accepted, not reviewed.
        project_findings = [f for f in rows_of(project, all_findings)
                            if f.severity != MINOR]
        project_corrections = ([] if args.report_only else
                               [c for c in rows_of(project, all_corrections)
                                if not c.get('minor')])
        project_committed = [c for c in rows_of(project, committed) if not c.get('minor')]
        project_applied = {k: v for k, v in all_applied.items() if k.startswith(project + '||')}
        if args.batch:
            # The pipeline runs on the datasets it was given, but the review is
            # the PROJECT's: written from this run alone, three new datasets
            # would push the other 127 out of it, answers typed for them
            # included.
            if args.fresh and os.path.isfile(review_workbook.review_path(project)):
                # The prepare pass: the final pass rewrites the workbook minutes
                # from now, so only log what was saved.
                review_workbook.append_changes(project, project_committed,
                                               project_applied, answers)
                print(f"  Review:      changes logged; the final pass rewrites "
                      f"{review_workbook.review_path(project)}")
                continue
        try:
            path = write_project_review(
                project, project_findings, project_corrections, project_committed,
                project_applied, answers, covered=selection[project],
                with_corrections=not args.report_only)
        except Exception as exc:
            print(f"  WARNING: could not write the review workbook for {project!r} "
                  f"({exc.__class__.__name__}: {exc})")
            continue
        groups = review_workbook.group_findings(project_findings)
        inputs = sum(1 for g in groups if g['severity'] != 'Verify')
        print(f"  Review:      {path}")
        print(f"               {len(groups)} distinct error(s) across "
              f"{len({f.dataset for f in project_findings})} dataset(s); "
              f"{inputs} to answer in Action_list")
        if args.apply:
            print(f"               {len(project_committed)} correction(s) SAVED "
                  f"(see Changes Applied)")
        elif project_corrections:
            print(f"               {len(project_corrections)} correction(s) proposed -- "
                  f"NOTHING saved yet; set apply=no in Corrections to reject one, "
                  f"then re-run with --commit")

    print()
    print(f"  The review workbooks are in each project's folder under: {META_OUTPUT_DIR}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
