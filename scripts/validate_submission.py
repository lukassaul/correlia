#!/usr/bin/env python3
"""
Correlia submission validator.

Checks a CSV file against the Correlia submission contract defined in
CONTRIBUTING.md. Runs in two modes:

  syntax-only (default):  validates structure, vocabularies, and duplicates
                          using local constants. No database required.

  full:                   additionally connects to a Correlia database and
                          verifies place_ids and indicator_codes resolve to
                          rows in analysis.places and analysis.indicators.

Usage:
  python scripts/validate_submission.py mydata.csv
  python scripts/validate_submission.py mydata.csv --full --dsn "postgresql://..."

Exit codes:
  0  validation passed
  1  validation failed (errors reported to stderr)
  2  usage error (missing file, bad arguments, database connection failure)
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Local vocabulary (mirrors catalog.* in the database).
# Keeping these here lets syntax-only mode run without a DB connection.
# They must stay in sync with migrations/004_seed_vocabularies.sql.
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    "place_id",
    "indicator_code",
    "year",
    "value",
    "support_level",
    "source_identifier",
]

OPTIONAL_COLUMNS = [
    "collection_method",
    "aggregation_method",
    "denominator",
    "notes",
]

GEO_LEVELS = {"country", "admin1", "admin2", "city"}

COLLECTION_METHODS = {
    "census",
    "survey",
    "administrative",
    "model",
    "satellite",
    "other",
}

AGGREGATION_METHODS = {
    "sum",
    "mean",
    "pop_weighted_mean",
    "median",
    "rate",
    "none",
}

YEAR_MIN = 1800
YEAR_MAX = 2100


# ---------------------------------------------------------------------------
# Error collection
# ---------------------------------------------------------------------------

class ValidationErrors:
    """Collects errors with line numbers and produces a readable report."""

    def __init__(self) -> None:
        self.errors: list[tuple[int | None, str]] = []

    def add(self, line: int | None, message: str) -> None:
        self.errors.append((line, message))

    def has_errors(self) -> bool:
        return bool(self.errors)

    def report(self) -> str:
        lines = []
        for line, message in self.errors:
            prefix = f"line {line}: " if line is not None else ""
            lines.append(f"  {prefix}{message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Structure and value checks (no database required)
# ---------------------------------------------------------------------------

def check_columns(header: list[str], errors: ValidationErrors) -> None:
    """Verify required columns are present and no unknown columns exist."""
    header_set = {h.strip() for h in header}

    for col in REQUIRED_COLUMNS:
        if col not in header_set:
            errors.add(None, f"missing required column: '{col}'")

    known = set(REQUIRED_COLUMNS) | set(OPTIONAL_COLUMNS)
    for col in header_set:
        if col not in known:
            errors.add(None, f"unknown column: '{col}'")


def check_row(
    row: dict[str, str],
    line: int,
    errors: ValidationErrors,
    seen_keys: set[tuple[str, str, str]],
) -> None:
    """Validate a single row. `line` is the 1-based line number in the file."""
    place_id = (row.get("place_id") or "").strip()
    indicator_code = (row.get("indicator_code") or "").strip()
    year = (row.get("year") or "").strip()
    value = (row.get("value") or "").strip()
    support_level = (row.get("support_level") or "").strip()
    source_identifier = (row.get("source_identifier") or "").strip()

    # place_id must be a positive integer
    if not place_id:
        errors.add(line, "place_id is empty")
    else:
        try:
            pid = int(place_id)
            if pid <= 0:
                errors.add(line, f"place_id must be positive, got {pid}")
        except ValueError:
            errors.add(line, f"place_id is not an integer: '{place_id}'")

    # indicator_code must be non-empty
    if not indicator_code:
        errors.add(line, "indicator_code is empty")

    # year must be an integer in range
    if not year:
        errors.add(line, "year is empty")
    else:
        try:
            y = int(year)
            if not (YEAR_MIN <= y <= YEAR_MAX):
                errors.add(
                    line,
                    f"year {y} is outside the allowed range {YEAR_MIN}-{YEAR_MAX}",
                )
        except ValueError:
            errors.add(line, f"year is not an integer: '{year}'")

    # value must be numeric
    if not value:
        errors.add(line, "value is empty")
    else:
        try:
            float(value)
        except ValueError:
            errors.add(line, f"value is not numeric: '{value}'")

    # support_level must be in the vocabulary
    if not support_level:
        errors.add(line, "support_level is empty")
    elif support_level not in GEO_LEVELS:
        errors.add(
            line,
            f"support_level '{support_level}' not in {sorted(GEO_LEVELS)}",
        )

    # source_identifier must be non-empty
    if not source_identifier:
        errors.add(line, "source_identifier is empty")

    # optional: collection_method
    collection_method = (row.get("collection_method") or "").strip()
    if collection_method and collection_method not in COLLECTION_METHODS:
        errors.add(
            line,
            f"collection_method '{collection_method}' not in "
            f"{sorted(COLLECTION_METHODS)}",
        )

    # optional: aggregation_method
    aggregation_method = (row.get("aggregation_method") or "").strip()
    if aggregation_method and aggregation_method not in AGGREGATION_METHODS:
        errors.add(
            line,
            f"aggregation_method '{aggregation_method}' not in "
            f"{sorted(AGGREGATION_METHODS)}",
        )

    # duplicate detection within the file
    key = (place_id, indicator_code, year)
    if place_id and indicator_code and year:
        if key in seen_keys:
            errors.add(
                line,
                f"duplicate (place_id, indicator_code, year): {key}",
            )
        else:
            seen_keys.add(key)


# ---------------------------------------------------------------------------
# Database checks (full mode only)
# ---------------------------------------------------------------------------

def check_against_database(
    rows: list[dict[str, str]],
    dsn: str,
    errors: ValidationErrors,
) -> None:
    """Verify place_ids and indicator_codes exist in the database."""
    try:
        import psycopg
    except ImportError:
        errors.add(None, "psycopg is not installed; cannot run --full mode")
        return

    place_ids: set[int] = set()
    indicator_codes: set[str] = set()

    for row in rows:
        pid = (row.get("place_id") or "").strip()
        if pid:
            try:
                place_ids.add(int(pid))
            except ValueError:
                pass  # already reported by check_row
        code = (row.get("indicator_code") or "").strip()
        if code:
            indicator_codes.add(code)

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Check place_ids
                if place_ids:
                    cur.execute(
                        "SELECT place_id FROM analysis.places WHERE place_id = ANY(%s)",
                        (list(place_ids),),
                    )
                    found = {r[0] for r in cur.fetchall()}
                    missing = place_ids - found
                    for pid in sorted(missing):
                        errors.add(None, f"place_id {pid} not found in analysis.places")

                # Check indicator_codes
                if indicator_codes:
                    cur.execute(
                        "SELECT code FROM analysis.indicators WHERE code = ANY(%s)",
                        (list(indicator_codes),),
                    )
                    found = {r[0] for r in cur.fetchall()}
                    missing = indicator_codes - found
                    for code in sorted(missing):
                        errors.add(
                            None,
                            f"indicator_code '{code}' not found in analysis.indicators",
                        )
    except Exception as exc:  # noqa: BLE001
        errors.add(None, f"database check failed: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a Correlia submission CSV against the submission contract.",
    )
    parser.add_argument("csv_file", type=Path, help="Path to the CSV file to validate")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also verify identifiers against a Correlia database",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("CORRELIA_DSN", ""),
        help="PostgreSQL DSN for --full mode "
        "(default: $CORRELIA_DSN)",
    )
    args = parser.parse_args()

    if not args.csv_file.is_file():
        print(f"error: file not found: {args.csv_file}", file=sys.stderr)
        return 2

    if args.full and not args.dsn:
        print(
            "error: --full requires --dsn or CORRELIA_DSN to be set",
            file=sys.stderr,
        )
        return 2

    errors = ValidationErrors()

    with args.csv_file.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print("error: CSV file has no header row", file=sys.stderr)
            return 2

        check_columns(reader.fieldnames, errors)

        rows: list[dict[str, str]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for i, row in enumerate(reader, start=2):  # line 1 is the header
            rows.append(row)
            check_row(row, i, errors, seen_keys)

    if args.full and rows:
        check_against_database(rows, args.dsn, errors)

    if errors.has_errors():
        print(
            f"validation failed: {len(errors.errors)} error(s)",
            file=sys.stderr,
        )
        print(errors.report(), file=sys.stderr)
        return 1

    n = len(rows)
    mode = "full" if args.full else "syntax-only"
    print(f"validation passed: {n} row(s) checked ({mode} mode)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
