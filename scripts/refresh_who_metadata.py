#!/usr/bin/env python3
"""
Refresh indicator metadata for WHO GHO indicators.

Reads every indicator whose source is the WHO Global Health Observatory,
looks up its canonical name in the scigantic-who indicator catalog, and
updates the local record.
"""

import os
import sys
import psycopg
import scigantic_who as who

DSN = os.environ.get("CORRELIA_DSN", "")
if not DSN:
    print("error: CORRELIA_DSN is not set", file=sys.stderr)
    sys.exit(2)

# Fetch the full indicator catalog once.
print("fetching indicator catalog ...")
catalog = who.indicators()
print(f"  {len(catalog)} indicators in catalog")

with psycopg.connect(DSN) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT i.indicator_id, i.code
            FROM analysis.indicators i
            JOIN analysis.sources s ON s.source_id = i.source_id
            WHERE s.creator = 'World Health Organization'
        """)
        rows = cur.fetchall()

        for indicator_id, code in rows:
            match = catalog[catalog["IndicatorCode"] == code]
            if match.empty:
                print(f"  {code}: not in catalog, skipping")
                continue

            name = str(match.iloc[0]["IndicatorName"])
            # Unit is embedded in the name; leave unit as unknown.
            cur.execute("""
                UPDATE analysis.indicators
                SET name = %s,
                    description = %s
                WHERE indicator_id = %s
            """, (name, f"WHO GHO indicator {code}.", indicator_id))
            print(f"  {code}: {name[:70]}")

        conn.commit()

print("done.")
