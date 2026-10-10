-- Correlia: core schema

CREATE SCHEMA IF NOT EXISTS analysis;   -- validated, correlation-ready data
CREATE SCHEMA IF NOT EXISTS staging;    -- submissions awaiting review
CREATE SCHEMA IF NOT EXISTS catalog;    -- metadata and vocabulary

-- =========================================================
-- GEOGRAPHIC HIERARCHY
-- =========================================================

CREATE TABLE analysis.places (
    place_id            BIGSERIAL PRIMARY KEY,
    canonical_name      TEXT NOT NULL,
    geo_level           TEXT NOT NULL,           -- 'country','admin1','admin2','city'
    parent_place_id     BIGINT REFERENCES analysis.places(place_id),
    iso_alpha3          CHAR(3),                 -- for countries
    iso_3166_2          TEXT,                    -- for subnational
    geonames_id         BIGINT,                  -- for cities
    gdl_code            TEXT,                    -- stable cross-year subnational code
    geom                GEOMETRY(GEOMETRY, 4326),
    valid_from          DATE NOT NULL DEFAULT '1900-01-01',
    valid_to            DATE,                    -- NULL = still current
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX places_geo_level_idx    ON analysis.places (geo_level);
CREATE INDEX places_parent_idx       ON analysis.places (parent_place_id);
CREATE INDEX places_iso_alpha3_idx   ON analysis.places (iso_alpha3);
CREATE INDEX places_geonames_idx     ON analysis.places (geonames_id);
CREATE INDEX places_geom_idx         ON analysis.places USING GIST (geom);
CREATE INDEX places_name_trgm_idx    ON analysis.places USING GIN (canonical_name gin_trgm_ops);

-- Boundary changes over time
CREATE TABLE analysis.place_mappings (
    mapping_id      BIGSERIAL PRIMARY KEY,
    old_place_id    BIGINT NOT NULL REFERENCES analysis.places(place_id),
    new_place_id    BIGINT NOT NULL REFERENCES analysis.places(place_id),
    mapping_type    TEXT NOT NULL,               -- 'split','merge','rename','redraw'
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    notes           TEXT
);

-- Population, area, and other per-place attributes (time-varying)
CREATE TABLE analysis.geo_attributes (
    attribute_id    BIGSERIAL PRIMARY KEY,
    place_id        BIGINT NOT NULL REFERENCES analysis.places(place_id),
    year            SMALLINT NOT NULL,
    population      BIGINT,
    area_km2        NUMERIC(14,4),
    population_density NUMERIC(14,4),
    source_id       BIGINT,                      -- FK added below
    UNIQUE (place_id, year)
);

-- =========================================================
-- SOURCES (DataCite-aligned)
-- =========================================================

CREATE TABLE analysis.sources (
    source_id               BIGSERIAL PRIMARY KEY,
    datacite_identifier     TEXT,                -- DOI or internal ID
    creator                 TEXT NOT NULL,
    title                   TEXT NOT NULL,
    publisher               TEXT NOT NULL,
    publication_year        SMALLINT NOT NULL,
    resource_type           TEXT NOT NULL DEFAULT 'Dataset',
    description             TEXT,
    rights_license          TEXT,                -- SPDX or URL
    version                 TEXT,
    language                CHAR(2),
    geo_coverage            TEXT,
    related_identifiers     JSONB,
    funding_reference       TEXT,
    date_added_to_db        TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_last_modified      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================================================
-- QUALITY ASSESSMENTS (ESS dimensions)
-- =========================================================

CREATE TABLE analysis.quality_assessments (
    assessment_id   BIGSERIAL PRIMARY KEY,
    source_id       BIGINT NOT NULL REFERENCES analysis.sources(source_id),
    dimension       TEXT NOT NULL,               -- see 004_seed_vocabularies.sql
    rating          SMALLINT CHECK (rating BETWEEN 1 AND 5),
    rationale       TEXT,
    assessed_by     TEXT NOT NULL,
    assessed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX qa_source_idx ON analysis.quality_assessments (source_id, dimension);

-- =========================================================
-- INDICATORS
-- =========================================================

CREATE TABLE analysis.indicators (
    indicator_id    BIGSERIAL PRIMARY KEY,
    code            TEXT UNIQUE NOT NULL,        -- e.g. 'WB_NY.GDP.PCAP.CD'
    name            TEXT NOT NULL,
    unit            TEXT NOT NULL,               -- e.g. 'USD_2015_PPP', 'liters_per_capita'
    description     TEXT,
    source_id       BIGINT REFERENCES analysis.sources(source_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX indicators_source_idx ON analysis.indicators (source_id);

-- =========================================================
-- OBSERVATIONS (the core fact table)
-- =========================================================

CREATE TABLE analysis.observations (
    observation_id      BIGSERIAL PRIMARY KEY,
    place_id            BIGINT NOT NULL REFERENCES analysis.places(place_id),
    indicator_id        BIGINT NOT NULL REFERENCES analysis.indicators(indicator_id),
    year                SMALLINT NOT NULL,
    value               NUMERIC,
    support_level       TEXT NOT NULL,           -- level at which data was actually collected
    collection_method   TEXT,                    -- 'census','survey','administrative','model','satellite'
    aggregation_method  TEXT,                    -- 'sum','mean','pop_weighted_mean','median'
    denominator         TEXT,                    -- what the value is normalized by
    source_id           BIGINT NOT NULL REFERENCES analysis.sources(source_id),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (place_id, indicator_id, year, source_id)
);

CREATE INDEX obs_place_ind_year_idx ON analysis.observations (place_id, indicator_id, year);
CREATE INDEX obs_ind_year_level_idx ON analysis.observations (indicator_id, year, support_level);
CREATE INDEX obs_source_idx         ON analysis.observations (source_id);

-- Add FK from geo_attributes now that sources exists
ALTER TABLE analysis.geo_attributes
    ADD CONSTRAINT geo_attributes_source_fk
    FOREIGN KEY (source_id) REFERENCES analysis.sources(source_id);

-- =========================================================
-- AUDIT LOG
-- =========================================================

CREATE TABLE analysis.audit_log (
    audit_id        BIGSERIAL PRIMARY KEY,
    actor           TEXT NOT NULL,               -- role or agent identifier
    action          TEXT NOT NULL,               -- 'insert','update','delete'
    table_name      TEXT NOT NULL,
    row_id          BIGINT,
    details         JSONB,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX audit_actor_idx     ON analysis.audit_log (actor, occurred_at);
CREATE INDEX audit_table_idx     ON analysis.audit_log (table_name, occurred_at);
