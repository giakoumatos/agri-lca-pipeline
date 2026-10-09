#!/usr/bin/env python3
"""
Convert a price to EUR2005, reimplementing the logic of the ecoinvent
price_converter.xlsx tool (which uses live Excel XLOOKUP/array formulas)
as pure Python so it can run without Excel.

Data source: assets/price_converter_model.json (built once from the
uploaded price_converter.xlsx - see build note at the bottom of this file
if the user supplies an updated version of that workbook).

The underlying model, exactly mirroring the spreadsheet:
  1. eur_current_year = amount / exchange_rate[currency][year]
     (exchange rate = units of <currency> per 1 EUR that year, ECB annual reference rate)
  2. eur2005 = eur_current_year / ppi_ratio[year]
     (ppi_ratio = Euro area PPI that year, relative to 2005=1, Eurostat)
  3. If prices are given for multiple years (e.g. a range of survey years),
     average the per-year EUR2005 values (matches the spreadsheet's
     AVERAGEIF over populated year columns).

Model coverage: currencies EUR, USD, JPY, INR, GBP, CNY, CHF, CAD, AUD;
years 2005-2025. ANY year in that range can serve as the source year, and
the EUR-only shorthand (--eur-year) handles the common case of a price
already expressed in EUR of some non-2005 year (no currency conversion
needed, just inflation adjustment).

Usage as a library:
    from convert_price_to_eur2005 import convert_price
    result = convert_price(entries=[{"currency": "USD", "year": 2023, "amount": 4.20}])
    # result = {"eur2005": 3.71, "comment": "The price was converted to EUR based on ..."}

Usage from the command line:
    python3 convert_price_to_eur2005.py USD 2023 4.20
    python3 convert_price_to_eur2005.py --multi '[{"currency":"USD","year":2022,"amount":4.0},{"currency":"USD","year":2023,"amount":4.4}]'
    python3 convert_price_to_eur2005.py --eur-year 2015 100
"""
import sys
import json
import argparse
from pathlib import Path

# Copied from the checkdataset skill; kept in config/models/ with the other reference data.
try:
    from config_new_datasets import PRICE_MODEL_FILE as MODEL_PATH
except ImportError:                     # run on its own, outside the project
    MODEL_PATH = Path(__file__).resolve().parent / "price_converter_model.json"


def load_model(path=None):
    path = Path(path) if path else MODEL_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def convert_one(currency, year, amount, model):
    year = str(year)
    exch = model["exchange_rates_currency_per_eur"]
    ppi = model["ppi_euro_area_relative_to_2005"]

    if currency not in exch:
        raise ValueError(
            f"Currency '{currency}' not in the price converter model. "
            f"Available: {sorted(exch.keys())}. If this is a real currency the "
            f"model doesn't cover, the user needs to supply an updated "
            f"price_converter.xlsx with that currency added to its "
            f"'PPI and exchange rates' sheet."
        )
    if year not in exch[currency] or year not in ppi:
        raise ValueError(
            f"Year {year} not covered by the price converter model "
            f"(covers {min(ppi.keys())}-{max(ppi.keys())}). Needs an updated "
            f"price_converter.xlsx to extend coverage."
        )

    rate = exch[currency][year]
    eur_current = amount / rate
    eur2005 = eur_current / ppi[year]
    return eur2005


def convert_eur_year_to_eur2005(amount, year, model=None):
    """
    Shorthand for the common case: a price is already expressed in EUR of
    some year (no currency conversion needed - e.g. a literature source
    that reports "EUR2015" or "EUR2018" prices directly), and only needs
    inflation-adjusting back to EUR2005. This is just convert_one with
    currency="EUR", exposed explicitly since it's a distinct, common input
    shape (as opposed to "local currency, raw amount, year" which needs the
    ECB exchange rate step too).

    Works for ANY year in the model's coverage (2005-2025, not just one
    hardcoded year) - the PPI deflation step is looked up per-year exactly
    like the currency-conversion path.
    """
    if model is None:
        model = load_model()
    return convert_one("EUR", year, amount, model)


def convert_price(entries, model=None):
    """
    entries: list of {"currency": str, "year": int|str, "amount": float}
    Mirrors the spreadsheet: converts each entry to EUR2005, then averages
    across entries if more than one is given (matches AVERAGEIF over
    populated year columns in the 'price converter' sheet).
    Returns {"eur2005": float, "per_entry": [...], "comment": str}
    """
    if model is None:
        model = load_model()

    per_entry = []
    for e in entries:
        val = convert_one(e["currency"], e["year"], e["amount"], model)
        per_entry.append({**e, "eur2005": val})

    avg = sum(x["eur2005"] for x in per_entry) / len(per_entry)

    currencies = {e["currency"] for e in entries}
    multiple = len(entries) > 1
    if currencies == {"EUR"}:
        years_differ_from_2005 = any(str(e["year"]) != "2005" for e in entries)
        if not years_differ_from_2005:
            comment = ""
        else:
            comment = (
                ("The prices were" if multiple else "")
                + " adjusted for inflation relative to 2005 using the producer "
                  "price index (PPI) of the euro area, published by eurostat "
                  f"({model.get('source_ppi', '')}, last accessed on "
                  f"{model.get('last_accessed_ppi', '')})."
            )
    else:
        comment = (
            ("The prices were" if multiple else "The price was")
            + " converted to EUR based on the annual exchange rates published "
              "by the European Central Bank (source: "
              "https://data.ecb.europa.eu/data/datasets/EXR), and adjusted for "
              "inflation relative to 2005 using the producer price index (PPI) "
              "of the euro area, published by eurostat "
              f"({model.get('source_ppi', '')}, last accessed on "
              f"{model.get('last_accessed_ppi', '')})."
        )

    return {"eur2005": avg, "per_entry": per_entry, "comment": comment.strip()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("currency", nargs="?")
    ap.add_argument("year", nargs="?")
    ap.add_argument("amount", nargs="?", type=float)
    ap.add_argument("--multi", help="JSON list of {currency, year, amount} entries")
    ap.add_argument("--eur-year", nargs=2, metavar=("YEAR", "AMOUNT"),
                     help="shorthand: amount is already in EUR of the given year, just inflation-adjust to EUR2005")
    ap.add_argument("--model", default=None, help="path to price_converter_model.json override")
    args = ap.parse_args()

    model = load_model(args.model)

    if args.eur_year:
        year, amount = args.eur_year
        result = {"eur2005": convert_eur_year_to_eur2005(float(amount), year, model)}
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if args.multi:
        entries = json.loads(args.multi)
    else:
        if not (args.currency and args.year and args.amount is not None):
            print("Provide currency year amount, or --multi '<json>'", file=sys.stderr)
            sys.exit(1)
        entries = [{"currency": args.currency, "year": int(args.year), "amount": args.amount}]

    result = convert_price(entries, model)
    print(json.dumps(result, indent=2))

# ---------------------------------------------------------------------------
# Note on updating the model:
# If the user supplies a newer price_converter.xlsx (updated exchange rates,
# additional years, or additional currencies), rebuild
# assets/price_converter_model.json from it:
#
#   from openpyxl import load_workbook
#   wb = load_workbook("<new file>.xlsx", data_only=True)
#   ws = wb["PPI and exchange rates"]
#   rows = list(ws.iter_rows(min_row=1, max_row=<n currencies + 2>, max_col=24, values_only=True))
#   years = [str(y) for y in rows[0][3:24]]
#   ppi = dict(zip(years, rows[1][3:24]))
#   exch = {r[0]: dict(zip(years, r[3:24])) for r in rows[2:]}
#   ... then write out the same JSON shape as above.
# ---------------------------------------------------------------------------
