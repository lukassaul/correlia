#!/usr/bin/env python3
"""
Correlia GeoNames loader.

Reads a GeoNames dump file and populates analysis.places with the geographic
units Correlia uses as its canonical backbone: countries, admin-1 regions,
admin-2 regions, and populated places.

GeoNames publishes daily dumps at:
  https://download.geonames.org/export/dump/

The files used by this loader are:

  countryInfo.txt    Country metadata (ISO codes, names)
  admin1CodesASCII.txt   First-level administrative divisions
  admin2Codes.txt    Second-level administrative divisions
  allCountries.txt   All populated places and features
                     (alternatively: cities5000.txt, cities15000.txt, etc.)

Download and unzip them into a directory, then run:

  python scripts/load_geonames.py --data-dir /path/to/geonames --dsn "postgresql://..."

The loader is idempotent: it uses UPSERT semantics so re-running it is safe.
Records are keyed on (geonames_id) and (iso_alpha3) and (iso_3166_2) as
appropriate.

Exit codes:
  0  load completed
  1  load failed (errors reported to stderr)
  2  usage error (missing files, bad arguments, database connection failure)
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import zipfile
from pathlib import Path
from typing import Iterator

try:
    import psycopg
except ImportError:
    print(
        "error: psycopg is not installed. Install with: pip install 'psycopg[binary]'",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# GeoNames file parsing
# ---------------------------------------------------------------------------

# GeoNames feature codes we care about, mapped to Correlia geo_level.
# See https://www.geonames.org/export/codes.html
ADMIN1_FEATURE_CODES = {"ADM1"}
ADMIN2_FEATURE_CODES = {"ADM2"}
COUNTRY_FEATURE_CODES = {"PCLI", "PCLD", "PCLS", "PCLF", "PCLIX", "PCL"}


def open_geonames_file(data_dir: Path, name: str) -> io.TextIOWrapper:
    """
    Open a GeoNames file, transparently handling both the plain .txt form
    and the .zip form that GeoNames also publishes.

    Looks for `name` and `name.zip` in `data_dir`.
    """
    plain = data_dir / name
    if plain.is_file():
        return plain.open(encoding="utf-8")

    zipped = data_dir / f"{name}.zip"
    if zipped.is_file():
        zf = zipfile.ZipFile(zipped)
        inner = zf.namelist()[0]
        return io.TextIOWrapper(zf.open(inner), encoding="utf-8")

    raise FileNotFoundError(f"neither {plain} nor {zipped} found")


def iter_country_info(data_dir: Path) -> Iterator[dict]:
    """
    Parse countryInfo.txt.

    Format (tab-separated, no header):
    0  ISO
    1  ISO3
    2  ISO-Numeric
    3  fips
    4  Country
    5  Capital
    ...
    16 geonameid
    """
    with open_geonames_file(data_dir, "countryInfo.txt") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 17:
                continue
            yield {
                "iso_alpha2": parts[0],
                "iso_alpha3": parts[1],
                "name": parts[4],
                "geonames_id": int(parts[16]) if parts[16] else None,
            }


def iter_admin1(data_dir: Path) -> Iterator[dict]:
    """
    Parse admin1CodesASCII.txt.

    Format (tab-separated, no header):
    0  code        e.g. "US.CA"
    1  name        e.g. "California"
    2  name_ascii
    3  geonameid
    """
    with open_geonames_file(data_dir, "admin1CodesASCII.txt") as f:
        for line in f:
            if not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            code = parts[0]
            if "." not in code:
                continue
            country_code, _ = code.split(".", 1)
            yield {
                "iso_alpha2": country_code,
                "iso_3166_2": code,
                "name": parts[1],
                "geonames_id": int(parts[3]) if parts[3] else None,
            }


def iter_admin2(data_dir: Path) -> Iterator[dict]:
    """
    Parse admin2Codes.txt.

    Format (tab-separated, no header):
    0  code        e.g. "US.CA.037"
    1  name
    2  name_ascii
    3  geonameid
    """
    with open_geonames_file(data_dir, "admin2Codes.txt") as f:
        for line in f:
            if not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            code = parts[0]
            if code.count(".") != 2:
                continue
            country_code, _, _ = code.split(".", 2)
            yield {
                "iso_alpha2": country_code,
                "gdl_code": code,  # stored in gdl_code for admin-2 for now
                "name": parts[1],
                "geonames_id": int(parts[3]) if parts[3] else None,
            }


def iter_places(data_dir: Path, filename: str) -> Iterator[dict]:
    """
    Parse allCountries.txt (or cities*.txt).

    Format (tab-separated, no header; see GeoNames readme):
    0  geonameid
    1  name
    2  asciiname
    3  alternatenames
    4  latitude
    5  longitude
    6  feature_class
    7  feature_code
    8  country_code
    9  cc2
    10 admin1_code
    11 admin2_code
    12 admin3_code
    13 admin4_code
    14 population
    15 elevation
    16 dem
    17 timezone
    18 modification_date
    """
    with open_geonames_file(data_dir, filename) as f:
        for line in f:
            if not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 19:
                continue

            feature_class = parts[6]
            feature_code = parts[7]

            # We only want populated places, and only those with a population.
            if feature_class != "P":
                continue
            try:
                population = int(parts[14]) if parts[14] else 0
            except ValueError:
                population = 0
            if population == 0:
                continue

            yield {
                "geonames_id": int(parts[0]),
                "name": parts[1],
                "latitude": float(parts[4]),
                "longitude": float(parts[5]),
                "country_code": parts[8],
                "admin1_code": parts[10],
                "admin2_code": parts[11],
                "population": population,
                "feature_code": feature_code,
            }


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def upsert_country(cur, c: dict) -> int:
    """Insert or update a country, return its place_id."""
    cur.execute(
        """
        INSERT INTO analysis.places
            (canonical_name, geo_level, iso_alpha3, geonames_id)
        VALUES (%s, 'country', %s, %s)
        ON CONFLICT (place_id) DO NOTHING
        RETURNING place_id
        """,
        (c["name"], c["iso_alpha3"], c["geonames_id"]),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # Already existed; look it up
    cur.execute(
        "SELECT place_id FROM analysis.places WHERE iso_alpha3 = %s",
        (c["iso_alpha3"],),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"country upsert failed for {c['iso_alpha3']}")
    return row[0]


def upsert_admin1(cur, a: dict, parent_id: int) -> int:
    """Insert or update an admin-1 region, return its place_id."""
    cur.execute(
        """
        INSERT INTO analysis.places
            (canonical_name, geo_level, parent_place_id, iso_3166_2, geonames_id)
        VALUES (%s, 'admin1', %s, %s, %s)
        ON CONFLICT (place_id) DO NOTHING
        RETURNING place_id
        """,
        (a["name"], parent_id, a["iso_3166_2"], a["geonames_id"]),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "SELECT place_id FROM analysis.places WHERE iso_3166_2 = %s",
        (a["iso_3166_2"],),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"admin1 upsert failed for {a['iso_3166_2']}")
    return row[0]


def upsert_admin2(cur, a: dict, parent_id: int) -> int:
    """Insert or update an admin-2 region, return its place_id."""
    cur.execute(
        """
        INSERT INTO analysis.places
            (canonical_name, geo_level, parent_place_id, gdl_code, geonames_id)
        VALUES (%s, 'admin2', %s, %s, %s)
        ON CONFLICT (place_id) DO NOTHING
        RETURNING place_id
        """,
        (a["name"], parent_id, a["gdl_code"], a["geonames_id"]),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "SELECT place_id FROM analysis.places WHERE gdl_code = %s",
        (a["gdl_code"],),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"admin2 upsert failed for {a['gdl_code']}")
    return row[0]


def upsert_city(cur, p: dict, parent_id: int | None) -> int:
    """Insert or update a city, return its place_id."""
    cur.execute(
        """
        INSERT INTO analysis.places
            (canonical_name, geo_level, parent_place_id, geonames_id, geom)
        VALUES (
            %s, 'city', %s, %s,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)
        )
        ON CONFLICT (place_id) DO NOTHING
        RETURNING place_id
        """,
        (
            p["name"],
            parent_id,
            p["geonames_id"],
            p["longitude"],
            p["latitude"],
        ),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        "SELECT place_id FROM analysis.places WHERE geonames_id = %s",
        (p["geonames_id"],),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"city upsert failed for {p['geonames_id']}")
    return row[0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load GeoNames data into analysis.places.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Directory containing GeoNames dump files",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("CORRELIA_DSN", ""),
        help="PostgreSQL DSN (default: $CORRELIA_DSN)",
    )
    parser.add_argument(
        "--places-file",
        default="allCountries.txt",
        help="Populated places file (default: allCountries.txt; "
        "try cities5000.txt for a smaller load)",
    )
    args = parser.parse_args()

    if not args.dsn:
        print(
            "error: --dsn or CORRELIA_DSN must be set",
            file=sys.stderr,
        )
        return 2

    if not args.data_dir.is_dir():
        print(f"error: data directory not found: {args.data_dir}", file=sys.stderr)
        return 2

    try:
        conn = psycopg.connect(args.dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot connect to database: {exc}", file=sys.stderr)
        return 2

    try:
        with conn:
            with conn.cursor() as cur:
                # ---- Countries ---------------------------------------------
                country_ids: dict[str, int] = {}
                n_countries = 0
                for c in iter_country_info(args.data_dir):
                    pid = upsert_country(cur, c)
                    country_ids[c["iso_alpha2"]] = pid
                    n_countries += 1
                print(f"countries:  {n_countries}")

                # ---- Admin-1 regions ---------------------------------------
                admin1_ids: dict[str, int] = {}
                n_admin1 = 0
                for a in iter_admin1(args.data_dir):
                    parent_id = country_ids.get(a["iso_alpha2"])
                    if parent_id is None:
                        continue
                    pid = upsert_admin1(cur, a, parent_id)
                    admin1_ids[a["iso_3166_2"]] = pid
                    n_admin1 += 1
                print(f"admin1:     {n_admin1}")

                # ---- Admin-2 regions ---------------------------------------
                admin2_ids: dict[str, int] = {}
                n_admin2 = 0
                for a in iter_admin2(args.data_dir):
                    # derive the parent admin1 code: "US.CA.037" -> "US.CA"
                    parts = a["gdl_code"].split(".")
                    admin1_code = f"{parts[0]}.{parts[1]}"
                    parent_id = admin1_ids.get(admin1_code)
                    if parent_id is None:
                        continue
                    pid = upsert_admin2(cur, a, parent_id)
                    admin2_ids[a["gdl_code"]] = pid
                    n_admin2 += 1
                print(f"admin2:     {n_admin2}")

                # ---- Cities ------------------------------------------------
                n_cities = 0
                for p in iter_places(args.data_dir, args.places_file):
                    # find parent: admin2 if we have it, otherwise admin1,
                    # otherwise country
                    parent_id = None
                    if p["admin2_code"]:
                        key = f"{p['country_code']}.{p['admin1_code']}.{p['admin2_code']}"
                        parent_id = admin2_ids.get(key)
                    if parent_id is None and p["admin1_code"]:
                        key = f"{p['country_code']}.{p['admin1_code']}"
                        parent_id = admin1_ids.get(key)
                    if parent_id is None:
                        parent_id = country_ids.get(p["country_code"])

                    upsert_city(cur, p, parent_id)
                    n_cities += 1
                print(f"cities:     {n_cities}")

        print("load completed.")
        return 0

    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: load failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
