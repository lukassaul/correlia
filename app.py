#!/usr/bin/env python3
"""
Correlia web API.

A read-only HTTP interface to the Correlia database. Exposes two endpoints:

  GET  /indicators          list every indicator with metadata and counts
  GET  /correlate           compute Pearson correlation between two indicators
  GET  /health              liveness check

The app connects to the database using the CORRELIA_DSN environment
variable. It should connect as a read-only role (correlia_viewer) in
production. The default DSN in development may use the postgres superuser.

Runs on port 8000 by default.
"""

from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

DSN = os.environ.get("CORRELIA_DSN", "")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_connection():
    if not DSN:
        raise RuntimeError("CORRELIA_DSN is not set")
    return psycopg.connect(DSN)


def pearson(xs: list[float], ys: list[float]) -> dict[str, Any]:
    n = len(xs)
    if n != len(ys):
        raise ValueError("xs and ys must have the same length")
    if n < 2:
        return {"n": n, "r": None, "r_squared": None}

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
        return {
            "n": n,
            "r": None,
            "r_squared": None,
            "mean_a": mean_x,
            "mean_b": mean_y,
        }

    r = cov / math.sqrt(var_x * var_y)
    return {
        "n": n,
        "r": r,
        "r_squared": r * r,
        "mean_a": mean_x,
        "mean_b": mean_y,
        "std_a": math.sqrt(var_x / n),
        "std_b": math.sqrt(var_y / n),
    }


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify the database is reachable at startup.
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        print(f"warning: database not reachable at startup: {exc}")
    yield


app = FastAPI(
    title="Correlia",
    description="Multi-scale geographic correlation discovery.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM analysis.observations")
                n = cur.fetchone()[0]
        return {"status": "ok", "observations": str(n)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}")


@app.get("/indicators")
def list_indicators() -> JSONResponse:
    """
    List every indicator in the database, with its unit, source, and
    observation count.
    """
    sql = """
        SELECT
            i.code,
            i.name,
            i.unit,
            i.description,
            s.title AS source_title,
            count(o.observation_id) AS observation_count,
            min(o.year) AS year_min,
            max(o.year) AS year_max,
            array_agg(DISTINCT o.support_level ORDER BY o.support_level)
                AS support_levels
        FROM analysis.indicators i
        LEFT JOIN analysis.observations o ON o.indicator_id = i.indicator_id
        LEFT JOIN analysis.sources s ON s.source_id = i.source_id
        GROUP BY i.code, i.name, i.unit, i.description, s.title
        ORDER BY i.code
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [c.name for c in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    # Postgres returns arrays as Python lists; JSON handles them fine.
    return JSONResponse({"indicators": rows})


@app.get("/correlate")
def correlate(
    indicator_a: str = Query(..., description="Indicator code for variable A"),
    indicator_b: str = Query(..., description="Indicator code for variable B"),
    support_level: str = Query("country", description="Geographic support level"),
    year_start: int = Query(1960, description="Start year, inclusive"),
    year_end: int = Query(2024, description="End year, inclusive"),
    group_by_year: bool = Query(False, description="Break down by year"),
) -> JSONResponse:
    """
    Compute the Pearson correlation between two indicators at a given
    support level and year range.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Resolve indicator codes to IDs
            cur.execute(
                "SELECT code, indicator_id FROM analysis.indicators "
                "WHERE code = ANY(%s)",
                ([indicator_a, indicator_b],),
            )
            ids = {code: iid for code, iid in cur.fetchall()}
            if indicator_a not in ids:
                raise HTTPException(
                    status_code=404,
                    detail=f"indicator not found: {indicator_a}",
                )
            if indicator_b not in ids:
                raise HTTPException(
                    status_code=404,
                    detail=f"indicator not found: {indicator_b}",
                )

            # Fetch paired observations
            sql = """
                WITH a AS (
                    SELECT place_id, year, value
                    FROM analysis.observations
                    WHERE indicator_id = %(a)s
                      AND support_level = %(level)s
                      AND year BETWEEN %(y0)s AND %(y1)s
                      AND value IS NOT NULL
                ),
                b AS (
                    SELECT place_id, year, value
                    FROM analysis.observations
                    WHERE indicator_id = %(b)s
                      AND support_level = %(level)s
                      AND year BETWEEN %(y0)s AND %(y1)s
                      AND value IS NOT NULL
                )
                SELECT a.place_id, a.year, a.value, b.value
                FROM a JOIN b ON a.place_id = b.place_id AND a.year = b.year
            """
            cur.execute(
                sql,
                {
                    "a": ids[indicator_a],
                    "b": ids[indicator_b],
                    "level": support_level,
                    "y0": year_start,
                    "y1": year_end,
                },
            )
            pairs = cur.fetchall()

    if not pairs:
        return JSONResponse({
            "indicator_a": indicator_a,
            "indicator_b": indicator_b,
            "support_level": support_level,
            "year_start": year_start,
            "year_end": year_end,
            "result": None,
            "message": "no paired observations found",
        })

    if group_by_year:
        by_year: dict[int, list[tuple[float, float]]] = {}
        for _place_id, year, a_val, b_val in pairs:
            by_year.setdefault(year, []).append((float(a_val), float(b_val)))
        results = []
        for year in sorted(by_year):
            xs = [p[0] for p in by_year[year]]
            ys = [p[1] for p in by_year[year]]
            r = pearson(xs, ys)
            results.append({"year": year, **r})
        return JSONResponse({
            "indicator_a": indicator_a,
            "indicator_b": indicator_b,
            "support_level": support_level,
            "results_by_year": results,
        })

    xs = [float(p[2]) for p in pairs]
    ys = [float(p[3]) for p in pairs]
    result = pearson(xs, ys)
    return JSONResponse({
        "indicator_a": indicator_a,
        "indicator_b": indicator_b,
        "support_level": support_level,
        "year_start": year_start,
        "year_end": year_end,
        "result": result,
    })
@app.get("/scan")
def scan(
    indicator: str = Query(..., description="Target indicator code"),
    support_level: str = Query("country"),
    year_start: int = Query(1960),
    year_end: int = Query(2024),
    min_n: int = Query(50, description="Minimum paired observations"),
    limit: int = Query(20, description="Maximum results to return"),
    order: str = Query("abs", description="'abs' for |r|, 'pos' for r, 'neg' for -r"),
) -> JSONResponse:
    """
    Scan every other indicator in the database for correlation with the
    target indicator at the given support level and year range.

    Returns the top-N results by absolute correlation, filtered by
    minimum paired-observation count.
    """
    order_sql = {
        "abs": "ORDER BY abs(r) DESC NULLS LAST",
        "pos": "ORDER BY r DESC NULLS LAST",
        "neg": "ORDER BY r ASC NULLS LAST",
    }.get(order, "ORDER BY abs(r) DESC NULLS LAST")

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Resolve the target indicator
            cur.execute(
                "SELECT indicator_id FROM analysis.indicators WHERE code = %s",
                (indicator,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"indicator not found: {indicator}",
                )
            target_id = row[0]

            sql = f"""
                WITH target AS (
                    SELECT place_id, year, value
                    FROM analysis.observations
                    WHERE indicator_id = %(target)s
                      AND support_level = %(level)s
                      AND year BETWEEN %(y0)s AND %(y1)s
                      AND value IS NOT NULL
                ),
                pairs AS (
                    SELECT
                        o.indicator_id,
                        t.value AS x,
                        o.value AS y
                    FROM target t
                    JOIN analysis.observations o
                      ON o.place_id = t.place_id
                     AND o.year = t.year
                     AND o.support_level = %(level)s
                     AND o.indicator_id != %(target)s
                     AND o.value IS NOT NULL
                ),
                agg AS (
                    SELECT
                        indicator_id,
                        count(*) AS n,
                        corr(x, y) AS r
                    FROM pairs
                    GROUP BY indicator_id
                    HAVING count(*) >= %(min_n)s
                )
                SELECT
                    i.code,
                    i.name,
                    i.unit,
                    s.title AS source_title,
                    a.n,
                    a.r
                FROM agg a
                JOIN analysis.indicators i ON i.indicator_id = a.indicator_id
                LEFT JOIN analysis.sources s ON s.source_id = i.source_id
                WHERE a.r IS NOT NULL
                {order_sql}
                LIMIT %(limit)s
            """
            cur.execute(sql, {
                "target": target_id,
                "level": support_level,
                "y0": year_start,
                "y1": year_end,
                "min_n": min_n,
                "limit": limit,
            })
            columns = [c.name for c in cur.description]
            results = [dict(zip(columns, r)) for r in cur.fetchall()]

            # Count how many indicators were tested in total
            cur.execute("""
                WITH target AS (
                    SELECT place_id, year, value
                    FROM analysis.observations
                    WHERE indicator_id = %(target)s
                      AND support_level = %(level)s
                      AND year BETWEEN %(y0)s AND %(y1)s
                      AND value IS NOT NULL
                )
                SELECT count(DISTINCT o.indicator_id)
                FROM target t
                JOIN analysis.observations o
                  ON o.place_id = t.place_id
                 AND o.year = t.year
                 AND o.support_level = %(level)s
                 AND o.indicator_id != %(target)s
                 AND o.value IS NOT NULL
            """, {
                "target": target_id,
                "level": support_level,
                "y0": year_start,
                "y1": year_end,
            })
            tested = cur.fetchone()[0]

    return JSONResponse({
        "indicator": indicator,
        "support_level": support_level,
        "year_start": year_start,
        "year_end": year_end,
        "min_n": min_n,
        "tested": tested,
        "results": results,
    })
