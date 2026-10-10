# Contributing to Correlia

Correlia accepts data contributions from a curated set of uploaders — human
researchers, institutional data stewards, and authorized AI agents. This
document defines the **submission contract**: the format and metadata every
contribution must satisfy before it can enter the `analysis` schema.

If you are reading this as a prospective contributor, read it in full before
preparing your first submission. If you are reading it as a developer working
on the validator or the ingestion pipeline, this is the specification your
code must enforce.

---

## 1. Who Can Contribute

Correlia operates on a **curated upload model**:

- **Anyone** may read the database (public `correlia_viewer` role).
- **Approved uploaders** may insert rows into `analysis.observations`,
  `analysis.sources`, and `analysis.indicators`.
- **Admins** manage roles, sources, and schema.

Uploaders are provisioned individually. Each uploader is mapped to one or more
`source_id` values in `analysis.source_owners`, and row-level security
enforces that they can only insert observations attributed to those sources.

To request uploader access, open an issue describing:

- The datasets you intend to contribute.
- The geographic and temporal coverage.
- The license under which the data is distributed.
- Whether you are a human contributor or an automated agent.

## 2. The Submission Contract

Every submission is a **single CSV file** with a header row. The file must
satisfy the column contract below, plus the value rules in sections 3–6.

### 2.1 Required Columns

| Column | Type | Description |
|--------|------|-------------|
| `place_id` | integer | Canonical place ID from `analysis.places` |
| `indicator_code` | text | Unique code for the indicator (see §4) |
| `year` | integer | Reference year, ISO 8601 (e.g. `2023`) |
| `value` | numeric | The observed value |
| `support_level` | text | Geographic level at which data was collected |
| `source_identifier` | text | DataCite identifier or internal source key |

### 2.2 Optional Columns

| Column | Type | Description |
|--------|------|-------------|
| `collection_method` | text | See §5.1 |
| `aggregation_method` | text | See §5.2 |
| `denominator` | text | What the value is normalized by, if anything |
| `notes` | text | Free-text caveats for this row |

### 2.3 Example

```csv
place_id,indicator_code,year,value,support_level,source_identifier,collection_method,aggregation_method,denominator,notes
12,WB_NY.GDP.PCAP.CD,2023,52340.12,country,10.1596/wdi,administrative,none,per_capita,Current USD
12,WB_NY.GDP.PCAP.CD,2022,51002.87,country,10.1596/wdi,administrative,none,per_capita,Current USD
