#!/usr/bin/env python3
"""
Correlia WHO Global Health Observatory loader.

Uses the scigantic-who package to query the WHO GHO from a public parquet
mirror over DuckDB. This avoids 3,099 individual API calls and decodes WHO
dimension codes automatically.

The loader registers a WHO source, registers each indicator, and inserts
country-level observations into analysis.observations.

Usage:
  python3 scripts/load_who.py
  python3 scripts/load_who.py --indicator WHOSIS_000001 --indicator MORT_100

Defaults to a curated set of health indicators if no --indicator is given.

Exit codes:
  0  load completed
  1  load failed
  2  usage error
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import psycopg
except ImportError:
    print("error: psycopg is not installed.", file=sys.stderr)
    sys.exit(2)

try:
    import scigantic_who as who
except ImportError:
    print(
        "error: scigantic-who is not installed. "
        "Install with: pip install scigantic-who",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Source record
# ---------------------------------------------------------------------------

WHO_SOURCE = {
    "creator": "World Health Organization",
    "title": "Global Health Observatory",
    "publisher": "World Health Organization",
    "publication_year": 2026,
    "resource_type": "Dataset",
    "description": (
        "WHO's main health data repository. Contains global health "
        "indicators covering mortality, morbidity, health systems, "
        "and risk factors. Distributed under CC BY 4.0."
    ),
    "rights_license": "CC-BY-4.0",
    "geo_coverage": "Global",
    "datacite_identifier": None,
}


# ---------------------------------------------------------------------------
# Curated indicator set
# ---------------------------------------------------------------------------

DEFAULT_INDICATORS = [
    "WHOSIS_000001",          # Life expectancy at birth
    "MDG_0000000001",         # Under-five mortality rate
    "WHS4_100",               # Measles immunization coverage
    "GHED_CHEGDP_SHA2011",    # Health spending as % of GDP
    "SA_0000001688",          # Alcohol consumption per capita
    "NCDMORT3070",            # Premature NCD mortality
    "MDG_0000000020",         # Tuberculosis incidence
    "MALARIA_EST_INCIDENCE",  # Malaria incidence
]


# ---------------------------------------------------------------------------
# DataFrame normalization
# ---------------------------------------------------------------------------

# Column names produced by scigantic-who's data() function.
COL_ISO = "SpatialDim"
COL_YEAR = "TimeDim"
COL_VALUE = "NumericValue"

# Dimension labels used by WHO GHO. The "both sexes" slice is preferred
# when present, so that each country-year has a single value.
BOTH_SEXES_LABEL = "Both sexes"


def reduce_to_one_slice(df):
    """
    Reduce a disaggregated DataFrame to one row per (SpatialDim, TimeDim).

    Prefers the "both sexes" slice when present. Otherwise takes the first
    row per group in a stable order.
    """
    if df is None or df.empty:
        return df

    # Prefer "both sexes" if the indicator is disaggregated by sex.
    if "Dim1_name" in df.columns and (df["Dim1_name"] == BOTH_SEXES_LABEL).any():
        df = df[df["Dim1_name"] == BOTH_SEXES_LABEL]

    # If still multiple rows per country-year (e.g. age or residence
    # breakdowns), keep a deterministic single row per group.
    sort_cols = [c for c in (COL_ISO, COL_YEAR) if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols)
    df = df.drop_duplicates(subset=[COL_ISO, COL_YEAR], keep="first")

    return df


def find_indicator_name(code: str) -> str:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = who.indicators()
    match = _CATALOG[_CATALOG["IndicatorCode"] == code]
    if not match.empty:
        return str(match.iloc[0]["IndicatorName"])
    return code

# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def get_or_create_source(cur) -> int:
    cur.execute(
        """
        SELECT source_id FROM analysis.sources
        WHERE creator = %s AND title = %s
        """,
        (WHO_SOURCE["creator"], WHO_SOURCE["title"]),
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
        WHO_SOURCE,
    )
    return cur.fetchone()[0]


def get_or_create_indicator(cur, code: str, name: str, source_id: int) -> int:
    cur.execute(
        "SELECT indicator_id FROM analysis.indicators WHERE code = %s",
        (code,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO analysis.indicators
            (code, name, unit, description, source_id)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING indicator_id
        """,
        (code, name, "unknown", f"WHO GHO indicator {code}.", source_id),
    )
    return cur.fetchone()[0]


def load_observations(cur, indicator_id: int, source_id: int, df) -> tuple[int, int]:
    """
    Insert observations from a scigantic-who DataFrame.

    Expects columns SpatialDim (ISO3), TimeDim (year), NumericValue (value),
    already reduced to one row per country-year.

    Returns (loaded, skipped).
    """
    if df is None or df.empty:
        return 0, 0

    # Build ISO3 -> place_id map for countries in this batch.
    iso3s = set(df[COL_ISO].dropna().unique())
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
    for _, row in df.iterrows():
        place_id = place_map.get(row[COL_ISO])
        if place_id is None:
            n_skipped += 1
            continue

        value = row.get(COL_VALUE)
        if value is None:
            n_skipped += 1
            continue
        # Guard against NaN (float comparison works because NaN != NaN).
        if isinstance(value, float) and value != value:
            n_skipped += 1
            continue

        try:
            year = int(row[COL_YEAR])
        except (ValueError, TypeError):
            n_skipped += 1
            continue

        cur.execute(
            """
            INSERT INTO analysis.observations
                (place_id, indicator_id, year, value, support_level,
                 collection_method, aggregation_method, denominator, source_id)
            VALUES (%s, %s, %s, %s, 'country', 'model', 'none', NULL, %s)
            ON CONFLICT (place_id, indicator_id, year, source_id)
            DO UPDATE SET
                value = EXCLUDED.value,
                support_level = EXCLUDED.support_level
            """,
            (place_id, indicator_id, year, float(value), source_id),
        )
        n_loaded += 1

    return n_loaded, n_skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load WHO GHO indicators into Correlia.",
    )
    parser.add_argument(
        "--indicator",
        action="append",
        default=[],
        help="WHO GHO indicator code (repeatable). Defaults to a curated set.",
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
    except Exception as exc:
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
                        df = who.data(code, spatial_type="COUNTRY", decode=True)
                    except Exception as exc:
                        print(f"  error: fetch failed: {exc}", file=sys.stderr)
                        continue

                    if df is None or df.empty:
                        print(f"  {code}: no data returned")
                        continue

                    df = reduce_to_one_slice(df)
                    name = find_indicator_name(code)
                    indicator_id = get_or_create_indicator(
                        cur, code, name, source_id
                    )
                    loaded, skipped = load_observations(
                        cur, indicator_id, source_id, df
                    )
                    print(
                        f"  {code}: {loaded} observations loaded, "
                        f"{skipped} skipped"
                    )

        print("load completed.")
        return 0

    except Exception as exc:
        print(f"error: load failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
