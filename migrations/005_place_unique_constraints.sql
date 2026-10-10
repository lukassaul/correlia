-- Correlia: unique constraints on places
--
-- These partial unique indexes exist so that the GeoNames loader (and any
-- future loader that ingests places) can use ON CONFLICT upserts and remain
-- idempotent across re-runs.
--
-- Each index is partial (WHERE ... IS NOT NULL) because a row may legitimately
-- have a NULL value in any of these columns — a country row has no geonames_id,
-- a city row has no iso_alpha3, and so on. Without the partial clause, the
-- constraint would fire on every row where the column is null.

CREATE UNIQUE INDEX IF NOT EXISTS places_iso_alpha3_uniq
    ON analysis.places (iso_alpha3)
    WHERE iso_alpha3 IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS places_iso_3166_2_uniq
    ON analysis.places (iso_3166_2)
    WHERE iso_3166_2 IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS places_gdl_code_uniq
    ON analysis.places (gdl_code)
    WHERE gdl_code IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS places_geonames_id_uniq
    ON analysis.places (geonames_id)
    WHERE geonames_id IS NOT NULL;
