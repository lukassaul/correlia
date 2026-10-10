# Correlia Deployment Notes

This document records the state of the Correlia project at the point where
local development was complete and deployment to a public VPS was pending.
It exists so that work can be resumed without reconstructing the context.

Last updated: 2026-10-10

---

## Where the Project Stands

The schema is proven against real data, and the full pipeline from source
to correlation query works end to end on a local machine.

### What is running locally

- PostgreSQL 16 with PostGIS 3.4, running in a Docker container named
  `correlia-db`.
- All migrations (`001` through `005`) applied successfully.
- GeoNames data loaded into `analysis.places`:
  - 252 countries
  - 3,865 admin-1 regions
  - 47,642 admin-2 regions
  - 69,712 cities (population >= 5000)
- World Bank WDI indicators loaded into `analysis.observations`:
  - `NY.GDP.PCAP.CD` — GDP per capita
  - `SP.POP.TOTL` — Total population
  - `SP.DYN.LE00.IN` — Life expectancy at birth
  - `SH.ALC.PCAP.LI` — Alcohol consumption per capita
- The first real correlation query returned a result:
  - GDP per capita vs alcohol consumption, country level, 2000-2020:
    `n=3859, r=+0.3782, r²=0.1431`

### Repository state

The GitHub repository at `https://github.com/lukassaul/correlia` contains:

- `README.md`, `LICENSE`, `.gitignore`, `CONTRIBUTING.md`
- `requirements.txt`
- `migrations/001` through `005`
- `docs/schema.md`, `docs/access.md`
- `scripts/validate_submission.py`, `scripts/load_geonames.py`,
  `scripts/load_wdi.py`, `scripts/correlate.py`
- `tests/fixtures/` with sample CSVs for the validator

The `data/` directory is gitignored and contains downloaded GeoNames
files. It is not part of the repository.

---

## What is Pending

### 1. Deployment to a VPS

The database needs to live somewhere reachable. The plan is a single VPS
running Docker with two containers: PostGIS and a web application.

**Chosen host**: VPSLab (`vpslab.io`), accepting Bitcoin and other
cryptocurrencies. The plan under consideration is 2 vCores / 12 GB RAM /
60 GB NVMe for approximately €6.99/month. This is sufficient for the
pilot scale.

**Alternatives considered**:
- BitLaunch (accepts Bitcoin, resells DigitalOcean/Vultr/Linode)
- SurfVPS (prepaid balance model, similar reseller approach)
- RamNode, Cinfu (cheaper, less convenient)

**Deployment sequence** (not yet started):
1. Build the web interface locally first, test against the local database.
2. Provision the VPS, install Docker.
3. Create `docker-compose.yml` with PostGIS and the web app.
4. Add Caddy as a reverse proxy with automatic HTTPS.
5. Configure daily `pg_dump` backups, copied off the VPS.
6. Harden PostgreSQL: `listen_addresses = 'localhost'`, strict
   `pg_hba.conf` rules, no public port 5432.

### 2. The web interface

The database has no UI. Users cannot see what indicators exist or run
correlations without the CLI scripts. A minimal web application is needed.

**Chosen approach**: FastAPI application with two endpoints:
- `GET /indicators` — lists every indicator with code, name, unit, and
  observation count.
- `POST /correlate` — takes two indicator codes, a support level, and a
  year range; returns the Pearson correlation.

This reuses the query logic already written in `scripts/correlate.py`.
Build it locally first.

**Alternatives considered**: Metabase or Superset (heavier, expose the full
schema including audit tables), static HTML + JSON API (more work).

### 3. Additional loaders

The following data sources have been identified but not yet loaded:
- WHO Global Health Observatory (API-based)
- UNdata (SDMX REST API)

The pattern established by `load_wdi.py` should generalize: fetch from
API, register source and indicators, insert observations with
`ON CONFLICT` upserts.

### 4. Subnational data

The current loaders only populate country-level observations
(`support_level = 'country'`). This means the multi-scale diagnostic —
running the same correlation at country, admin1, admin2, and city
levels — is not yet possible.

To enable it, at least one subnational source must be loaded. Candidates
include GADM-based regional datasets, Eurostat NUTS data, or city-level
surveys from specific countries.

### 5. The scan script

`scripts/scan_correlations.py` does not yet exist. It should take one
indicator and test it against every other indicator at a given support
level, reporting the top-N by absolute correlation. This is the "what
correlates with X" query in practice — the core use case for the
database.

The script should report `n` for each pair, and the documentation should
note that running it across hundreds of indicators will produce false
positives by design.

---

## Design Decisions Recorded

These are decisions made during design that should not be revisited
without reason.

### Multi-support levels are non-negotiable

Data is stored at the geographic level at which it was **actually
collected** (`support_level` column in `observations`). Nothing is
silently disaggregated or aggregated. This is what makes the multi-scale
diagnostic possible and what protects against Simpson's Paradox artifacts.

### Aggregation is the researcher's decision

The database does not provide default aggregation rules. If a researcher
wants to aggregate city-level data to country level, they choose the
method. The database provides the raw data and the metadata needed to
decide.

### The contribution contract is enforced pre-upload

Contributors (human or agent) validate their CSVs locally with
`validate_submission.py` before upload. This keeps the database clean and
makes every observation traceable to a canonical place identifier.

### Access is public-read, curated-write

Anyone may read. Only approved uploaders may write, and only for sources
they own. This is enforced by PostgreSQL roles and row-level security,
not by application code.

### Correlation is not causation

The database is a hypothesis-generation engine. Exploratory correlation
queries across many indicators will produce false positives. This is a
property of multiple testing, not a defect. The multi-scale design helps
filter obvious artifacts, but a robust correlation across scales is still
not proof of a causal relationship.

---

## Environment Notes

### Local development

```bash
# Database container
docker run --name correlia-db \
  -e POSTGRES_PASSWORD=correlia \
  -e POSTGRES_DB=correlia \
  -p 5432:5432 \
  -d postgis/postgis:16-3.4

# Python environment
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

# Connection string
export CORRELIA_DSN="postgresql://postgres:correlia@localhost:5432/correlia"
