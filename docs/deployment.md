# Correlia Deployment Notes

This document records the state of the Correlia project so that work can
be resumed without reconstructing the context.

Last updated: 2026-10-13

---

## Where the Project Stands

The database is deployed on a public VPS, populated with two authoritative
sources, and served by a working web interface at `https://correlia.org`.

### Public interface

| URL | Purpose |
|-----|---------|
| `https://correlia.org` | Frontend: correlate two indicators, scan for correlates |
| `https://correlia.org/indicators` | JSON list of every indicator |
| `https://correlia.org/correlate` | JSON Pearson correlation between two indicators |
| `https://correlia.org/scan` | JSON top-N correlates for one indicator |
| `https://correlia.org/health` | Liveness check with observation count |

### Data loaded

| Source | Indicators | Observations |
|--------|-----------:|-------------:|
| GeoNames (places backbone) | — | 121,477 places |
| World Bank WDI | 23 | ~166,000 |
| WHO Global Health Observatory | 8 | ~44,000 |
| **Total** | **31** | **~210,000** |

### Repository

GitHub: `https://github.com/lukassaul/correlia`

Top-level contents:

- `README.md`, `LICENSE`, `.gitignore`, `CONTRIBUTING.md`, `requirements.txt`
- `app.py`, `Dockerfile`, `docker-compose.yml`, `Caddyfile`
- `migrations/` — five SQL migrations
- `docs/` — `schema.md`, `access.md`, `deployment.md`
- `frontend/` — `index.html`
- `scripts/` — loaders, validators, CLI tools
- `tests/fixtures/` — sample CSVs for the validator

The `data/` directory is gitignored. It contains GeoNames downloads and is
not part of the repository.

---

## VPS Details

- **Host**: VPSLab, paid in Bitcoin
- **Domain**: `correlia.org`, A record at Namecheap pointing to the VPS IP
- **Stack**: Docker Compose running three services: `db`, `api`, `caddy`
- **HTTPS**: Caddy provisions and renews TLS certificates automatically

### Services

| Service | Image | Purpose |
|---------|-------|---------|
| `db` | `postgis/postgis:16-3.4` | PostgreSQL 16 + PostGIS 3.4 |
| `api` | built from `Dockerfile` | FastAPI app on port 8000 |
| `caddy` | `caddy:2-alpine` | Reverse proxy + static frontend |

### Volumes

| Volume | Contents |
|--------|----------|
| `correlia_pgdata` | PostgreSQL data directory |
| `caddy_data` | TLS certificates |
| `caddy_config` | Caddy runtime state |

The `correlia_pgdata` volume is pinned by name in `docker-compose.yml`
(`name: correlia_pgdata`) so that Compose does not prefix it with the project
name. Without this, running Compose from a differently-named directory
creates a new empty volume and loses the data.

---

## Known Issues and Fixes

### IPv6 resolver fix (2026-10-13)

The VPSLab VPS was provisioned with global IPv6 addresses on `eth0`, but
the upstream network does not route IPv6 traffic. Every outbound connection
attempted IPv6 first, hung for roughly 5 seconds, then fell back to IPv4.
This made pip, the World Bank API, and DuckDB extension downloads appear
"slow" even though the VPS network itself is fast (6.7 ms to 1.1.1.1,
24 MB/s from Cloudflare).

**Fix**: uncomment the precedence line in `/etc/gai.conf`:

    precedence ::ffff:0:0/96  100

This makes the resolver prefer IPv4 for dual-stack hosts. Verified by the
WHO loader completing in seconds instead of timing out.

### Compose volume prefixing

Docker Compose prefixes volume names with the project name by default,
which is derived from the directory. Running Compose from `/srv/correlia/src`
creates volumes named `src_correlia_pgdata` instead of `correlia_pgdata`.

**Fix**: pin the name in `docker-compose.yml`:

```yaml
volumes:
  correlia_pgdata:
    name: correlia_pgdata
