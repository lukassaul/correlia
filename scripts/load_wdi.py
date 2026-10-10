#!/usr/bin/env python3
"""
Correlia World Bank WDI loader.

Fetches indicator data from the World Bank v2 API and loads it into
analysis.sources, analysis.indicators, and analysis.observations.

The World Bank API is the preferred path over bulk CSV downloads because it
returns clean JSON with ISO country codes, year, and value already
structured. Bulk CSV requires parsing a wide format and pivoting, which is
more error-prone for a first loader [citation:1][citation:16].

Usage:
  python3 scripts/load_wdi.py --indicator NY.GDP.PCAP.CD
  python3 scripts/load_wdi.py --indicator SP.POP.TOTL --indicator SH.H2O.SAFE.ZS

  # No --indicator: loads the default set (GDP per capita, population,
  # life expectancy, alcohol consumption proxy)

The loader is idempotent: observations have a unique constraint on
(place_id, indicator_id, year, source_id), and the script uses
ON CONFLICT ... DO UPDATE to refresh values.

Exit codes:
  0  load completed
  1  load failed (errors reported to stderr)
  2  usage error (missing args, connection failure)
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterator

try:
    import psycopg
except ImportError:
    print(
        "error: psycopg is not installed. Install with: pip install 'psycopg[binary]'",
        file=sys.stderr,
    )
    sys.exit(2)

try:
    import requests
except ImportError:
    print(
        "error: requests is not installed. Install with: pip install requests",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# World Bank source record
# ---------------------------------------------------------------------------

WDI_SOURCE = {
    "creator": "World Bank",
    "title": "World Development Indicators",
    "publisher": "World Bank Group",
    "publication_year": 2024,
    "resource_type": "Dataset",
    "description": (
        "The primary World Bank collection of development indicators, "
        "compiled from officially recognized international sources. "
        "Covers national, regional, and global estimates."
    ),
    "rights_license": "CC-BY-4.0",
    "geo_coverage": "Global",
    "datacite_identifier": None,
}

# Indicators we know how to label. Anything not in this dict can still be
# loaded, but will be registered with a generic name and unit.
INDICATOR_METADATA = {
    "NY.GDP.PCAP.CD": {
        "name": "GDP per capita (current US$)",
        "unit": "USD_current",
        "description": "GDP per capita in current US dollars.",
    },
    "SP.POP.TOTL": {
        "name": "Population, total",
        "unit": "persons",
        "description": "Total population based on de facto definition.",
    },
    "SP.DYN.LE00.IN": {
        "name": "Life expectancy at birth, total (years)",
        "unit": "years",
        "description": "Life expectancy at birth, total population.",
    },
    "SH.ALC.PCAP.LI": {
        "name": "Alcohol consumption per capita (liters of pure alcohol)",
        "unit": "liters_per_capita",
        "description": (
            "Total alcohol per capita consumption (15+ years), in liters of "
            "pure alcohol. WHO estimates, as reported in WDI."
        ),
    },
}

DEFAULT_INDICATORS = [
    "NY.GDP.PCAP.CD",
    "SP.POP.TOTL",
    "SP.DYN.LE00.IN",
    "SH.ALC.PCAP.LI",
]

API_BASE = "https://api.worldbank.org/v2"
PER_PAGE = 20000


# ---------------------------------------------------------------------------
# API fetching
# ---------------------------------------------------------------------------

def fetch_indicator_data(indicator_code: str, start: int, end: int) -> list[dict]:
    """
    Fetch one indicator from the World Bank v2 API.

    Returns a list of dicts, each with keys:
      countryiso3code, date, value

    Paginates if the response exceeds PER_PAGE rows.
    """
    rows: list[dict] = []
    page = 1
    while True:
        url = (
            f"{API_BASE}/country/all/indicator/{indicator_code}"
            f"?format=json&per_page={PER_PAGE}"
            f"&date={start}:{end}&page={page}"
        )
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        payload = resp.json()

        # The World Bank API returns a two-element array:
        #   [0] = metadata dict
        #   [1] = list of data rows (or null if no data)
        if not isinstance(payload, list) or len(payload) < 2:
            raise RuntimeError(f"unexpected API response for {indicator_code}")

        meta, data = payload[0], payload[1]
        if data is None:
            break
        rows.extend(data)

        total_pages = int(meta.get("pages", 1))
        if page >= total_pages:
            break
        page += 1

    return rows


def iter_observations(rows: list[dict]) -> Iterator[dict]:
    """
    Yield normalized observation dicts from raw API rows.

    Skips rows with a null value or a non-ISO3 country code.
    """
    for row in rows:
        iso3 = (row.get("countryiso3code") or "").strip()
        if not iso3 or len(iso3) != 3:
            continue
        value = row.get("value")
        if value is None:
            continue
        year_str = (row.get("date") or "").strip()
        if not year_str:
            continue
        try:
            year = int(year_str)
        except ValueError:
            continue

        yield {
            "iso3": iso3,
            "year": year,
            "value": float(value),
        }


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def get_or_create_source(cur) -> int:
    """Insert the WDI source if missing, return its source_id."""
    cur.execute(
        """
        SELECT source_id FROM analysis.sources
        WHERE creator = %s AND title = %s AND publisher = %s
        """,
        (WDI_SOURCE["creator"], WDI_SOURCE["title"], WDI_SOURCE["publisher"]),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO analysis.sources
            (creator, title, publisher, publication_year, resource_type,
             description, rights_license, geo_coverage, datacite_identifier)
        VALUES (%(creator)s, %(title)s, %(publisher)s, %(publication_year)s,
                %(resource_type)s, %(description)s, %(rights_license)s,
                %(geo_coverage)s, %(datacite_identifier)s)
        RETURNING source_id
        """,
        WDI_SOURCE,
    )
    return cur.fetchone()[0]


def get_or_create_indicator(cur, code: str, source_id: int) -> int:
    """Insert the indicator if missing, return its indicator_id."""
    cur.execute(
        "SELECT indicator_id FROM analysis.indicators WHERE code = %s",
        (code,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    meta = INDICATOR_METADATA.get(code, {})
    name = meta.get("name", f"World Bank indicator {code}")
    unit = meta.get("unit", "unknown")
    description = meta.get("description", f"World Bank indicator {code}.")

    cur.execute(
        """
        INSERT INTO analysis.indicators
            (code, name, unit, description, source_id)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING indicator_id
        """,
        (code, name, unit, description, source_id),
    )
    return cur.fetchone()[0]

def load_observations(
    cur,
    indicator_code: str,
    indicator_id: int,
    source_id: int,
    rows: list[dict],
) -> tuple[int, int]:
    """
    Insert observations, one per (country, year).

    Returns (inserted_or_updated, skipped_missing_place).
    """
    # Normalize first, so we only work with rows that have a valid ISO3,
    # a non-null value, and a parseable year.
    normalized = list(iter_observations(rows))

    # Build a map of ISO3 -> place_id for the countries we need.
    iso3s = {obs["iso3"] for obs in normalized}
    cur.execute(
        """
        SELECT iso_alpha3, place_id FROM analysis.places
        WHERE iso_alpha3 = ANY(%s)
        """,
        (list(iso3s),),
    )
    place_map = {iso3: pid for iso3, pid in cur.fetchall()}

    n_loaded = 0
    n_skipped = 0
    denominator = _denominator_for(indicator_code)
    for obs in normalized:
        place_id = place_map.get(obs["iso3"])
        if place_id is None:
            n_skipped += 1
            continue

        cur.execute(
            """
            INSERT INTO analysis.observations
                (place_id, indicator_id, year, value, support_level,
                 collection_method, aggregation_method, denominator, source_id)
            VALUES (%s, %s, %s, %s, 'country', 'administrative', 'none',
                    %s, %s)
            ON CONFLICT (place_id, indicator_id, year, source_id)
            DO UPDATE SET
                value = EXCLUDED.value,
                support_level = EXCLUDED.support_level
            """,
            (
                place_id,
                indicator_id,
                obs["year"],
                obs["value"],
                denominator,
                source_id,
            ),
        )
        n_loaded += 1

    return n_loaded, n_skipped


def _denominator_for(code: str) -> str | None:
    """Best-effort denominator guess from the indicator code."""
    if "PCAP" in code or "PER_CAPITA" in code:
        return "per_capita"
    if "TOTL" in code:
        return "total"
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load World Bank WDI indicators into Correlia.",
    )
    parser.add_argument(
        "--indicator",
        action="append",
        default=[],
        help="WDI indicator code (repeatable). "
        "Defaults to a standard set if none given.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=1960,
        help="Start year (default: 1960)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2024,
        help="End year (default: 2024)",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("CORRELIA_DSN", ""),
        help="PostgreSQL DSN (default: $CORRELIA_DSN)",
    )
    args = parser.parse_args()

    if not args.dsn:
        print("error: --dsn or CORRELIA_DSN must be set", file=sys.stderr)
        return 2

    indicators = args.indicator or DEFAULT_INDICATORS

    try:
        conn = psycopg.connect(args.dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot connect to database: {exc}", file=sys.stderr)
        return 2

    try:
        with conn:
            with conn.cursor() as cur:
                source_id = get_or_create_source(cur)
                print(f"source_id: {source_id}")

                for code in indicators:
                    print(f"fetching {code} ...")
                    try:
                        rows = fetch_indicator_data(
                            code, args.start_year, args.end_year
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"  error: fetch failed: {exc}", file=sys.stderr)
                        continue

                    indicator_id = get_or_create_indicator(cur, code, source_id)
                    loaded, skipped = load_observations(
                        cur, code, indicator_id, source_id, rows
                    )
                    print(
                        f"  {code}: {loaded} observations loaded, "
                        f"{skipped} skipped (no matching place)"
                    )

        print("load completed.")
        return 0

    except Exception as exc:  # noqa: BLE001
        print(f"error: load failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
