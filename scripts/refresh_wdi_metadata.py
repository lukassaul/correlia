#!/usr/bin/env python3
"""
Refresh indicator metadata from the World Bank API.

For every indicator whose source is World Bank WDI, fetch the canonical
name, unit, and description from the World Bank indicator API and update
the local record.
"""

import os
import sys
import psycopg
import requests

DSN = os.environ.get("CORRELIA_DSN", "")
API = "https://api.worldbank.org/v2/indicator"

if not DSN:
    print("error: CORRELIA_DSN is not set", file=sys.stderr)
    sys.exit(2)

with psycopg.connect(DSN) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT i.indicator_id, i.code
            FROM analysis.indicators i
            JOIN analysis.sources s ON s.source_id = i.source_id
            WHERE s.creator = 'World Bank'
        """)
        rows = cur.fetchall()

        for indicator_id, code in rows:
            url = f"{API}/{code}?format=json"
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                print(f"  {code}: fetch failed ({exc})")
                continue

            # Response is [metadata, [indicator_record]]
            if not isinstance(payload, list) or len(payload) < 2:
                print(f"  {code}: unexpected response shape")
                continue
            record = payload[1][0] if payload[1] else None
            if not record:
                print(f"  {code}: no metadata")
                continue

            name = record.get("name") or code
            unit = record.get("unit") or "unknown"
            description = record.get("sourceNote") or ""

            cur.execute("""
                UPDATE analysis.indicators
                SET name = %s, unit = %s, description = %s
                WHERE indicator_id = %s
            """, (name, unit, description, indicator_id))
            print(f"  {code}: {name[:60]}")

        conn.commit()
