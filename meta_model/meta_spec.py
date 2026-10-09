"""
Meta Model -- UNIVERSAL specification

The rules that hold for any project, any crop, any country:

  * the canonical text form (whitespace, punctuation, final stop)
  * the locked yield-line format
  * how an activityName is normalised (lowercase, comma-separated, single
    spaced) -- the only grounds on which a dataset is ever renamed
  * which meta columns hold prose, which are expected constant, which are
    expected empty

What each project fills those rules with -- its crop families, the segments
each family's name carries, its region names, the ISIC code per family, the
value of each constant column, the house wording of every prose column -- is
NOT here. That is induced per project into meta_profile.py by
build_meta_spec.py, because hardcoding it made the model usable on exactly one
project and made every dataset of any other read as an unknown crop family.

Author: Andreas Giakoumatos
Last Updated: 2026-09-11
"""

import re


# ── Canonical text form ───────────────────────────────────────────────────────
# Part of the spec, not a convenience: it defines what "the same text" means.
# Templates are induced from tidied text and dataset values are tidied before
# being matched, so both sides are compared in the same form.

def tidy_text(value) -> str:
    """No double spaces, no space before punctuation, no trailing blanks."""
    text = re.sub(r'[ \t]{2,}', ' ', str(value))
    text = re.sub(r'[ \t]+([.,;:])', r'\1', text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    return text.strip()


def ends_with_stop(value) -> bool:
    """A prose column ends in a full stop."""
    return str(value).rstrip().endswith(('.', '!', '?'))


# ── The yield line (LOCKED FORMAT) ───────────────────────────────────────────
# generalComment must carry the crop yield as, exactly:
#
#     Yield: [26299.94] kg/ha        Yield: [9000.00] kg/ha
#
# Square brackets, the short unit kg/ha, a period separator and no thousands
# separator.  Always two decimal places, a whole number included -- 9000.00,
# not 9000 (the review skill's format).  This is a locked
# rule, not a style preference: downstream code reads the figure back out of
# the prose, so its shape is part of the data contract.  A dataset with no
# yield line at all is BLOCKING -- the figure cannot be invented.

YIELD_UNIT      = 'kg/ha'
YIELD_TEMPLATE  = 'Yield: [{value}] ' + YIELD_UNIT

# The locked form, and the loose form used to find a yield line however written.
YIELD_LOCKED_RE = re.compile(r'Yield:\s\[\d+\.\d{2}\]\skg/ha')
# Finds a yield DECLARATION however it is worded.  Between the word "yield"
# and the figure there may be anything short of a sentence end or another
# number -- "Yield:", "Yield of the dataset =", "yield is", "Yield -" -- so
# that gap is matched loosely, but it cannot cross a full stop (which would
# let it read a figure out of the next sentence) nor another digit (which
# would skip past the real one).  The value allows an exponent, because some
# datasets write the yield as "2.27E+03".
YIELD_ANY_RE = re.compile(
    r'[Yy]ield\b[^.\d]{0,40}?\[?\s*(?P<value>[\d][\d.,]*(?:[eE][+-]?\d+)?)\s*\]?\s*'
    r'(?P<unit>kg/ha|kg\s+per\s+hectare|t/ha|tonnes?/ha|tons?\s+per\s+hectare)',
    re.IGNORECASE,
)

# A comment may state more than one yield -- "the dry matter yield is 12,582
# kg/ha. Applying a 60% utilization factor, the effective yield considered in
# the dataset is 7,549 kg/ha." -- and the locked line must carry the one the
# dataset USES.  A declaration whose sentence calls it effective, or the one
# considered / used / applied / adopted / modelled in the dataset, is that
# yield; otherwise the first one, as always.
_YIELD_IN_USE_RE = re.compile(
    r'\b(?:effective|considered|used|applied|adopted|modell?ed)\b', re.IGNORECASE)


def find_yield(text):
    """The declaration of the yield the dataset uses (a YIELD_ANY_RE match), or None."""
    body = str(text or '')
    matches = list(YIELD_ANY_RE.finditer(body))
    for match in matches[1:] if len(matches) > 1 else ():
        sentence_start = max(body.rfind('. ', 0, match.start()),
                             body.rfind('\n', 0, match.start())) + 1
        if _YIELD_IN_USE_RE.search(body[sentence_start:match.start('value')]):
            return match
    return matches[0] if matches else None


# How many words may precede "yield" inside its sentence and still count as
# part of the declaration ("Annual production-average yield is ...").  More
# than this and the sentence carries other content, so only the declaration
# itself is replaced and the rest is left alone.
_YIELD_PREFIX_WORDS = 5

# Units that are the locked unit under another spelling; anything else (t/ha)
# is a real unit difference -- see convert_yield_unit() below for the ones
# that convert by a fixed, universal factor.
_YIELD_SAME_UNIT = ('kg/ha', 'kg per hectare')


THOUSANDS_COMMA_RE = re.compile(r'^[1-9]\d{0,2}(?:,\d{3})+$')


def parse_number_loose(text) -> float | None:
    """
    Read a number written in English or European convention.

    English  "26,299.94" -- last separator is the dot   -> drop commas
    European "17.089,84" -- last separator is the comma -> drop dots, comma->dot
    Plain    "26299.94" / "26299,94" / "26300"
    Typo     "2734.63.00" -- take the first valid number

    Mirrors the reader in agri_diesel_calculator_new.py, which parses the same
    figure back out of the prose; the two must agree or normalising the text
    would change the value the diesel step reads.
    """
    if text is None:
        return None
    # Tolerate the wrappers messy input arrives in: brackets, spaces, a unit.
    s = str(text).strip().strip('[]()').strip()
    s = re.sub(r'(?i)\s*(kg/ha|kg\s+per\s+hectare)\s*$', '', s).strip()
    if not s:
        return None
    # A comma followed by groups of exactly three digits is a THOUSANDS
    # separator: "12,582" is 12582, "1,900" is 1900 -- not 12.582 and 1.9.
    # A first group of 0 ("0,125") or any other grouping ("8604,16") is a
    # decimal comma, as before.
    if THOUSANDS_COMMA_RE.match(s):
        return float(s.replace(',', ''))
    last_dot, last_comma = s.rfind('.'), s.rfind(',')
    try:
        if last_dot > last_comma:
            return float(s.replace(',', ''))
        if last_comma > last_dot:
            return float(s.replace('.', '').replace(',', '.'))
        return float(s)
    except ValueError:
        match = re.match(r'(\d+(?:[.,]\d+)?)', s)
        return parse_number_loose(match.group(1)) if match else None


def format_yield(value) -> str | None:
    """
    Normalise a yield figure to the locked form: period separator, no
    thousands separator, always two decimals.
    '17.089,84' -> '17089.84', 9000 -> '9000.00'.  Returns None when no number
    can be read.

    The formatting itself is delegated to generate_metadata.format_yield(),
    which is THE locked formatter shared with the review skill, so both
    produce byte-identical yield lines.  What is added here is tolerance of
    the shapes found in real datasets that would make that function raise:
    surrounding brackets, a trailing unit, a doubled decimal point, and
    scientific notation.
    """
    number = value if isinstance(value, (int, float)) else parse_number_loose(value)
    if number is None:
        return None
    from generate_metadata import format_yield as locked_format_yield
    return locked_format_yield(number)


def yield_line_status(text) -> tuple[str, str | None, str | None]:
    """
    Inspect the yield line of a generalComment.

    Returns (status, canonical_line, detail):
      'ok'          already in the locked form
      'reformat'    a figure was found in another shape; canonical_line is it
      'unit'        stated in a different unit (t/ha) -- needs a human
      'missing'     no yield line at all
    """
    body = str(text or '')
    match = find_yield(body)
    if not match:
        # Show whatever stands in for the yield -- an unfilled placeholder
        # ("Yield : [VALORE] kg/ha") is far more use in a report than a bare
        # "not found".
        leftover = re.search(r'[Yy]ield[^.]{0,60}', body)
        return 'missing', None, (leftover.group(0).strip() if leftover else None)

    unit = ' '.join(match.group('unit').lower().split())
    if unit not in _YIELD_SAME_UNIT:
        return 'unit', None, match.group(0).strip()

    formatted = format_yield(match.group('value'))
    if formatted is None:
        return 'missing', None, match.group(0).strip()

    canonical = YIELD_TEMPLATE.format(value=formatted)
    found     = match.group(0).strip()
    if found == canonical and YIELD_LOCKED_RE.search(body):
        return 'ok', canonical, found
    return 'reformat', canonical, found


# ── Validity period: the dates and the prose must agree ─────────────────────
# startDate/endDate and the years named in timePeriodComment describe the same
# thing, so they are checked against each other in both directions.  An
# endDate later than the current year is not a disagreement but an error --
# a dataset cannot be valid for a period that has not happened -- so where the
# prose gives a usable year, the date is corrected from it.  Where both
# readings are plausible and merely differ, neither is assumed right: the
# difference is reported with the prose's years offered.

def current_year() -> int:
    from datetime import datetime
    return datetime.now().year


def year_of(value):
    """The year in a date cell or a date-like string, or None."""
    match = re.search(r'(?:19|20)\d{2}', str(value or ''))
    return int(match.group(0)) if match else None


def set_year(value, year: int) -> str:
    """
    Put `year` into a date value, keeping the rest of it.

    "2029-12-31 00:00:00" with 2020 gives "2020-12-31" -- the month and day
    are the period's end, not something to re-guess.
    """
    text = str(value or '').strip()
    match = re.search(r'(?:19|20)\d{2}(-\d{2}-\d{2})', text)
    if match:
        return f'{year}{match.group(1)}'
    return f'{year}-12-31'


def agricultural_seasons(start_value, end_value):
    """
    How many agricultural seasons the dates span, or None.

    Counted from the length of the period, not from the calendar years it
    touches: 1 August 2021 to 1 August 2022 is ONE season, though it names two
    years, and 15 May 2023 to 15 May 2026 is three.  2015-01-01 to 2020-12-31
    is still six.  Dates with no day and month fall back to the years.
    """
    import pandas as pd
    start = pd.to_datetime(str(start_value or ''), errors='coerce')
    end = pd.to_datetime(str(end_value or ''), errors='coerce')
    if pd.isna(start) or pd.isna(end):
        first, last = year_of(start_value), year_of(end_value)
        return (last - first + 1) if first and last and last >= first else None
    if end < start:
        return None
    return max(1, round((end - start).days / 365.25))


_NUMBER_VALUES = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
                  'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
_AVERAGE_RE = re.compile(
    r'\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)[- ]years?[- ]'
    r'(?:average|mean|production period|period|cycle)\b'
    r'|\baverage (?:of|over) (?:the )?(?:past |last )?(\d{1,2}|one|two|three|four|five|six|'
    r'seven|eight|nine|ten) years\b', re.I)


def stated_average_years(general_comment):
    """
    The averaging period the generalComment states ("a 5-year average", "over
    the entire 5-year production period", "the average of the past six
    years"), in years, or None.  Template guidance in [brackets] is ignored:
    an unfilled template mentions periods that describe nothing.
    """
    text = re.sub(r'\[[^\]]*\]', ' ', str(general_comment or ''))
    for match in _AVERAGE_RE.finditer(text):
        word = (match.group(1) or match.group(2) or '').lower()
        years = int(word) if word.isdigit() else _NUMBER_VALUES.get(word)
        if years and years > 1:
            return years
    return None


def period_years(text) -> tuple:
    """The first and last year named in a timePeriodComment, or (None, None)."""
    years = [int(y) for y in re.findall(r'(?:19|20)\d{2}', str(text or ''))]
    return (min(years), max(years)) if years else (None, None)


def set_period_years(text, start, end) -> str:
    """
    Rewrite the years a timePeriodComment names, keeping its wording.

    Only the year tokens are touched, so "The data refer to the period 2010 -
    2015 and ..." keeps every word and just changes the two numbers.  The
    earliest year becomes `start` and the latest `end`; a comment naming one
    year only gets `end` when the period is a single year, else the range.
    """
    body = str(text or '')
    found = [int(y) for y in re.findall(r'(?:19|20)\d{2}', body)]
    if not found:
        return body
    lowest, highest = min(found), max(found)
    if lowest == highest and start != end:
        # One year stands for the whole period: widen it to the real range.
        return re.sub(r'(?:19|20)\d{2}', f'{start} - {end}', body, count=1)

    def swap(match):
        year = int(match.group(0))
        if year == lowest:
            return str(start)
        if year == highest:
            return str(end)
        return match.group(0)

    return re.sub(r'(?:19|20)\d{2}', swap, body)


# ── Pesticide emissions in extrapolations (LOCKED SENTENCE) ─────────────────
# How pesticide emissions were calculated is not a per-dataset choice: this
# pipeline computes them with OLCA-Pest (step 1), so the statement is the same
# everywhere and is stated the same way.  Any sentence that says how pesticide
# emissions were arrived at is replaced by it -- the datasets variously credit
# "Zampori and Pant", "Zampori et al" and three different phrasings -- and
# where no such sentence exists, it is added.
#
# Only sentences about pesticide EMISSIONS are touched.  A sentence that
# merely mentions pesticides for another reason -- where the quantities came
# from, what the data represent, packaging end-of-life -- says nothing about
# the calculation and is left exactly as it is.

EXTRAPOLATIONS_COLUMN = 'modellingAndValidation_extrapolations'

PESTICIDE_EMISSION_SENTENCE = (
    'The emissions due to pesticides use are calculated according to the '
    'OLCA-Pest model (Nemecek et al. 2022).'
)

_PESTICIDE_WORDS = re.compile(
    r'pesticid|plant\s+protection|phytosanitar|herbicid|fungicid|insecticid|cryptogam',
    re.IGNORECASE,
)
_EMISSION_WORD = re.compile(r'emission', re.IGNORECASE)


# Periods that do not end a sentence.  "(Nemecek et al. 2022)" is one
# sentence, and splitting it at "al." tore the locked sentence in half.
_ABBREVIATIONS = ('al', 'etc', 'e.g', 'i.e', 'cf', 'vs', 'approx', 'fig', 'no')


def split_sentences(text) -> list:
    """
    Split prose into sentences, keeping each one's own full stop.

    A period only ends a sentence when what follows starts like a new one --
    a capital or an opening quote.  That alone keeps "et al. 2022" together,
    since a digit follows; the abbreviation list covers "e.g. Something".
    """
    body = ' '.join(str(text or '').split())
    if not body:
        return []

    parts, start = [], 0
    for match in re.finditer(r'\.\s+', body):
        following = body[match.end():match.end() + 1]
        if not (following.isupper() or following in ('"', '“')):
            continue
        preceding = body[start:match.start()].split()
        if preceding and preceding[-1].lower().strip('(,"') in _ABBREVIATIONS:
            continue
        parts.append(body[start:match.end()].strip())
        start = match.end()

    tail = body[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def is_pesticide_emission_sentence(sentence: str) -> bool:
    """A sentence saying how pesticide emissions were calculated."""
    return bool(_PESTICIDE_WORDS.search(sentence) and _EMISSION_WORD.search(sentence))


def apply_pesticide_sentence(text) -> tuple:
    """
    Return (text, status) with the locked pesticide-emission sentence in place.

    status is 'ok' when it already reads that way, 'replaced' when another
    wording was swapped out, or 'added' when the column said nothing about
    pesticide emissions at all.
    """
    sentences = split_sentences(text)
    matches = [i for i, sentence in enumerate(sentences)
               if is_pesticide_emission_sentence(sentence)]

    if not matches:
        if not sentences:
            return PESTICIDE_EMISSION_SENTENCE, 'added'
        # Close the preceding sentence first: several of these columns end
        # without a full stop, and appending straight onto one ran the two
        # together ("... Pb, Hg The emissions due to ...").
        if not ends_with_stop(sentences[-1]):
            sentences[-1] = sentences[-1].rstrip() + '.'
        return ' '.join(sentences + [PESTICIDE_EMISSION_SENTENCE]), 'added'

    if (len(matches) == 1
            and sentences[matches[0]] == PESTICIDE_EMISSION_SENTENCE):
        return ' '.join(sentences), 'ok'

    # The first one becomes the locked sentence; any further ones are saying
    # the same thing again and go.
    kept = [PESTICIDE_EMISSION_SENTENCE if index == matches[0] else sentence
            for index, sentence in enumerate(sentences)
            if index == matches[0] or index not in matches]
    return ' '.join(kept), 'replaced'


def remove_pesticide_sentence(text) -> tuple:
    """
    Return (text, status) without the locked pesticide-emission sentence, for
    a dataset that uses no pesticides -- 'removed' or 'ok'.

    Only the locked sentence goes: it is the model's own, and says emissions
    were calculated.  Anything else the author wrote about pesticides ("no
    plant protection products are applied") is left exactly as it is.
    """
    sentences = split_sentences(text)
    kept = [s for s in sentences if s.strip() != PESTICIDE_EMISSION_SENTENCE]
    if len(kept) == len(sentences):
        return ' '.join(sentences), 'ok'
    return ' '.join(kept), 'removed'


# ── includedActivitiesStart (STANDARD OPENING) ───────────────────────────────
# The field always opens with "This activity starts ", naming what prepares
# the current cultivation.  Three forms are standard:
#
#   This activity starts with  <what prepares this cultivation>
#   This activity starts after <the point the boundary opens at>
#   This activity starts at    <the point the boundary opens at>
#
# "at" is how ecoinvent itself opens the field on datasets that name a point
# rather than an operation ("starts at the extraction of raw materials"), so
# flagging it was rejecting correct text -- and every dataset read in from a
# .spold opens that way.
#
# "begins with/after/at" says the same thing in different words; it is
# rewritten to the standard opening, leaving the rest of the sentence
# untouched.  Any other opening is reported -- rewording someone's
# description is not mechanical.

INCLUDED_START_COLUMN = 'includedActivitiesStart'
INCLUDED_START_PREFIX = 'This activity starts '

_INCLUDED_START_OK     = re.compile(r'^\s*This activity starts (?:with|after|at)\b', re.IGNORECASE)
_INCLUDED_START_LEGACY = re.compile(r'^\s*This activity begins (with|after|at)\b', re.IGNORECASE)

# A worked example of the full standard, for the fix sheet and for anyone
# writing one from scratch: opening, then inputs, then the activities covered.
INCLUDED_START_EXAMPLE = (
    'This activity starts after the harvest of the previous crop. A non-hibernating '
    'green manure crop is modelled between harvest of the previous crop and soil '
    'cultivation of the current crop. Included inputs are seeds (crop and green '
    'manure), mineral fertilizer and plant protection products. Activities of soil '
    'cultivation, sowing, fertilizer application, farm manure spreading, pest and '
    'pathogen control, weed control and harvest are included.'
)


def included_start_status(text) -> tuple:
    """
    Check the opening of includedActivitiesStart.

    Returns (status, canonical_text, detail):
      'ok'          already opens with "This activity starts with/after"
      'reformat'    opens with the equivalent "begins with/after"
      'nonstandard' opens some other way -- a human has to reword it
      'missing'     nothing there
    """
    body = str(text or '').strip()
    if not body:
        return 'missing', None, None

    opening = ' '.join(body.split()[:5])
    if _INCLUDED_START_OK.match(body):
        return 'ok', body, opening

    legacy = _INCLUDED_START_LEGACY.match(body)
    if legacy:
        rewritten = (INCLUDED_START_PREFIX + legacy.group(1).lower() + ' '
                     + body[legacy.end():].lstrip())
        return 'reformat', rewritten, opening

    return 'nonstandard', None, opening


def apply_included_start(text) -> tuple:
    """Return (text_with_standard_opening, status, detail)."""
    status, canonical, detail = included_start_status(text)
    if status == 'reformat' and canonical:
        return canonical, status, detail
    return str(text or ''), status, detail


def set_yield_line(text, value) -> str:
    """
    Put a yield figure into a comment in the locked form.

    Replaces an existing yield line however it is written, including an
    unfilled placeholder like "Yield : [VALORE] kg/ha"; appends one when the
    comment has none at all.
    """
    formatted = format_yield(value)
    if formatted is None:
        return str(text or '')
    canonical = YIELD_TEMPLATE.format(value=formatted)
    body = str(text or '')

    if find_yield(body):
        return YIELD_ANY_RE.sub(lambda _m: canonical, body, count=1)

    # A placeholder or other unreadable yield sentence: replace it whole.
    # A colon or dash is required: without it this matches the bare word
    # "yield" in ordinary prose and mangles the sentence around it.
    placeholder = re.search(r'[Yy]ield\s*[:\-][^.]{0,60}\.?', body)
    if placeholder:
        return (body[:placeholder.start()] + canonical + '.'
                + body[placeholder.end():]).strip()

    return (body.rstrip() + (' ' if body.strip() else '') + canonical + '.').strip()


def replace_crop_word(text, wrong_crop: str, own_crop: str) -> str:
    """
    Swap another crop's name for this dataset's own, as a PROPOSAL.

    Plural and capitalisation are carried over, so "Clementines" in a lemon
    dataset becomes "Lemons".  Whole words only: "lime" must not be found
    inside "limestone".
    """
    body = str(text or '')
    if not wrong_crop or not own_crop:
        return body

    def swap(match):
        found = match.group(0)
        word = own_crop + 's' if found.lower().endswith('s') and not own_crop.endswith('s')             else own_crop
        if found[:1].isupper():
            word = word[:1].upper() + word[1:]
        return word

    pattern = re.compile(r'\b' + re.escape(wrong_crop) + r's?\b', re.IGNORECASE)
    return pattern.sub(swap, body)


# Compass and landform words take an article in prose -- "in the south", not
# "in south" -- while a place name does not: "in Andalusia".  Generated text
# has to read like the hand-written text it sits beside.
_ARTICLE_REGIONS = (
    'north', 'south', 'east', 'west', 'centre', 'center', 'islands', 'island',
    'north west', 'north east', 'south west', 'south east', 'hill', 'hills',
    'plain', 'plains', 'mountain', 'mountains',
)


# The geography code spelled out, for prose.  Extend it as projects bring new
# countries; an unknown code leaves the country unnamed rather than guessed at.
COUNTRY_NAMES = {
    'IT': 'Italy',       'TN': 'Tunisia',  'CA': 'Canada',   'ES': 'Spain',
    'FR': 'France',      'GR': 'Greece',   'PT': 'Portugal', 'DE': 'Germany',
    'CH': 'Switzerland', 'AT': 'Austria',  'BE': 'Belgium',  'NL': 'Netherlands',
    'PL': 'Poland',      'RO': 'Romania',  'HU': 'Hungary',  'BG': 'Bulgaria',
    'HR': 'Croatia',     'SI': 'Slovenia', 'SK': 'Slovakia', 'CZ': 'Czechia',
    'DK': 'Denmark',     'SE': 'Sweden',   'FI': 'Finland',  'IE': 'Ireland',
    'BR': 'Brazil',      'AR': 'Argentina', 'AU': 'Australia', 'NZ': 'New Zealand',
    'ZA': 'South Africa', 'MA': 'Morocco', 'EG': 'Egypt',    'TR': 'Turkey',
    'IN': 'India',       'CN': 'China',    'MX': 'Mexico',   'CL': 'Chile',
    'PE': 'Peru',        'GB': 'the United Kingdom',
    'US': 'the United States',
}


# Particles that stay lowercase inside a place name: "Colle della Sabina",
# not "Colle Della Sabina".
_NAME_PARTICLES = (
    'of', 'the', 'del', 'della', 'delle', 'dei', 'degli', 'di', 'da', 'd',
    'la', 'le', 'lo', 'el', 'los', 'las', 'de', 'do', 'dos', 'e', 'y', 'and',
)


def capitalise_place(name) -> str:
    """Title-case a place name that the nomenclature writes in lower case.

    Words that already carry a capital are left alone, so 'Castelli Romani'
    and acronyms such as 'DOC' survive untouched.
    """
    words = str(name or '').split()
    out = []
    for i, word in enumerate(words):
        if word != word.lower():
            out.append(word)
        elif i and word.strip('.').lower() in _NAME_PARTICLES:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return ' '.join(out)


def country_name(code) -> str:
    """The country a geography code names, or '' when it names no country."""
    return COUNTRY_NAMES.get(str(code or '').strip().upper(), '')


def region_phrase(region, country=None) -> str:
    """
    A region as it should read inside a sentence.

    A compass or area word takes an article and, where the country is known,
    names it -- "the south of Italy" rather than the bare "the south", which
    reads as though the country were understood when it is not.  A place name
    takes neither: "Andalusia", not "the Andalusia of Spain".
    """
    name = ' '.join(str(region or '').split())
    if not name:
        return ''
    if name.lower() not in _ARTICLE_REGIONS:
        # A place name, not a compass direction -- it reads as a proper noun
        # in prose even though the nomenclature writes it in lower case.
        return capitalise_place(name)
    # The country attaches only to a word that names a region.  "the hill" is
    # a landform, and "the hill of Italy" is nonsense -- it takes the article
    # and nothing more.
    if country and name.lower() in AXIS_VOCABULARIES['region']:
        return f'the {name} of {country}'
    return f'the {name}'


# ── House wording (applied to EVERY text column) ─────────────────────────────
# One spelling per word, everywhere.  Two kinds of entry:
#
#   outright misspellings -- "seedligs", "nationale", "avarege", "lemom",
#   "combatin", "dI Agricoltura" -- which are simply wrong; and
#
#   spelling variants the datasets mix -- fertilization/fertilisation,
#   modeled/modelled -- where BRITISH English is chosen, as ecoinvent uses.
#   Flip these entries to switch the house style; nothing else needs changing.
#
# These are cosmetic, so they are applied silently as minor tidy-ups and are
# not put in the review queue.
WORDING = {
    # misspellings
    'seedligs':        'seedlings',
    'nationale':       'national',
    'avarage':         'average',
    'avarege':         'average',
    'avereage':        'average',
    'lemom':           'lemon',
    'combatin':        'combating',
    'cryptogames':     'cryptogams',
    'dI':              'di',
    # British house spelling
    'fertilization':   'fertilisation',
    'fertilizations':  'fertilisations',
    'fertilizer':      'fertiliser',
    'fertilizers':     'fertilisers',
    'modeled':         'modelled',
    'organization':    'organisation',
    'standardized':    'standardised',
    # ecoinvent's term: a product output besides the reference product is a
    # by-product, never a co-product -- in the metadata and every comment.
    'co-product':      'by-product',
    'co-products':     'by-products',
    'coproduct':       'by-product',
    'coproducts':      'by-products',
    'co product':      'by-product',
    'co products':     'by-products',
}

# Case-insensitive, because these words also appear capitalised at the start
# of a sentence ("Modeled crop duration: 25 years"); the replacement puts the
# original capitalisation back.
_WORDING_RE = re.compile(
    r'\b(' + '|'.join(sorted(WORDING, key=len, reverse=True)) + r')\b',
    re.IGNORECASE)


def apply_house_wording(text) -> str:
    """One spelling per word, with the original capitalisation kept."""
    def swap(match):
        found = match.group(0)
        word = WORDING.get(found) or WORDING.get(found.lower(), found)
        if found[:1].isupper() and word[:1].islower():
            word = word[:1].upper() + word[1:]
        return word

    return _WORDING_RE.sub(swap, str(text or ''))


def canonical_column_text(column: str, value, pesticides: bool = True) -> str:
    """
    The canonical form of one meta text column.

    Used by the model when comparing AND by build_meta_spec when inducing the
    templates, so both sides speak the same form.  Without this the templates
    would keep, say, "kg per hectare" as fixed wording and then reject the very
    line the yield rule produces.

    `pesticides` says whether the dataset uses any: the locked pesticide-
    emission sentence belongs in extrapolations only then, and is taken out of
    a dataset that uses none.
    """
    text = apply_house_wording(tidy_text(value))
    if column == 'generalComment':
        text = apply_yield_format(text)[0]
    elif column == INCLUDED_START_COLUMN:
        text = apply_included_start(text)[0]
    elif column == EXTRAPOLATIONS_COLUMN:
        text = (apply_pesticide_sentence(text) if pesticides
                else remove_pesticide_sentence(text))[0]
    # The final stop belongs to the canonical form, so the builder puts it in
    # the templates and the model puts it in the values.  Adding it on one
    # side only made 21 allocationComments fail their own induced template.
    if text and not ends_with_stop(text):
        text += '.'
    return text


def _yield_span(body: str, match) -> tuple[int, int, bool]:
    """
    The span of text the locked line should replace.

    A declaration often opens its own sentence ("Annual production-average
    yield is 2249.9 kg/ha."), and replacing only from the word "yield" would
    leave the qualifier dangling in front of the new line.  So the span is
    widened back to the start of that sentence -- but only when what precedes
    "yield" there is short.  A sentence carrying other content keeps it: then
    only the declaration itself is replaced.
    """
    start, end = match.start(), match.end()
    sentence_start = max(body.rfind('.', 0, start), body.rfind('\n', 0, start)) + 1
    prefix = body[sentence_start:start]
    if prefix.strip() and len(prefix.split()) > _YIELD_PREFIX_WORDS:
        # Buried mid-sentence: dropping the locked line in here would read
        # badly ("... and the reported Yield: [5000] kg/ha for the region"),
        # so it is flagged inline and left for a human to place.
        return start, end, True
    return sentence_start + (len(prefix) - len(prefix.lstrip())), end, False


def apply_yield_format(text) -> tuple[str, str, str | None]:
    """
    Return (text_with_locked_yield_line, status, detail).

    The old declaration goes and the locked line takes its place; everything
    else in the comment is left alone, including any clause that follows it
    ("..., calculated as a weighted average of the regional yields.").
    """
    status, canonical, detail = yield_line_status(text)
    if status != 'reformat' or canonical is None:
        return str(text or ''), status, detail
    body = str(text)
    match = find_yield(body)
    if not match:
        return body, status, detail
    start, end, inline = _yield_span(body, match)
    rewritten = body[:start] + canonical + body[end:]
    # 'inline' only changes the STATUS the caller sees ("this one sat
    # mid-sentence"), never whether the rewrite happens: the splice only ever
    # touches the bare declaration itself, so it is exactly as safe stated
    # mid-sentence as it is on its own line.
    return rewritten, ('reformat_inline' if inline else status), detail


# Units that convert to kg/ha by one fixed, universal factor -- unlike a
# genuine unit AMBIGUITY, there is nothing to weigh here, so these convert
# outright the same way a misspelling or a whitespace tidy-up does.
_YIELD_CONVERT_FACTOR = {
    't/ha':              1000.0,
    'tonne/ha':          1000.0,
    'tonnes/ha':         1000.0,
    'ton per hectare':   1000.0,
    'tons per hectare':  1000.0,
}


def convert_yield_unit(text) -> str | None:
    """
    The stated yield converted to the locked kg/ha line, or None when the
    unit found is not one of the fixed factors above, or there is no yield
    declaration at all.
    """
    body = str(text or '')
    match = find_yield(body)
    if not match:
        return None
    unit = ' '.join(match.group('unit').lower().split())
    factor = _YIELD_CONVERT_FACTOR.get(unit)
    number = parse_number_loose(match.group('value'))
    if factor is None or number is None:
        return None
    formatted = format_yield(number * factor)
    if formatted is None:
        return None
    canonical = YIELD_TEMPLATE.format(value=formatted)
    start, end, _inline = _yield_span(body, match)
    return body[:start] + canonical + body[end:]


# ── Nomenclature: the UNIVERSAL rules ────────────────────────────────────────
# What holds for any project, any crop, any country.  Everything that varies
# between projects -- which crop families exist, which segments each one
# carries, the region names, the ISIC code per family, the value of a constant
# column -- is NOT here: it is induced per project into meta_profile.py by
# build_meta_spec.py.  Hardcoding it made the model usable on exactly one
# project and flagged every dataset of any other as an unknown crop family.

ACTIVITY_NAME_SEPARATOR = ', '

# The canonical name is lowercase, comma-separated, single-spaced.  These are
# formatting rules, safe to apply anywhere, and are the ONLY grounds on which
# the model renames a dataset.  A missing or unknown axis is a naming call and
# is reported instead.
def canonical_segments(name) -> list:
    """Split an activityName into its normalised segments."""
    return [re.sub(r'\s+', ' ', part).strip().lower()
            for part in re.split(r'\s*,\s*', str(name or '').strip())
            if part.strip()]


def join_segments(segments) -> str:
    return ACTIVITY_NAME_SEPARATOR.join(segments)


# A productive activity is named after what it produces, followed by the word
# "production": "grass production", "wheat grain production, spring,
# conventional, hill region".  Markets and treatments are named otherwise.
PRODUCTION_WORD = 'production'
NOT_PRODUCTION_OPENINGS = ('market for ', 'market group for ', 'treatment of ')
PRODUCTION_KEEP_LABEL = 'not a production activity -- keep the name'


def same_activity_name(a, b) -> bool:
    """Whether two activityNames are the same name once formatting is set
    aside -- case, spacing around the commas, doubled spaces."""
    return bool(canonical_segments(a)) and canonical_segments(a) == canonical_segments(b)


def lacks_production_word(name) -> bool:
    """True when the name's first segment -- the product -- has no 'production'."""
    segments = canonical_segments(name)
    if not segments or segments[0].startswith(NOT_PRODUCTION_OPENINGS):
        return False
    return not re.search(rf'\b{PRODUCTION_WORD}\b', segments[0])


def production_name_options(name, reference_product=None) -> list:
    """
    The ways of putting 'production' into a name that lacks it, best first,
    as (label, name) pairs; the last always keeps the name as it is.

    The product the activity is named after decides where the word goes:
      - the reference product IS the first segment
          'alfalfa-grass silage'          -> 'alfalfa-grass silage production'
      - the reference product opens it, the rest qualifies it
          'grass organic'  (product grass) -> 'grass production, organic'
      - it ends in a practice (an -ing word, with the animal before it)
          'alfalfa-grass cattle grazing'  -> 'alfalfa-grass production, cattle grazing'
    """
    segments = canonical_segments(name)
    if not segments:
        return []
    first, rest = segments[0], segments[1:]
    words = first.split()
    product = join_segments(canonical_segments(reference_product)) if reference_product else ''

    def named(head, tail=''):
        return join_segments([f'{head} {PRODUCTION_WORD}'] + ([tail] if tail else []) + rest)

    # A practice is not a product, even where the reference product was
    # given the activity's own name ('alfalfa-grass cattle grazing'), so a
    # trailing practice is split off first.
    whole = ('production after the whole product', named(first))
    ranked = []
    if len(words) > 1 and words[-1].endswith('ing'):
        cut = len(words) - 2 if len(words) > 2 else len(words) - 1
        ranked.append(('production after the crop, the practice as the next segment',
                       named(' '.join(words[:cut]), ' '.join(words[cut:]))))
    if product and product == first:
        ranked.append(whole)
    elif product and first.startswith(product + ' '):
        ranked.append(('production after the reference product',
                       named(product, first[len(product):].strip())))
    ranked.append(whole)
    ranked.append((PRODUCTION_KEEP_LABEL, join_segments(segments)))
    options, seen = [], set()
    for label, value in ranked:
        if value not in seen:
            seen.add(value)
            options.append((label, value))
    return options


# ── Axis meanings (universal) ────────────────────────────────────────────────
# An axis is recognised by WHAT IT CONTAINS, not by where it sits: any project
# that distinguishes conventional from organic uses the same words for it.
# This is what lets the generator know which segment of a name is the
# production system without anyone labelling the slots per project.
AXIS_VOCABULARIES = {
    'production_system': ('conventional', 'organic', 'integrated'),
    'water_regime':      ('irrigated', 'non-irrigated', 'non irrigated', 'rainfed'),
    'training_system':   ('single-cone', 'poli-cone', 'single cone', 'poli cone',
                          'full wind'),
    'grade':             ('fresh grade', 'processing grade'),
    # Compass and area words name a region in any project.  Deliberately NOT
    # including hill/plain/mountain: those are a LANDFORM axis (olive uses
    # one), and labelling them 'region' would put "hill" where the region
    # belongs in generated text.  A project's own region names (Andalusia,
    # Murcia) are picked up by build_meta_spec's cross-family pass instead.
    'region':            ('north', 'south', 'east', 'west', 'centre', 'center',
                          'islands', 'north west', 'north east', 'south west',
                          'south east', 'north-west', 'north-east',
                          'south-west', 'south-east'),
}


# Where each labelled axis belongs in the name, so an axis the name is
# missing can be put back in the right place instead of appended.  Axes with
# no controlled vocabulary (variety, appellation, landform) are unlabelled and
# simply keep the position they already hold.
AXIS_ORDER = ('production_system', 'water_regime', 'training_system',
              'grade', 'region')

# The spelling the nomenclature uses, for vocabularies that accept more than
# one form of the same value.
CANONICAL_AXIS_VALUES = {
    'non irrigated': 'non-irrigated',
    'single cone':   'single-cone',
    'poli cone':     'poli-cone',
    'center':        'centre',
    'north-west':    'north west',
    'north-east':    'north east',
    'south-west':    'south west',
    'south-east':    'south east',
}

# What to offer for a missing axis, best guess first and duplicate spellings
# dropped -- these are the choices a human picks from.
AXIS_CHOICES = {
    'production_system': ('conventional', 'organic', 'integrated'),
    'water_regime':      ('irrigated', 'non-irrigated', 'rainfed'),
    'training_system':   ('single-cone', 'poli-cone', 'full wind'),
    'grade':             ('fresh grade', 'processing grade'),
}


def canonical_axis_value(value) -> str:
    """One axis value in the spelling the nomenclature uses."""
    text = ' '.join(str(value or '').split()).lower()
    return CANONICAL_AXIS_VALUES.get(text, text)


def axis_rank(axis) -> int:
    """How far along the name an axis sits; unknown axes sort last."""
    try:
        return AXIS_ORDER.index(axis)
    except ValueError:
        return len(AXIS_ORDER)


def insert_axis_value(segments, axis, value) -> list:
    """
    Put `value` into `segments` at the position `axis` belongs to.

    The first segment is the base ("grape production") and never moves.  The
    insertion point is the first segment belonging to a LATER axis, so a
    missing training system lands between the water regime and the region
    rather than at the end of the name.
    """
    segments = list(segments)
    rank = axis_rank(axis)
    index = len(segments)
    for position in range(1, len(segments)):
        label = label_axis([segments[position]])
        if label is None:
            continue                      # uncontrolled axis: nothing to compare
        if axis_rank(label) > rank:
            index = position
            break
    return segments[:index] + [str(value)] + segments[index:]


def axis_value_in_text(text, axis) -> str:
    """
    The value of `axis` that a dataset's own prose names, or ''.

    Longest term first, so "non-irrigated" is not read as "irrigated", and a
    term negated by "non-" is not read at all: "produced under non-organic
    conditions" says what the dataset is NOT, and reading 'organic' out of it
    puts the opposite word in the name.
    """
    body = ' '.join(str(text or '').split()).lower()
    if not body:
        return ''
    for term in sorted(AXIS_VOCABULARIES.get(axis, ()), key=len, reverse=True):
        if term.startswith('non'):
            pattern = r'\b' + re.escape(term) + r'\b'
        else:
            pattern = r'(?<!non-)(?<!non )\b' + re.escape(term) + r'\b'
        if re.search(pattern, body):
            return canonical_axis_value(term)
    return ''


def axis_choices(axis, preferred='') -> list:
    """The choices for a missing axis, with any inferred value first."""
    choices = [canonical_axis_value(v) for v in AXIS_CHOICES.get(axis, ())]
    preferred = canonical_axis_value(preferred)
    if preferred and preferred in choices:
        choices.remove(preferred)
        choices.insert(0, preferred)
    elif preferred:
        choices.insert(0, preferred)
    return choices


def label_axis(values):
    """
    Name an axis from the values observed in it, or None when unrecognised.

    Every observed value must belong to the vocabulary, so an axis holding
    crop varieties is not mistaken for a controlled one.
    """
    observed = {str(v).strip().lower() for v in values if str(v).strip()}
    if not observed:
        return None
    for name, vocabulary in AXIS_VOCABULARIES.items():
        if observed <= set(vocabulary):
            return name
    return None


# ── Column classification (universal) ────────────────────────────────────────
# Which columns hold prose, which are structural.  The VALUES of the constant
# columns are learned per project; only the fact that they are expected to be
# constant across a project is universal.
CONSTANT_CANDIDATES = (
    'specialActivityType',
    'technologyLevel',
    'dataEntryBy_isActiveAuthor',
    'dataGeneratorAndPublication_accessRestrictedTo',
)

# Structural ecoinvent fields this pipeline does not populate.  A project that
# legitimately fills one is not wrong, so this is reported, never enforced.
EMPTY_CANDIDATES = (
    'synonyms',
    'tags',
    'dataEntryBy_personName',
    'dataGeneratorAndPublication_dataPublishedIn',
    'dataGeneratorAndPublication_pageNumbers',
    'dataGeneratorAndPublication_personName',
    'dataGeneratorAndPublication_publishedSourceId',
    'modellingAndValidation_percent',
    'fileAttributes_majorRelease',
    'fileAttributes_minorRelease',
    'fileAttributes_majorRevision',
    'fileAttributes_minorRevision',
)

TEMPLATE_COLUMNS = (
    'includedActivitiesStart',
    'includedActivitiesEnd',
    'generalComment',
    'geographyComment',
    'technologyComment',
    'timePeriodComment',
    'modellingAndValidation_extrapolations',
    'allocationComment',
)

# Carried through untouched; only presence is checked.
FREE_COLUMNS = ('geography', 'startDate', 'endDate')

# Legitimately blank on some datasets, so blankness is not itself a finding.
OPTIONAL_COLUMNS = ('allocationComment',)

# Sheets carrying an activityName that must follow a rename.
RENAME_SHEETS = ('meta', 'review', 'exchanges', 'properties', 'parameters')
# A link from another activity of the same file follows its target's rename.
RENAME_COLUMNS = ('activityName', 'activityLink_activityName')

# How sure the project has to be before a value counts as its standard: a
# column is treated as constant, or an ISIC as the family's, when at least this
# share of the datasets agree.  The minority then reads as the deviation.
MAJORITY_SHARE = 0.75
