# PostgreSQL version policy (SECURITY-2D-3E1B-1)

Canonical production PostgreSQL = **`postgres:16-alpine`** (PostgreSQL 16, Alpine).

The production database runs as the docker-compose `postgres` service, self-hosted on the
VDS (see `docs/LAUNCH_RUNBOOK.md`). The real-PostgreSQL security/concurrency test matrix runs
in the `postgres-explain` and `postgres-migration` CI jobs. All three **must** reference the
same major and variant.

| Contour | Image |
|---------|-------|
| production compose `postgres` service | `postgres:16-alpine` |
| CI `postgres-explain` service | `postgres:16-alpine` |
| CI `postgres-migration` service | `postgres:16-alpine` |

Enforced offline by `backend/tests/test_postgresql_version_parity_guard.py` (structural YAML
parse): all three exactly `postgres:16-alpine`; `latest`, PostgreSQL 15, and a bare
`postgres:16` (no `-alpine`) are rejected; an unexpected extra stateful PostgreSQL service
fails until classified.

## Rules

- **Prod compose and both real-PG CI jobs must match on major + variant.** CI proves the code
  on the exact PostgreSQL the production container runs.
- **A PostgreSQL major is NOT changed by a routine image bump.** Once real seller data exists,
  a major upgrade requires its own runbook: backup → restore test → maintenance window →
  upgrade → verification → documented rollback. Never fold a major upgrade into an unrelated PR.
- **`docker compose down -v` is forbidden in production** — it destroys the named volume
  `postgres_data` and every seller's imported data. Use `docker compose down` (volume preserved).
- **No auto-merge of a PostgreSQL image change** (bot may open a PR only).
- **A move to managed PostgreSQL** (e.g. a Selectel managed service) is a separate decision and
  requires re-running this parity check against the managed major/variant.

## Not yet done (tracked, out of scope here)

- **Digest pin** of the PostgreSQL image (`tag@sha256:`) — separate step **3E1B-2**. This PR
  aligns major/variant only; it does **not** pin a digest.
- **Automated backup / PITR / verified restore** — separate step **3E1B-3**. Backups are **not**
  implemented yet; today only the manual `pg_dump` in the launch runbook exists. Do not treat
  backup/PITR as done.
