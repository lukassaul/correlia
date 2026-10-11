#!/usr/bin/env python3
"""
Correlia correlation query.

Computes the Pearson correlation between two indicators over a year range,
at a specified geographic support level. Optionally breaks the result down
by year.

This is a read-only tool. It does not modify the database.

Usage:
  python3 scripts/correlate.py \
      --indicator-a NY.GDP.PCAP.CD \
      --indicator-b SH.ALC.PCAP.LI \
      --support-level country \
      --year-start 2000 --year-end 2020

Exit codes:
  0  correlation computed
  1  query failed
  2  usage error
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass

try:
    import psycopg
except ImportError:
    print(
        "error: psycopg is not installed. Install with: pip install 'psycopg[binary]'",
        file=sys.stderr,
    )
    sys.exit(2)


@dataclass
class CorrelationResult:
    n: int
    r: float | None
    r_squared: float | None
    mean_a: float | None
    mean_b: float | None
    std_a: float | None
    std_b: float | None


def pearson(xs: list[float], ys: list[float]) -> CorrelationResult:
    n = len(xs)
    if n != len(ys):
        raise ValueError("xs and ys must have the same length")
    if n < 2:
        return CorrelationResult(n, None, None, None, None, None, None)

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov = 0.0
    var_x = 0.0
    var_y = 0.0
    for x, y in zip(xs, ys):
        dx = x - mean_x
        dy = y - mean_y
        cov += dx * dy
        var_x += dx * dx
        var_y += dy * dy

    if var_x == 0 or var_y == 0:
        return CorrelationResult(n, None, None, mean_x, mean_y, 0.0, 0.0)

    r = cov / math.sqrt(var_x * var_y)
    return CorrelationResult(
        n=n,
        r=r,
        r_squared=r * r,
        mean_a=mean_x,
        mean_b=mean_y,
        std_a=math.sqrt(var_x / n),
        std_b=math.sqrt(var_y / n),
    )


QUERY = """
WITH a AS (
    SELECT place_id, year, value
    FROM analysis.observations
    WHERE indicator_id = %(indicator_a)s
      AND support_level = %(support_level)s
      AND year BETWEEN %(year_start)s AND %(year_end)s
      AND value IS NOT NULL
),
b AS (
    SELECT place_id, year, value
    FROM analysis.observations
    WHERE indicator_id = %(indicator_b)s
      AND support_level = %(support_level)s
      AND year BETWEEN %(year_start)s AND %(year_end)s
      AND value IS NOT NULL
)
SELECT a.place_id, a.year, a.value, b.value
FROM a
JOIN b ON a.place_id = b.place_id AND a.year = b.year
ORDER BY a.place_id, a.year
"""

QUERY_BY_YEAR = """
WITH a AS (
    SELECT place_id, year, value
    FROM analysis.observations
    WHERE indicator_id = %(indicator_a)s
      AND support_level = %(support_level)s
      AND year BETWEEN %(year_start)s AND %(year_end)s
      AND value IS NOT NULL
),
b AS (
    SELECT place_id, year, value
    FROM analysis.observations
    WHERE indicator_id = %(indicator_b)s
      AND support_level = %(support_level)s
      AND year BETWEEN %(year_start)s AND %(year_end)s
      AND value IS NOT NULL
)
SELECT a.year, a.place_id, a.value, b.value
FROM a
JOIN b ON a.place_id = b.place_id AND a.year = b.year
ORDER BY a.year, a.place_id
"""


def get_indicator_id(cur, code: str) -> int | None:
    cur.execute(
        "SELECT indicator_id FROM analysis.indicators WHERE code = %s",
        (code,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def fetch_pairs(
    cur,
    indicator_a: int,
    indicator_b: int,
    support_level: str,
    year_start: int,
    year_end: int,
    group_by_year: bool,
) -> list[tuple]:
    query = QUERY_BY_YEAR if group_by_year else QUERY
    cur.execute(
        query,
        {
            "indicator_a": indicator_a,
            "indicator_b": indicator_b,
            "support_level": support_level,
            "year_start": year_start,
            "year_end": year_end,
        },
    )
    return cur.fetchall()


def format_result(label: str, result: CorrelationResult) -> str:
    if result.r is None:
        if result.n < 2:
            return f"{label}: n={result.n} (insufficient paired observations)"
        return f"{label}: n={result.n} (zero variance in one or both series)"
    return (
        f"{label}: n={result.n}, r={result.r:+.4f}, r²={result.r_squared:.4f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute Pearson correlation between two Correlia indicators.",
    )
    parser.add_argument(
        "--indicator-a", required=True,
        help="Indicator code for the first variable",
    )
    parser.add_argument(
        "--indicator-b", required=True,
        help="Indicator code for the second variable",
    )
    parser.add_argument(
        "--support-level", default="country",
        help="Geographic support level (default: country)",
    )
    parser.add_argument(
        "--year-start", type=int, default=1960,
        help="Start year, inclusive (default: 1960)",
    )
    parser.add_argument(
        "--year-end", type=int, default=2024,
        help="End year, inclusive (default: 2024)",
    )
    parser.add_argument(
        "--group-by", choices=["year", "none"], default="none",
        help="Group results by year (default: none)",
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

    try:
        conn = psycopg.connect(args.dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot connect to database: {exc}", file=sys.stderr)
        return 2

    try:
        with conn:
            with conn.cursor() as cur:
                a_id = get_indicator_id(cur, args.indicator_a)
                b_id = get_indicator_id(cur, args.indicator_b)
                if a_id is None:
                    print(
                        f"error: indicator not found: {args.indicator_a}",
                        file=sys.stderr,
                    )
                    return 2
                if b_id is None:
                    print(
                        f"error: indicator not found: {args.indicator_b}",
                        file=sys.stderr,
                    )
                    return 2

                group_by_year = args.group_by == "year"
                pairs = fetch_pairs(
                    cur,
                    a_id, b_id,
                    args.support_level,
                    args.year_start, args.year_end,
                    group_by_year,
                )

                print(f"Correlation: {args.indicator_a} vs {args.indicator_b}")
                print(f"Support level: {args.support_level}")
                print(f"Years: {args.year_start}-{args.year_end}")
                print()

                if not pairs:
                    print("No paired observations found.")
                    return 0

                if group_by_year:
                    by_year: dict[int, list[tuple[float, float]]] = {}
                    for year, _place_id, a_val, b_val in pairs:
                        by_year.setdefault(year, []).append(
                            (float(a_val), float(b_val))
                        )
                    for year in sorted(by_year):
                        xs = [p[0] for p in by_year[year]]
                        ys = [p[1] for p in by_year[year]]
                        result = pearson(xs, ys)
                        print(format_result(f"{year}", result))
                else:
                    xs = [float(p[2]) for p in pairs]
                    ys = [float(p[3]) for p in pairs]
                    result = pearson(xs, ys)
                    print(format_result("aggregate", result))
                    if result.r is not None:
                        print()
                        print(f"  mean A:  {result.mean_a:.4g}")
                        print(f"  mean B:  {result.mean_b:.4g}")
                        print(f"  std A:   {result.std_a:.4g}")
                        print(f"  std B:   {result.std_b:.4g}")

        return 0

    except Exception as exc:  # noqa: BLE001
        print(f"error: query failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
