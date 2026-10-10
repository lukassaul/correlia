# Correlia

**Multi-scale geographic correlation discovery.**

Correlia is a curated, open-access database for exploring correlations across
disparate geographic and statistical datasets. It is designed to let
researchers ask questions like *"what correlates with skin cancer?"* across
thousands of indicators — at the country, regional, and city level — and to
assess whether the relationships they find are robust or artifacts of
aggregation.

The project is in early development. The schema is stable; the ingestion
pipeline and query layer are being built.

---

## Why Correlia Exists

Statistical data is collected on wildly different geographic units and time
periods. Country-level GDP, city-level alcohol consumption, regional
biodiversity indices, and local price surveys all live in separate silos,
keyed to incompatible identifiers. This makes correlation studies hard, and
makes it easy to mistake an artifact of aggregation for a real relationship.

Correlia addresses this by:

- **Canonicalizing geography.** Every observation is anchored to a place in a
  nested hierarchy (country → admin1 → admin2 → city), using stable
  identifiers (ISO 3166, GeoNames, GDL codes).
- **Preserving support level.** Each observation records the geographic level
  at which it was *actually collected*. Nothing is silently disaggregated or
  aggregated.
- **Making provenance first-class.** Every value traces back to a citable
  source record with bibliographic metadata and quality assessments.
- **Supporting multi-scale queries.** The same correlation can be computed at
  every available support level, exposing Simpson's Paradox and other
  scale-dependent artifacts.

## Architecture

- **PostgreSQL + PostGIS** — single instance, spatial indexes, full SQL.
- **Schemas** — `analysis` (validated data), `staging` (submissions under
  review), `catalog` (controlled vocabularies and metadata).
- **Access model** — public read access; curated write access for approved
  uploaders (human or agent).
- **Row-level security** — uploaders may only insert rows for sources they
  own. All writes are logged.

## Core Tables

| Table | Purpose |
|-------|---------|
| `analysis.places` | Canonical geographic units with nested hierarchy |
| `analysis.place_mappings` | Boundary changes over time (split, merge, redraw) |
| `analysis.geo_attributes` | Time-varying place attributes (population, area) |
| `analysis.sources` | DataCite-aligned bibliographic records |
| `analysis.quality_assessments` | ESS-dimension quality ratings per source |
| `analysis.indicators` | Indicator definitions with units and descriptions |
| `analysis.observations` | The core fact table |
| `analysis.audit_log` | Every write, by every actor |

Full schema documentation lives in [`docs/schema.md`](docs/schema.md).

## Access Model

| Role | Read | Write |
|------|------|-------|
| `correlia_viewer` | All validated data | None |
| `correlia_uploader` | All validated data | Own sources only |
| `correlia_admin` | Everything | Everything |

Uploaders are provisioned individually and mapped to the sources they are
authorized to contribute to. See [`docs/access.md`](docs/access.md).

## Contributing Data

All contributions must conform to the **submission contract**:

- **Canonical geographic identifiers** — ISO 3166-1 alpha-3 for countries,
  ISO 3166-2 for subnational regions, GeoNames ID for cities.
- **Explicit support level** — the geographic level at which data was
  collected.
- **Explicit units and time periods** — ISO 8601 years and controlled unit
  vocabulary.
- **Provenance** — source URL, retrieval date, and license.
- **Quality self-assessment** — ratings on the ESS dimensions.

A validator CLI is provided so contributors can check their files locally
before upload:

```bash
python scripts/validate_submission.py mydata.csv
