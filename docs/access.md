# Correlia Access Model

Correlia separates **reading** from **writing**. Anyone may read; only
approved uploaders may write. This document describes the roles, how they
are provisioned, and how row-level security enforces the write boundary.

---

## Roles

| Role | Read | Write | Notes |
|------|------|-------|-------|
| `correlia_viewer` | All validated data | None | Public read access |
| `correlia_uploader` | All validated data | Own sources only | Provisioned individually |
| `correlia_admin` | Everything | Everything | Bypasses RLS |

All three are `NOLOGIN` group roles. Individual logins are created and
granted membership in the appropriate group.

### Provisioning a Viewer

```sql
CREATE ROLE alice LOGIN PASSWORD '...';
GRANT correlia_viewer TO alice;
