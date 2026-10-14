"""
The message to the data provider, at the top of the review's Verdict sheet.

What was corrected in the datasets and what is still needed from the data
provider, worded for them and ready to copy into an email.  It is rebuilt on
every run from the review itself -- the change log and the open Action_list
items -- so it always says where the datasets stand.

Only what the provider can act on or should know is in it: the model's own
bookkeeping (a name it cannot check for want of a profile, a figure it could
not compare) stays in the review.  A kind of finding with no wording here is
still listed, in the finding's own words, so nothing is left out unnoticed.
"""

import re

import pandas as pd

OWN_PRODUCTS = ('ReferenceProduct', 'ByProduct')
MANDATORY = ('wet mass', 'dry mass', 'water content', 'water in wet mass',
             'carbon content, fossil', 'carbon content, non-fossil')

# Open findings that are the review's own business, not the provider's.
INTERNAL_KINDS = (
    'nomenclature',                         # no profile for the crop family yet
    'comment figures not checked',
    'house wording',
    'template instruction text left in a data cell',
    'not a dataset -- skipped',
)

# Changes Applied, as the provider reads them: (words in the logged issue or
# field, the sentence).  The first match wins; each sentence is said once.
DONE = (
    (('propert', 'ecoinvent defaults'),
     'Properties of purchased inputs were completed and aligned with the ecoinvent Master Data.'),
    (('eur2005',), 'Prices were converted to EUR2005.'),
    (('zero',), 'Exchanges with a zero amount were removed.'),
    (('air', 'placement', 'compartment'),
     'Emissions were placed in the ecoinvent compartments.'),
    (('geograph',), 'Geographies were set to their ecoinvent codes.'),
    (('flow name', 'master data name', 'spelling', 'not in master data', 'product name'),
     'Flow names were corrected to their ecoinvent Master Data names.'),
    (('uncertainty', 'pedigree'),
     'Uncertainty and pedigree scores were completed following the Data Quality Guideline.'),
    (('yield', 'opening', 'normalisation', 'final stop', 'wording', 'empty --', 'sentence',
      'comment', 'period', 'dates'),
     'Metadata texts and exchange comments were harmonised to the standard wording.'),
)


def _clean(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    return str(value).strip()


def _first_sentence(text: str) -> str:
    text = _clean(text).split('\n')[0]
    match = re.match(r'(.+?[.!?])(\s|$)', text)
    return (match.group(1) if match else text)[:300]


def _join(names) -> str:
    names = [f'"{n}"' for n in names]
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def _which(datasets: list, total: int) -> str:
    if total > 1 and len(datasets) == total:
        return 'all datasets' if total > 4 else f'all {["", "one", "two", "three", "four"][total]} datasets'
    return ', '.join(datasets)


# ── What was done ───────────────────────────────────────────────────────────

def _done(history: pd.DataFrame, labels: dict, products: dict) -> list:
    lines, said = [], set()
    if history is None or history.empty:
        return lines
    rows = history.fillna('').astype(str)
    # A logged rename to a bare number was an answer misread (a price taken
    # for a name), undone since: not a correction to report.
    rows = rows[~rows['after'].str.contains(r"renamed to '[\d.,\s]+'", regex=True)]

    renamed = rows[(rows['field'] == 'activityName') & rows['after'].str.strip().ne('')]
    if not renamed.empty:
        names = sorted({labels.get(d, d) for d in renamed['dataset']})
        lines.append('Activity names now follow the ecoinvent pattern, e.g. '
                     + _join(names[:2]) + '.')
        said.add('names')

    # Own products renamed or given properties -- by an answer in the review
    # or an edit in the split Excel -- told as the datasets now have them.
    own = r'\[(?:ReferenceProduct|ByProduct)\]'
    renamed_to, filled = set(), set()
    for _, row in rows.iterrows():
        after = row['after']
        renamed_to |= set(re.findall(r'\[exchanges\] added: (.+?) ' + own, after))
        renamed_to |= set(re.findall(r"renamed to '(.+?)'", after)) \
            if row['field'].startswith('exchange:') else set()
        filled |= set(re.findall(r'\[properties\] added: (.+?) ' + own, after))
        if re.search('price missing|mandatory properties', row['issue']):
            filled.add(row['field'].split(':', 1)[-1])
    current = {n: props for d in products.values() for n, props in d.items()}
    names = sorted(n for n in renamed_to if n in current)
    if names:
        lines.append(('The product is now named ' if len(names) == 1
                      else 'The products are now named ') + _join(names) + '.')
    for name in sorted(n for n in filled if n in current):
        props, parts = current[name], []
        if all(p in props for p in MANDATORY):
            parts.append('its full set of properties')
        if 'price' in props:
            parts.append(f'a price of {props["price"]}')
        if parts:
            lines.append(f'"{name}" now has ' + ' and '.join(parts) + '.')

    other = 0
    for _, row in rows.iterrows():
        text = f'{row["issue"]} {row["field"]}'.lower()
        if 'activity name' in text or row['field'] == 'activityName' \
                or 'split excel' in text or 'price missing' in text \
                or 'mandatory properties' in text or 'could not be applied' in text:
            continue
        sentence = next((s for words, s in DONE if any(w in text for w in words)), None)
        if sentence is None:
            other += 1
        elif sentence not in said:
            said.add(sentence)
            lines.append(sentence)
    if other:
        lines.append(f'{other} further correction(s), listed in the review.')
    return lines


# ── What is still needed ────────────────────────────────────────────────────

def _period(text) -> str:
    """'2018 - 2018' -> '2018'."""
    match = re.fullmatch(r'\s*(\S+)\s*-\s*(\S+)\s*', _clean(text))
    return match.group(1) if match and match.group(1) == match.group(2) else _clean(text)


def _request(action: dict) -> str | None:
    """One action item as a request to the provider, '{where}' standing for
    its datasets; None when it is internal."""
    kind = action['issue']
    sample = action['finding']
    if any(kind.startswith(k) for k in INTERNAL_KINDS):
        return None
    where = '{where}'
    item = _clean(sample.column)
    item = f'"{item.split(":", 1)[1]}"' if item.startswith('exchange') and ':' in item else item
    if kind == 'price missing':
        return (f'{item} price ({where}): the dataset gives no price for this product, so '
                f'economic allocation cannot be calculated. Please provide a price with its '
                f'currency and year.')
    if kind == 'mandatory properties missing':
        missing = _clean(sample.found).replace('missing: ', '')
        return f'{item} properties ({where}): please provide {missing}.'
    match = re.match(r'(\w+) opening not standard', kind)
    if match:
        field = {'includedActivitiesStart': 'Included activities start',
                 'includedActivitiesEnd': 'Included activities end'}.get(match.group(1),
                                                                            match.group(1))
        example = ('"This activity starts after the harvest of the previous crop…"'
                   if 'Start' in match.group(1) else '"This activity ends with …"')
        return (f'"{field}" ({where}): this field should open in the standard way, e.g. '
                f'{example}. Please send a revised text.')
    if kind.startswith('the dates do not cover'):
        return (f'Time period ({where}): the dataset dates cover {_clean(sample.found)}, but '
                f'the general comment describes a different production period. Which period '
                f'do the data represent?')
    if kind == 'production volume source outside the dataset period':
        return (f'Production volume ({where}): it refers to {_period(sample.found)}, while the '
                f'dataset covers {_period(sample.expected)}. Do you have a more recent figure? '
                f'Otherwise, please confirm that it is the best available.')
    if kind == 'yield line missing':
        return f'Yield ({where}): please give the yield in kg/ha.'
    if kind == 'amount contradicts its comment':
        return (f'{item} ({where}): {_first_sentence(sample.note)} Please correct the amount '
                f'or the comment.')
    return f'{item} ({where}): {_first_sentence(sample.note) or kind}.'


def _requests(actions: list, total: int) -> list:
    # The same request for several Action_list rows (one issue, different
    # current texts) is asked once, for all their datasets together.
    asked: dict = {}
    products = [a for a in actions if a['issue'] == 'product name not an ecoinvent product']
    for action in actions:
        if action in products:
            continue
        line = _request(action)
        if line:
            asked.setdefault(line, [])
            asked[line] += [d for d in action['datasets'] if d not in asked[line]]
    lines = [line.replace('{where}', _which(sorted(datasets), total))
             for line, datasets in asked.items()]
    if products:
        names = [_clean(a['finding'].column).split(':', 1)[-1] for a in products]
        lines.append(f'Product names: {_join(names)} '
                     f'{"is not an existing ecoinvent product" if len(names) == 1 else "are not existing ecoinvent products"}. '
                     f'Please confirm they should be created as new products, or tell us which '
                     f'existing product they correspond to.')
    return lines


# ── The message ─────────────────────────────────────────────────────────────

def own_products(sheets: dict) -> dict:
    """{product name: {property: 'amount unit'}} of a dataset's own products."""
    exchanges, properties = sheets.get('exchanges'), sheets.get('properties')
    if exchanges is None or 'group' not in exchanges.columns:
        return {}
    names = [_clean(n) for n in exchanges.loc[exchanges['group'].isin(OWN_PRODUCTS), 'name']]
    found = {n: {} for n in names if n}
    if properties is not None and {'name', 'propertyName'} <= set(properties.columns):
        for _, p in properties.iterrows():
            name = _clean(p.get('name'))
            if name in found and _clean(p.get('amount')):
                try:
                    amount = f'{float(p.get("amount")):g}'
                except (TypeError, ValueError):
                    amount = _clean(p.get('amount'))
                found[name][_clean(p.get('propertyName')).lower()] = \
                    f'{amount} {_clean(p.get("unitName"))}'.strip()
    return found


def lines(project: str, actions: list, history: pd.DataFrame, labels: dict,
          products: dict) -> list:
    """The message, one paragraph per line, for the top of the Verdict sheet.

    actions   the Action_list rows: {'issue', 'severity', 'datasets' (names),
              'finding' (a member finding)}
    history   the change log (Changes Applied)
    labels    {dataset workbook: its name in the review}
    products  {dataset workbook: own_products(...)}
    """
    names = sorted(labels.values())
    total = len(names)
    done = _done(history, labels, products)
    needed = _requests(actions, total)
    out = [f'Subject: {project} datasets: corrections applied and open points', '',
           'Dear [Name],', '',
           f'We have reviewed the {project} datasets ({", ".join(names)}). Below is a summary '
           + ('of what we corrected and what we still need from you.' if needed
              else 'of what we corrected.')]
    if done:
        out += ['', 'Corrections applied'] + [f'- {line}' for line in done]
    if needed:
        out += ['', 'Still needed from you'] + [f'{i}. {line}' for i, line in enumerate(needed, 1)]
        out += ['', 'Once we have these points, we can finalise the datasets.']
    else:
        out += ['', 'Nothing further is needed from you: the datasets are ready to be finalised.']
    out += ['', 'Best regards,', '[Your name]']
    return out
