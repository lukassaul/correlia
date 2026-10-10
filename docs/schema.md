# Correlia Schema Reference

This document describes every table in the Correlia database: its purpose,
columns, constraints, and relationships to other tables.

The schema is defined by the migrations in `migrations/`. Apply them in order:

1. `001_extensions.sql` — PostGIS and supporting extensions
2. `002_core_schema.sql` — tables and indexes
3. `003_roles_and_permissions.sql` — roles, RLS, and access control
4. `004_seed_vocabularies.sql` — controlled vocabularies

---

## Schemas

Correlia uses three PostgreSQL schemas to separate concerns:

| Schema | Purpose |
|--------|---------|
| `analysis` | Validated, correlation-ready data |
| `staging` | Submissions under review, before promotion |
| `catalog` | Controlled vocabularies and reference data |

All analysis queries run against `analysis`. Nothing in `staging` is
guaranteed to be clean or complete, and nothing in `staging` appears in
analysis results.

---

## Geographic Hierarchy

### `analysis.places`

The canonical table of geographic units. Every observation in the database
resolves to a row here. Places are nested: a city has a parent admin-2, which
has a parent admin-1, which has a parent country.

| Column | Type | Notes |
|--------|------|-------|
| `place_id` | BIGSERIAL | Primary key |
| `canonical_name` | TEXT | Not null |
| `geo_level` | TEXT | Not null; one of `catalog.vocab_geo_level` |
| `parent_place_id` | BIGINT | FK to `places.place_id`; null for countries |
| `iso_alpha3` | CHAR(3) | For countries |
| `iso_3166_2` | TEXT | For admin-1 regions |
| `geonames_id` | BIGINT | For cities and other populated places |
| `gdl_code` | TEXT | Stable cross-year subnational code |
| `geom` | GEOMETRY | EPSG:4326 |
| `valid_from` | DATE | Defaults to `1900-01-01` |
| `valid_to` | DATE | Null means still current |
| `created_at` | TIMESTAMPTZ | Defaults to `now()` |

**Indexes**: `geo_level`, `parent_place_id`, `iso_alpha3`, `geonames_id`,
`geom` (GiST), `canonical_name` (trigram).

**Design note**: `valid_from` and `valid_to` allow the same place to have
multiple rows across time if boundaries change. This is how Correlia handles
boundary redraws without breaking panel studies.

### `analysis.place_mappings`

Records how places map to each other across boundary changes: splits,
merges, renames, and redraws.

| Column | Type | Notes |
|--------|------|-------|
| `mapping_id` | BIGSERIAL | Primary key |
| `old_place_id` | BIGINT | FK to `places.place_id` |
| `new_place_id` | BIGINT | FK to `places.place_id` |
| `mapping_type` | TEXT | `split`, `merge`, `rename`, `redraw` |
| `valid_from` | DATE | Not null |
| `valid_to` | DATE | Null means still valid |
| `notes` | TEXT | Explanation of the change |

### `analysis.geo_attributes`

Time-varying attributes of a place: population, area, density. Kept separate
from `observations` because these are **place properties**, not statistical
indicators about the place.

| Column | Type | Notes |
|--------|------|-------|
| `attribute_id` | BIGSERIAL | Primary key |
| `place_id` | BIGINT | FK to `places.place_id` |
| `year` | SMALLINT | Not null |
| `population` | BIGINT | |
| `area_km2` | NUMERIC(14,4) | |
| `population_density` | NUMERIC(14,4) | |
| `source_id` | BIGINT | FK to `sources.source_id` |

**Unique constraint**: `(place_id, year)`.

**Design note**: Population is used to weight aggregations. Keeping it as a
time-varying attribute (rather than a column on `places`) is essential for
panel studies that span years.

---

## Sources and Quality

### `analysis.sources`

Bibliographic records for every dataset in the database, aligned with the
DataCite Metadata Schema.

| Column | Type | Notes |
|--------|------|-------|
| `source_id` | BIGSERIAL | Primary key |
| `datacite_identifier` | TEXT | DOI or internal ID |
| `creator` | TEXT | Not null |
| `title` | TEXT | Not null |
| `publisher` | TEXT | Not null |
| `publication_year` | SMALLINT | Not null |
| `resource_type` | TEXT | Defaults to `Dataset` |
| `description` | TEXT | Abstract and methodology |
| `rights_license` | TEXT | SPDX identifier or URL |
| `version` | TEXT | |
| `language` | CHAR(2) | ISO 639-1 |
| `geo_coverage` | TEXT | Bounding box or description |
| `related_identifiers` | JSONB | Related DOIs, URLs |
| `funding_reference` | TEXT | Funder and grant number |
| `date_added_to_db` | TIMESTAMPTZ | Defaults to `now()` |
| `date_last_modified` | TIMESTAMPTZ | Defaults to `now()` |

**Design note**: The five DataCite mandatory properties are `creator`,
`title`, `publisher`, `publication_year`, and an identifier. `date_added_to_db`
is the catalog record creation date, distinct from the source's own
publication date.

### `analysis.quality_assessments`

Per-source quality ratings along the ESS dimensions.

| Column | Type | Notes |
|--------|------|-------|
| `assessment_id` | BIGSERIAL | Primary key |
| `source_id` | BIGINT | FK to `sources.source_id` |
| `dimension` | TEXT | One of `catalog.vocab_quality_dimension` |
| `rating` | SMALLINT | 1–5 |
| `rationale` | TEXT | Explanation of the rating |
| `assessed_by` | TEXT | Contributor identifier |
| `assessed_at` | TIMESTAMPTZ | Defaults to `now()` |

**Design note**: Multiple assessments per source are allowed. This lets
different reviewers rate the same source on different dimensions, or the
same dimension at different times.

---

## Indicators

### `analysis.indicators`

Definitions for every statistical indicator in the database.

| Column | Type | Notes |
|--------|------|-------|
| `indicator_id` | BIGSERIAL | Primary key |
| `code` | TEXT | Unique; e.g. `WB_NY.GDP.PCAP.CD` |
| `name` | TEXT | Human-readable name |
| `unit` | TEXT | e.g. `USD_2015_PPP`, `liters_per_capita` |
| `description` | TEXT | Methodology and caveats |
| `source_id` | BIGINT | FK to `sources.source_id` |
| `created_at` | TIMESTAMPTZ | Defaults to `now()` |

**Design note**: Units live on the indicator, not on each observation. This
prevents the same indicator from being recorded in inconsistent units across
rows.

---

## Observations

### `analysis.observations`

The core fact table. Each row is a single value of a single indicator for a
single place in a single year, attributed to a single source.

| Column | Type | Notes |
|--------|------|-------|
| `observation_id` | BIGSERIAL | Primary key |
| `place_id` | BIGINT | FK to `places.place_id` |
| `indicator_id` | BIGINT | FK to `indicators.indicator_id` |
| `year` | SMALLINT | ISO 8601 |
| `value` | NUMERIC | |
| `support_level` | TEXT | Level at which data was collected |
| `collection_method` | TEXT | See `catalog.vocab_collection_method` |
| `aggregation_method` | TEXT | See `catalog.vocab_aggregation_method` |
| `denominator` | TEXT | What the value is normalized by |
| `source_id` | BIGINT | FK to `sources.source_id` |
| `notes` | TEXT | Row-level caveats |
| `created_at` | TIMESTAMPTZ | Defaults to `now()` |

**Unique constraint**: `(place_id, indicator_id, year, source_id)`.

**Indexes**: `(place_id, indicator_id, year)`, `(indicator_id, year,
support_level)`, `(source_id)`.

**Design note**: The `support_level` column records the geographic level at
which the data was actually collected, which may be coarser than the level of
the `place_id` it is attached to. For example, a country-level GDP figure
might be attached to the country's capital city for mapping purposes, but
`support_level` remains `country`. Queries that correlate variables should
filter or group by `support_level` to avoid spurious cross-scale joins.

---

## Audit

### `analysis.audit_log`

Every write to the database, by every actor.

| Column | Type | Notes |
|--------|------|-------|
| `audit_id` | BIGSERIAL | Primary key |
| `actor` | TEXT | Role or agent identifier |
| `action` | TEXT | `insert`, `update`, `delete` |
| `table_name` | TEXT | Affected table |
| `row_id` | BIGINT | Affected row |
| `details` | JSONB | Before/after or context |
| `occurred_at` | TIMESTAMPTZ | Defaults to `now()` |

**Indexes**: `(actor, occurred_at)`, `(table_name, occurred_at)`.

**Design note**: The audit log is append-only. It is never truncated in
normal operation.

---

## Access Control

### `analysis.source_owners`

Maps uploader roles to the sources they are authorized to contribute to.
Referenced by the row-level security policy on `observations`.

| Column | Type | Notes |
|--------|------|-------|
| `source_id` | BIGINT | FK to `sources.source_id` |
| `owner_role` | NAME | PostgreSQL role name |
| `granted_at` | TIMESTAMPTZ | Defaults to `now()` |

**Primary key**: `(source_id, owner_role)`.

---

## Controlled Vocabularies

All vocabularies live in the `catalog` schema and are seeded by
`004_seed_vocabularies.sql`.

| Table | Purpose |
|-------|---------|
| `catalog.vocab_geo_level` | `country`, `admin1`, `admin2`, `city` |
| `catalog.vocab_collection_method` | `census`, `survey`, `administrative`, `model`, `satellite`, `other` |
| `catalog.vocab_aggregation_method` | `sum`, `mean`, `pop_weighted_mean`, `median`, `rate`, `none` |
| `catalog.vocab_quality_dimension` | ESS dimensions: `relevance`, `accuracy`, `timeliness`, `comparability`, `coherence`, `accessibility`, `lineage`, `completeness` |

---

## Entity Relationships
