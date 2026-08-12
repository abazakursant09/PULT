# PostgreSQL version policy (SECURITY-2D-3E1B-1 / -2)

Canonical production PostgreSQL = **`postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777`**.

The readable tag (`16-alpine`) is kept for humans; the **digest** freezes the exact bytes.
That digest is the **OCI image-index (manifest-list) digest** of `postgres:16-alpine`
(PostgreSQL **16.14**, Alpine, `linux/amd64` child) that the green master CI pulled and ran
after SECURITY-2D-3E1B-1. Verified **2026-08-12** via two authoritative sources (Docker
Registry v2 API + Docker Hub API — identical; by-digest HTTP 200). This is a pin of
already-CI-proven bytes, **not** a version bump.

The production database runs as the docker-compose `postgres` service (self-hosted on the VDS,
see `docs/LAUNCH_RUNBOOK.md`). The real-PostgreSQL security/concurrency matrix runs in the
`postgres-explain` and `postgres-migration` CI jobs. All three **must** reference the same
tag **and** the same digest.

| Contour | Image |
|---------|-------|
| production compose `postgres` service | `postgres:16-alpine@sha256:57c72fd2…ffc07777` |
| CI `postgres-explain` service | `postgres:16-alpine@sha256:57c72fd2…ffc07777` |
| CI `postgres-migration` service | `postgres:16-alpine@sha256:57c72fd2…ffc07777` |

Enforced offline by `backend/tests/test_postgresql_version_parity_guard.py` (structural YAML
parse): all three exactly the canonical tag+digest; tag-only, digest-only, `latest`,
PostgreSQL 15, a bare `postgres:16`, an uppercase/short/other digest, a comment-only digest,
a removed image, or an unexpected second stateful PostgreSQL service all fail.

## Rules

- **Prod compose and both real-PG CI jobs must match on tag AND pinned digest.** CI proves the
  code on the exact PostgreSQL bytes the production container runs.
- **A digest bump is a separate, reviewed maintenance PR.** The PR records the full old → new
  digest, and before the bump the digest must pass a backup/restore test and the full real-PG
  CI on the new digest. Never fold a digest bump into an unrelated change.
- **A PostgreSQL major/variant change is NOT a digest bump.** Once real seller data exists, a
  major upgrade needs its own runbook: backup → restore test → maintenance window → upgrade →
  verification → documented rollback.
- **`docker compose down -v` is forbidden in production** — it destroys the named volume
  `postgres_data` and every seller's imported data. Use `docker compose down` (volume preserved).
- **No auto-merge of a PostgreSQL image change** (bot may open a PR only).
- **A move to managed PostgreSQL** is a separate decision and requires re-running this parity
  check against the managed major/variant.
- **A digest pin is not a CVE audit** — it guarantees reproducibility and byte-integrity, not
  the absence of vulnerabilities.

## Not yet done (tracked, out of scope here)

- **Automated backup / PITR / verified restore** — separate step **3E1B-3**. Backups are **not**
  implemented yet; today only the manual `pg_dump` in the launch runbook exists. Deployment is
  **not** fully immutable and disaster recovery is **not** in place. Do not treat backup/PITR
  or DR as done.
