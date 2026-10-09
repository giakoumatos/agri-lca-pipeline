"""
Meta Profile Builder  (maintenance script -- NOT part of the pipeline)

Induces meta_profile.py: everything about a project's `meta` sheet that the
universal rules in meta_spec.py cannot know by themselves.

    python meta_model/build_meta_spec.py                 # preview
    python meta_model/build_meta_spec.py --write         # write it
    python meta_model/build_meta_spec.py --project "X"   # any project

What is induced, per project:

  * its crop families, taken from the first segment of each activityName --
    no list of crops is hardcoded anywhere, so a project of tomatoes and
    sunflowers profiles exactly as well as one of grapes and olives;
  * for each family, how many segments its names carry, whether the last one
    is optional (present on some datasets, absent on others -- typically a
    region, absent on the national average), and the vocabulary observed at
    each position;
  * the ISIC code each family uses;
  * the value of each column that is constant across the project;
  * which columns are empty throughout;
  * the house wording of every prose column.

Wording and values are taken by MAJORITY, not by first sight: where most of a
family says "0121:Growing of grapes" and four say "0121: Growing of Grapes",
the majority is the standard and the rest are the deviation. A wording only
one dataset uses is not made a template at all -- a lone variant is not a
standard, it is the thing worth looking at.

The reference project should be one whose meta has been reviewed: whatever it
contains becomes the standard later datasets are measured against.

Author: Andreas Giakoumatos
Last Updated: 2026-09-11
"""

import os
import re
import sys
import glob
import difflib
import argparse
import collections

import pandas as pd

# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_new_datasets import INPUT_PROJECT_FOLDER, BASE_DIR
import meta_spec as spec

PROFILE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'meta_profile.py')

# Values at least this similar are treated as the same wording.  Kept high on
# purpose: merging genuinely different phrasings ("the reference technology IS
# evaluated" / "WAS evaluated using literature data") into one template would
# turn their difference into a slot and accept anything in its place.
_CLUSTER_THRESHOLD = 0.90

# How many datasets must share a wording before it counts as house style.
_MIN_CLUSTER_SUPPORT = 2

# An induced template must keep at least this share of its tokens fixed,
# otherwise the cluster was too loose to describe a house style.
_MIN_FIXED_SHARE = 0.45

# An axis is treated as an open identifier rather than a controlled vocabulary
# when at least this share of the family's datasets carry a distinct value.
_OPEN_AXIS_SHARE = 0.5


# ── Text template induction ───────────────────────────────────────────────────

def _tokens(text: str) -> list:
    return re.findall(r'\S+', str(text))


def cluster(values: list) -> list:
    """Greedily group values by similarity; each group becomes one template."""
    groups: list = []
    for value in values:
        for group in groups:
            if difflib.SequenceMatcher(None, group[0], value,
                                       autojunk=False).ratio() >= _CLUSTER_THRESHOLD:
                group.append(value)
                break
        else:
            groups.append([value])
    return groups


def induce(values: list) -> tuple:
    """
    Return (template, fixed_share) for a group of near-identical strings.

    Tokens present in every value stay fixed; runs that differ collapse into a
    single {n} slot.
    """
    token_lists = [_tokens(v) for v in values if str(v).strip()]
    if not token_lists:
        return '', 0.0

    skeleton = token_lists[0]
    keep = [True] * len(skeleton)
    # A slot is needed not only where the skeleton's own tokens differ between
    # members, but also wherever a member INSERTS text the skeleton does not
    # have -- a comment naming the grape variety against one that does not.
    # Without this the template has no slot there and can never match the
    # longer wording.
    gap = [False] * (len(skeleton) + 1)

    for other in token_lists[1:]:
        matched = [False] * len(skeleton)
        for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(
                None, skeleton, other, autojunk=False).get_opcodes():
            if tag == 'equal':
                for i in range(i1, i2):
                    matched[i] = True
            elif tag == 'insert':
                gap[i1] = True
        keep = [k and m for k, m in zip(keep, matched)]

    out, run = [], []
    slot_no = 0

    def flush_run():
        if run:
            out.append(' '.join(run))
            run.clear()

    def add_slot():
        nonlocal slot_no
        if out and out[-1].endswith('}'):
            return
        slot_no += 1
        out.append('{%d}' % slot_no)

    for index, token in enumerate(skeleton):
        if gap[index]:
            flush_run()
            add_slot()
        if keep[index]:
            run.append(token)
        else:
            flush_run()
            add_slot()
    if gap[len(skeleton)]:
        flush_run()
        add_slot()
    flush_run()

    template    = ' '.join(out)
    fixed_share = (sum(1 for k in keep if k) / len(keep)) if keep else 0.0
    return template, fixed_share


# ── Reading the project ───────────────────────────────────────────────────────

def project_files(project_dir: str) -> list:
    from config_new_datasets import is_review_workbook
    return [p for p in sorted(glob.glob(os.path.join(project_dir, '*.xlsx')))
            if not os.path.basename(p).startswith('~')
            and 'master_data' not in os.path.basename(p)
            and 'meta_model_report' not in os.path.basename(p)
            and not is_review_workbook(p)]


def collect(project_dir: str) -> tuple:
    """Read every dataset's meta rows.  Returns (all_rows, by_family, n_files)."""
    files = project_files(project_dir)
    all_rows, by_family = [], collections.defaultdict(list)
    for path in files:
        try:
            meta = pd.read_excel(path, sheet_name='meta')
        except Exception:
            continue
        for _, row in meta.iterrows():
            segments = spec.canonical_segments(row.get('activityName'))
            if not segments:
                continue
            all_rows.append(row)
            by_family[segments[0]].append((row, segments))
    return all_rows, by_family, len(files)


def _clean(value) -> str:
    if value is None:
        return ''
    text = str(value)
    return '' if text.strip().lower() in ('nan', 'none') else text.strip()


def induce_name_shape(entries: list) -> dict:
    """
    Work out the shape of one family's activityNames.

    fixed_segments  how many segments every name of this family carries
    optional_last   whether some names carry one more (a region, say, absent
                    on a national average)
    vocabulary      the values seen at each fixed position
    optional_vocab  the values seen in the optional trailing position
    """
    counts = collections.Counter(len(segments) - 1 for _, segments in entries)
    fixed  = min(counts)
    longest = max(counts)
    optional_last = longest == fixed + 1

    vocabulary = [set() for _ in range(fixed)]
    optional_vocab = set()
    for _row, segments in entries:
        axes = segments[1:]
        body = axes[:fixed] if (optional_last and len(axes) > fixed) else axes[:fixed]
        for index, value in enumerate(body):
            if index < fixed:
                vocabulary[index].add(value)
        if len(axes) > fixed:
            optional_vocab.add(axes[-1])

    # An axis whose values are nearly all distinct is an open identifier (the
    # grape variety: 43 values across 43 datasets), not a controlled
    # vocabulary. New values there are normal and must not be reported; an
    # axis with few repeated values is controlled, and a new value IS worth
    # reporting.
    n = len(entries)
    open_axes = [len(v) >= max(2, _OPEN_AXIS_SHARE * n) for v in vocabulary]

    # Name each axis from what it holds, so the generator can find the
    # production system and water regime without per-project slot labels.
    axis_labels = [spec.label_axis(v) for v in vocabulary]
    optional_label = spec.label_axis(optional_vocab) or ('region' if optional_vocab else None)

    return {
        'segment_counts': sorted(counts),
        'fixed_segments': fixed,
        'optional_last':  optional_last,
        'vocabulary':     [sorted(v) for v in vocabulary],
        'open_axes':      open_axes,
        'axis_labels':    axis_labels,
        'optional_label': optional_label,
        'optional_vocabulary': sorted(optional_vocab),
        'irregular': sorted(c for c in counts if c not in (fixed, fixed + 1)),
    }


def majority_value(values: list) -> tuple:
    """Return (value, share) of the most common non-empty value."""
    cleaned = [v for v in (_clean(x) for x in values) if v]
    if not cleaned:
        return None, 0.0
    value, n = collections.Counter(cleaned).most_common(1)[0]
    return value, n / len(cleaned)


def build(all_rows: list, by_family: dict) -> tuple:
    """Induce the whole profile.  Returns (profile, warnings)."""
    warnings = []
    frame = pd.DataFrame(all_rows)

    # Columns constant across the project
    constants = {}
    for column in spec.CONSTANT_CANDIDATES:
        if column not in frame.columns:
            continue
        value, share = majority_value(frame[column].tolist())
        if value is not None and share >= spec.MAJORITY_SHARE:
            constants[column] = value
        elif value is not None:
            warnings.append(f'{column}: no value reaches {spec.MAJORITY_SHARE:.0%} '
                            f'(most common {value!r} at {share:.0%}) -- not treated as constant')

    # Columns empty throughout
    always_empty = [c for c in spec.EMPTY_CANDIDATES
                    if c in frame.columns and not any(_clean(v) for v in frame[c])]

    families = {}
    for family, entries in sorted(by_family.items()):
        rows = [row for row, _ in entries]
        family_frame = pd.DataFrame(rows)
        shape = induce_name_shape(entries)
        if shape['irregular']:
            warnings.append(f'{family}: activityNames with {shape["irregular"]} axes do not fit '
                            f'the {shape["fixed_segments"]}(+1) shape of the rest')

        isic, isic_share = majority_value(family_frame.get('ISIC', pd.Series(dtype=str)).tolist())
        if isic and isic_share < spec.MAJORITY_SHARE:
            warnings.append(f'{family}: ISIC {isic!r} holds for only {isic_share:.0%} '
                            f'of the family -- check before trusting it')
            isic = None

        templates = {}
        for column in spec.TEMPLATE_COLUMNS:
            if column not in family_frame.columns:
                continue
            values = [spec.canonical_column_text(column, v)
                      for v in family_frame[column].dropna() if str(v).strip()]
            if not values:
                continue
            column_templates = []
            for group in cluster(values):
                if len(group) < _MIN_CLUSTER_SUPPORT:
                    warnings.append(f'{family} / {column}: 1 dataset uses a wording of its '
                                    f'own -- not made a template, it will be reported')
                    continue
                template, fixed_share = induce(group)
                if not template:
                    continue
                if fixed_share < _MIN_FIXED_SHARE:
                    warnings.append(f'{family} / {column}: a cluster of {len(group)} value(s) '
                                    f'kept only {fixed_share:.0%} of its tokens fixed -- '
                                    f'not turned into a template')
                    continue
                column_templates.append(template)
            if column_templates:
                templates[column] = column_templates

        families[family] = {
            'datasets':  len(entries),
            'isic':      isic,
            'templates': templates,
            **{k: v for k, v in shape.items() if k != 'irregular'},
        }

    # Cross-family pass: regions are not a fixed vocabulary anyone can list,
    # but a project names them consistently.  Whatever appears as the optional
    # trailing axis of one family is this project's region vocabulary, so an
    # unlabelled axis drawn entirely from it is a region too -- that is how
    # grape's region gets found, where it is a fixed axis rather than an
    # optional one.
    regions = set()
    for info in families.values():
        if info.get('optional_label') == 'region':
            regions.update(info.get('optional_vocabulary') or [])
    if regions:
        for info in families.values():
            for index, label in enumerate(info['axis_labels']):
                if label is None and not info['open_axes'][index]:
                    values = set(info['vocabulary'][index])
                    if values and values <= regions:
                        info['axis_labels'][index] = 'region'

    return {'constants': constants, 'always_empty': always_empty,
            'families': families, 'regions': sorted(regions)}, warnings


def existing_profiles() -> dict:
    """
    The profiles already recorded, so profiling one project does not erase the
    others.  This is what lets a single run judge every project at once.
    """
    try:
        import importlib
        import meta_profile
        importlib.reload(meta_profile)
        return dict(getattr(meta_profile, 'PROFILES', {}) or {})
    except Exception:
        return {}


def render(profiles: dict) -> str:
    """Render the whole PROFILES table -- every project, not just this one."""
    out = [
        '"""',
        'Meta profiles -- GENERATED, do not edit by hand.',
        '',
        "Everything about each project's `meta` sheet that the universal rules in",
        'meta_spec.py cannot know by themselves: its crop families, the shape and',
        "vocabulary of each family's activityName, the ISIC code per family, the",
        'constant column values, and the house wording of each prose column.',
        '',
        'Keyed by project, so one run can judge every project at once and',
        'profiling a new project leaves the others intact.',
        '',
        'A {n} slot in a template is text the dataset supplies itself (crop noun,',
        'cultivation system, region, yield); everything else is fixed house style.',
        '',
        'Written by meta_model/build_meta_spec.py -- re-run it with',
        '--write for a new project, or when a house style changes.',
        '"""',
        '',
        'PROFILES = {',
    ]
    for project, profile in sorted(profiles.items()):
        out.append(f'    {project!r}: {{')
        out.append(f'        {"datasets"!r}: {profile.get("datasets", 0)},')
        out.append(f'        {"constants"!r}: {{')
        for column, value in sorted((profile.get('constants') or {}).items()):
            out.append(f'            {column!r}: {value!r},')
        out.append('        },')
        out.append(f'        {"always_empty"!r}: (')
        for column in profile.get('always_empty') or ():
            out.append(f'            {column!r},')
        out.append('        ),')
        out.append(f'        {"regions"!r}: {tuple(profile.get("regions") or ())!r},')
        out.append(f'        {"families"!r}: {{')
        for family, info in sorted((profile.get('families') or {}).items()):
            out.append(f'            {family!r}: {{')
            for field in ('datasets', 'isic', 'fixed_segments', 'optional_last',
                          'segment_counts', 'open_axes', 'axis_labels',
                          'optional_label', 'optional_vocabulary'):
                out.append(f'                {field!r}: {info.get(field)!r},')
            out.append(f'                {"vocabulary"!r}: [')
            for values in info.get('vocabulary') or []:
                out.append(f'                    {values!r},')
            out.append('                ],')
            out.append(f'                {"templates"!r}: {{')
            for column, items in sorted((info.get('templates') or {}).items()):
                out.append(f'                    {column!r}: [')
                for template in items:
                    out.append(f'                        {template!r},')
                out.append('                    ],')
            out.append('                },')
            out.append('            },')
        out.append('        },')
        out.append('    },')
    out.append('}')
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default=INPUT_PROJECT_FOLDER)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()

    project_dir = os.path.join(BASE_DIR, 'output', args.project)
    if not os.path.isdir(project_dir):
        print(f"  ERROR: reference project not found: {project_dir}")
        return 1

    print(f"1. Reading reference project {args.project!r}...")
    all_rows, by_family, n_files = collect(project_dir)
    if not all_rows:
        print("   No meta rows found.")
        return 1
    print(f"   {n_files} dataset(s), {len(by_family)} crop family(ies).")

    print("\n2. Inducing the profile...")
    profile, warnings = build(all_rows, by_family)
    print(f"   constants   : {profile['constants']}")
    print(f"   always empty: {len(profile['always_empty'])} column(s)")
    n_templates = sum(len(v) for f in profile['families'].values()
                      for v in f['templates'].values())
    print(f"   families    : {len(profile['families'])}, {n_templates} template(s)")
    for family, info in sorted(profile['families'].items()):
        shape = f'{info["fixed_segments"]}{"(+1)" if info["optional_last"] else ""} axes'
        print(f'     {family:<32} {info["datasets"]:>4} datasets  {shape:<10} '
              f'ISIC={str(info["isic"])[:34]!r}')
    for warning in warnings[:12]:
        print(f'   WARNING: {warning}')
    if len(warnings) > 12:
        print(f'   ... and {len(warnings) - 12} more warning(s)')

    profiles = existing_profiles()
    was_known = args.project in profiles
    profile["datasets"] = n_files
    profiles[args.project] = profile
    text = render(profiles)

    others = [name for name in profiles if name != args.project]
    print(f"   {'updated' if was_known else 'added'} profile for {args.project!r}; "
          f"{len(others)} other project(s) kept: {others}")

    if not args.write:
        print(f"   ({len(text.splitlines())} lines)  Re-run with --write to save "
              f"{os.path.basename(PROFILE_FILE)}.")
        return 0

    open(PROFILE_FILE, 'w', encoding='utf-8').write(text)
    print(f"\n3. Written: {PROFILE_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
