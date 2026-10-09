#!/usr/bin/env python3
"""
Generate standardized metadata comment text (generalComment, geographyComment,
technologyComment, timePeriodComment, samplingProcedure, extrapolations,
allocationComment) from structured inputs, following the unified metadata
template.

This is a TEMPLATE FILLER, not a free-text writer - it deliberately doesn't
try to be clever about wording beyond what the template specifies, so output
stays consistent across every dataset the skill touches. Missing optional
inputs simply omit the corresponding clause/sentence rather than guessing.

FIXED SENTENCE SKELETON: every field follows the same slot order and shape
every time - only the substituted values change, never the structure itself
(per explicit instruction). The one legitimate structural exception is the
perennial-crop duration sentence, which only exists for perennial crops
because there's nothing analogous to say for an annual crop - that's a real
difference in what's true of the crop, not an inconsistency to eliminate.

YIELD NUMBER FORMAT (locked in by explicit instruction): the yield value
is formatted with a period separator and no thousands separator, to exactly
2 decimal places (e.g. 26299.94), a whole number included (9000 ->
9000.00, the review skill's format) - regardless of how the input was
given (int, float, or a string using a comma decimal like "8604,16", which
this module normalizes automatically). Everything else about the yield
line's structure (the "Yield: [...] kg/ha." wrapper) stays exactly as
before - only the number's own formatting is affected by this rule.

IN THIS PIPELINE: format_yield() below is THE locked formatter -
meta_spec.format_yield() parses messy input (brackets, a trailing unit,
scientific notation) and then hands the number here, so the pipeline and the
review skill produce byte-identical yield lines. find_short_form_citations()
is likewise the one used by check_metadata_nomenclature.check_citations().

Usage as a library:
    from generate_metadata import generate_general_comment, generate_geography_comment, ...
    text = generate_general_comment(crop_name="olive", production_system="integrated", ...)

Usage from the command line (prints all sections as JSON, given a JSON input file):
    python generate_metadata.py inputs.json
"""
import re
import json
import argparse


def _join_list(items, conjunction="and"):
    """Oxford-comma-with-and join - used for plant protection product
    lists, matching the real examples' convention, e.g.
    "herbicides, fungicides, and insecticides"."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", {conjunction} " + items[-1]


def _join_list_plain(items):
    """Plain comma-separated join, no 'and' before the last item - matches
    the real examples' convention for fertiliser nutrient lists, e.g.
    "(Nitrogen, Phosphorus, Potassium)" NOT "(Nitrogen, Phosphorus, and
    Potassium)". Plant protection type lists DO use 'and' (_join_list) -
    these are two different, both-confirmed conventions, not one to
    standardize away."""
    return ", ".join(i for i in items if i)


def format_yield(yield_value):
    """
    LOCKED FORMAT (explicit instruction): period separator, no thousands
    separator, and exactly 2 decimal places - e.g. 26299.94, and 9000.00
    for a whole number.

    Accepts int, float, or a string - including a string using a comma
    decimal separator (e.g. "8604,16", the exact format found and fixed in
    a real dataset during development) or a thousands-separated string
    (e.g. "26,299.94" or "26.299,94") - normalizes all of these to a plain
    float before formatting, rather than assuming clean input.
    """
    if isinstance(yield_value, (int, float)):
        num = float(yield_value)
    else:
        s = str(yield_value).strip()
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            # Groups of exactly three digits after a first group of 1-3 that
            # is not 0: thousands ("12,582", "1,234,567").  Otherwise a
            # decimal comma ("8604,16", "0,125").
            if re.match(r"^[1-9]\d{0,2}(?:,\d{3})+$", s):
                s = s.replace(",", "")
            else:
                s = s.replace(",", ".")
        num = float(s)
    # Always exactly two decimals, a whole number included: 9000 -> 9000.00
    # (the review skill's locked format).
    return f"{num:.2f}"


def generate_general_comment(
    crop_name,
    production_system,
    water_regime,
    yield_kg_ha,
    variety_or_cultivar=None,
    region=None,
    product_description=None,
    is_perennial=False,
    fertiliser_nutrients=None,
    organic_fertiliser_type=None,
    plant_protection_types=None,
    by_product=None,
    reference_product_name=None,
    crop_duration_years=None,
    unproductive_years=None,
    decommissioning_excluded=None,
    transport_destination="the farm centre",
):
    """
    FIXED SENTENCE SKELETON - same slots, same order, same wording shape
    every time. Only the substituted values change - never the sentence
    structure itself. The one legitimate structural variation is the
    crop-duration sentence, which only exists for perennial crops.

    Slot order, always: intro -> outputs -> inputs -> operations ->
    water regime -> [duration, perennial only] -> yield.
    """
    ref_product = reference_product_name or crop_name
    variety_clause = f", {variety_or_cultivar}" if variety_or_cultivar else ""
    region_clause = f" in {region}" if region else ""
    product_desc = product_description or ref_product

    sentences = []

    sentences.append(
        f"This dataset represents the average production of 1 kg of {crop_name}{variety_clause}, "
        f"obtained under {production_system} cultivation at farm level{region_clause} to produce {product_desc}."
    )

    if by_product:
        sentences.append(
            f"This cultivation generates two outputs: {ref_product} as the reference product "
            f"and {by_product} as a by-product."
        )
    else:
        sentences.append(f"This cultivation generates one output: {ref_product} as the reference product.")

    planting_material_clause = (
        "seedlings, the planting of the trees and the establishment of the orchard"
        if is_perennial else "seeds"
    )
    if organic_fertiliser_type:
        fertiliser_clause = f"organic fertilisers ({organic_fertiliser_type})"
    elif fertiliser_nutrients:
        fertiliser_clause = f"mineral fertilisers ({_join_list_plain(fertiliser_nutrients)})"
    else:
        fertiliser_clause = "no fertilisers"
    if plant_protection_types:
        protection_clause = f"plant protection products ({_join_list(plant_protection_types)})"
    else:
        protection_clause = "no synthetic plant protection products"

    # "includes the production and use of" carries both a material that is
    # applied (seeds, fertiliser) and one that is not (an orchard).
    sentences.append(
        f"The dataset includes the production and use of {planting_material_clause}, "
        f"{fertiliser_clause}, and {protection_clause}."
    )

    ops_start = "pruning and soil management" if is_perennial else "soil tillage and sowing"
    irrigation_clause = ", irrigation" if water_regime == "irrigated" else ""
    sentences.append(
        f"It encompasses all mechanical operations carried out with agricultural machinery, "
        f"from {ops_start} to fertilisation, pest control treatments{irrigation_clause}, "
        f"harvesting, and transport to {transport_destination}."
    )

    regime_text = "an irrigated system" if water_regime == "irrigated" else "a rainfed system (no irrigation)"
    sentences.append(f"This production is modelled as {regime_text}.")

    if is_perennial and crop_duration_years:
        decomm_clause = "; orchard decommissioning is not included in the dataset" if decommissioning_excluded else ""
        unprod_clause = f", but the first {unproductive_years} years are unproductive" if unproductive_years else ""
        sentences.append(
            f"Modelled crop duration: {crop_duration_years} years{unprod_clause}{decomm_clause}."
        )

    sentences.append(f"Yield: [{format_yield(yield_kg_ha)}] kg/ha.")

    return " ".join(sentences)


def generate_geography_comment(
    crop_name, production_system, region, country="Italy",
    percent_national_production=None, subzone_detail=None,
):
    """
    FIXED STRUCTURE - always the same three-sentence shape. Missing detail
    is handled by a neutral fallback phrase, not a different sentence
    structure, so every geographyComment has the same shape.
    """
    percent_clause = (f" ({percent_national_production}% of national production considered)"
                      if percent_national_production else "")
    # Where no narrower area is named, the region itself is the answer -- the
    # datasets say "refer to the plains of southern Italy", naming an area,
    # not "the wider production area for this crop", which names nothing.
    subzone_text = subzone_detail or region

    return (
        f"Data on {production_system} {crop_name} cultivation are representative of "
        f"{region}{percent_clause}. "
        f"Data on fertilisers (based on nitrogen, phosphorus and potassium) refer to the "
        f"national average. Data on fuel, lubricating oil, anti-cryptogamics, herbicides and "
        f"applied technologies refer to {subzone_text}."
    )


_NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def _year_count_word(n):
    return _NUMBER_WORDS.get(n, str(n))


def generate_time_period_comment(start_year, end_year, field_collected=False, crop_name=None, n_years=None,
                                 average_years=None):
    """
    n_years        the agricultural seasons the dates span (meta_spec.
                   agricultural_seasons) -- 2021-08 to 2022-08 is ONE season.
    average_years  the production period the generalComment states (an
                   alfalfa stand's five years), when it differs from the
                   seasons.  The comment then follows THAT period -- it is
                   what the yield describes -- without years, since the dates
                   do not say which; the model flags the dates for checking.
    """
    seasons = n_years or (end_year - start_year + 1)
    if average_years and average_years != seasons:
        return (
            f"The data refer to a {_year_count_word(average_years)}-year production period, over which "
            f"the yield is averaged, so as to take into account changes in agricultural yield."
        )
    if seasons == 1 and not field_collected:
        return (
            f"The statistical analysis is conducted over one agricultural season ({start_year} - {end_year})"
            if start_year != end_year else
            f"The statistical analysis is conducted over one agricultural season ({start_year})"
        ) + ", so as to consider the values of a full crop cycle."
    if field_collected:
        n = n_years or (end_year - start_year + 1)
        return (
            f"Data were collected in the field between {start_year} and {end_year}. "
            f"Production per hectare represents the average of the past {_year_count_word(n)} years for taking into "
            f"account the yearly fluctuations of {crop_name or 'the crop'} production."
        )
    n = n_years or (end_year - start_year + 1)
    return (
        f"The statistical analysis is conducted over a {_year_count_word(n)}-year time period ({start_year} - {end_year}), "
        f"so as to consider average values that take into account changes in agricultural yield."
    )


# How the data were obtained, in the wording the datasets already use.
SAMPLING_SOURCES = {
    "field":       "measurements collected on field at farm level",
    "statistical": "statistical sources",
    "literature":  "literature data",
}


def generate_sampling_procedure(
    crop_name, production_system=None, region=None, data_source=None,
    start_year=None, end_year=None, n_farms=None, source_reference=None,
):
    """
    FIXED STRUCTURE - how the data behind the dataset were obtained, and over
    what period.

    Says only what can be established from the dataset itself: the crop and
    cultivation system from the activity name, the region where the name
    carries one, the kind of source from the technology comment, and the
    period from the validity dates. A missing input drops its clause rather
    than being guessed at, exactly as in the other generators here, because
    this text is proposed for a human to confirm - not asserted.
    """
    sentences = []
    system = f"{production_system} " if production_system else ""
    where = f" in {region}" if region else ""
    source = SAMPLING_SOURCES.get(data_source, data_source)

    if source:
        sentences.append(
            f"Data on {system}{crop_name} cultivation{where} are based on {source}."
        )
    else:
        sentences.append(
            f"Data on {system}{crop_name} cultivation{where} are based on the sources "
            f"named in the technology comment."
        )

    if n_farms:
        sentences.append(f"The sample comprises {n_farms} farms.")
    if source_reference:
        sentences.append(f"Source: {str(source_reference).strip().rstrip('.')}.")

    if start_year and end_year:
        sentences.append(
            f"The data cover the period {start_year} - {end_year} and represent an "
            f"average over that period."
        )

    return " ".join(sentences)


def generate_allocation_comment(
    crop_name, by_product, ref_factor, by_factor, yield_source,
    ref_price_per_t, by_product_price_per_t, price_years, price_source,
    additional_citations=None,
):
    crop_cap = crop_name[0].upper() + crop_name[1:] if crop_name else crop_name
    text = (
        f"For the allocation of {crop_name}, a factor of {ref_factor} is used (while for {by_product} "
        f"the factor is {by_factor}) derived from an economic-physical allocation. {crop_cap} and "
        f"{by_product} yields are reported in \"{yield_source}\". The average price of {crop_name} "
        f"({ref_price_per_t} €/t) and {by_product} ({by_product_price_per_t} €/t) over the reference "
        f"period ({price_years}) is reported in \"{price_source}\"."
    )
    if additional_citations:
        text += " " + " ".join(additional_citations)
    return text


def generate_allocation_comment_multi(crop_name, ref_factor, ref_price_per_t,
                                      by_products, yield_source, price_years,
                                      price_source, additional_citations=None):
    """
    The same template for any number of by-products.  `by_products` is a list
    of (name, factor, price_text), each price with its unit ("170 €/t").
    With exactly one by-product, priced per tonne, the text is
    identical to generate_allocation_comment(); with more, each clause lists
    them in order ("for A the factor is x, and for B the factor is y").
    """
    if len(by_products) == 1 and str(ref_price_per_t).endswith(' €/t')             and str(by_products[0][2]).endswith(' €/t'):
        name, factor, price = by_products[0]
        return generate_allocation_comment(
            crop_name, name, ref_factor, factor, yield_source,
            str(ref_price_per_t)[:-4], str(price)[:-4], price_years, price_source,
            additional_citations)

    def listed(parts):
        return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]

    crop_cap = crop_name[0].upper() + crop_name[1:] if crop_name else crop_name
    factors = listed([f"for {n} the factor is {f}" for n, f, _ in by_products])
    names = listed([n for n, _, _ in by_products])
    # Each price arrives with its unit ("170 €/t"), so products in different
    # units can be listed together.
    prices = listed([f"{n} ({p})" for n, _, p in by_products])
    text = (
        f"For the allocation of {crop_name}, a factor of {ref_factor} is used (while {factors}) "
        f"derived from an economic-physical allocation. {crop_cap} and {names} yields are "
        f"reported in \"{yield_source}\". The average price of {crop_name} ({ref_price_per_t}) "
        f"and {prices} over the reference period ({price_years}) is reported in \"{price_source}\"."
    )
    if additional_citations:
        text += " " + " ".join(additional_citations)
    return text


def find_short_form_citations(text):
    pattern = r'[A-Z][a-zA-Z\-]+(?: (?:and|&|et al\.?) [A-Z][a-zA-Z\-]+)?,?\s*\(?(?:19|20)\d{2}\)?'
    found = set()
    for m in re.finditer(pattern, text):
        found.add(m.group(0).strip())
    return sorted(found)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs_json", help="JSON file with keys matching the generate_* function arguments")
    args = ap.parse_args()

    with open(args.inputs_json, "r", encoding="utf-8") as f:
        inputs = json.load(f)

    out = {}
    if "general_comment" in inputs:
        out["generalComment"] = generate_general_comment(**inputs["general_comment"])
    if "geography_comment" in inputs:
        out["geographyComment"] = generate_geography_comment(**inputs["geography_comment"])
    if "time_period_comment" in inputs:
        out["timePeriodComment"] = generate_time_period_comment(**inputs["time_period_comment"])
    if "allocation_comment" in inputs:
        out["allocationComment"] = generate_allocation_comment(**inputs["allocation_comment"])

    print(json.dumps(out, indent=2, ensure_ascii=False))
