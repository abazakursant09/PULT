# Backup / restore policy (SECURITY-2D-3E1B-3A — foundation)

## What 3A is — and is NOT

**3A is the backup FOUNDATION, proven only on synthetic data in CI.** It is:

- a reproducible, least-privilege backup runner (pinned image + pinned rclone/age);
- `pg_dump --format=custom` → client-side **age** encryption → SHA-256 → S3 upload → scoped verify;
- a restore runner that fetches, verifies size+checksums, decrypts, and restores into a **new empty** database;
- a synthetic **backup → destroy source → restore into a new DB → verify** proof (GitHub Actions + MinIO);
- a fail-closed negative-test matrix and offline contract guards.

**3A is NOT** (do not describe it as any of these): an automatic daily backup; a real production backup; PITR / continuous WAL archive; a confirmed Selectel bucket; production monitoring; ready disaster recovery. **RPO/RTO are NOT yet delivered** — they require scheduling + monitoring (3C).

**3E1B-3B (PITR) and 3E1B-3C (operations: scheduling, retention enforcement, monitoring, runbook) remain mandatory before launch.**

## Architecture

Self-hosted Docker Compose on a Selectel VDS; PostgreSQL pinned to
`postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777`.
Backups go **offsite** to Selectel S3-compatible Object Storage, recommended in a **different
region than the VDS** (VDS Moscow ru-2c → bucket St-Petersburg), all within the Russian
Federation. Only the database is backed up (~5 GB); uploads are delete-after-import and are
not backed up.

Runner tools (build-time pinned, SHA-256 verified via BuildKit `ADD --checksum`, no unpinned
`apk`, no runtime download): rclone **v1.75.0** (S3), age **v1.3.1** (encryption). See
`ops/backup/Dockerfile`.

## Security

- Encryption: **age** X25519 recipient. Backup writer holds only the **public** recipient; the
  **private** identity is required only for restore and is never in Git/CI fixtures. No plaintext
  upload path exists; encryption failure aborts before any upload.
- TLS verification is mandatory (rclone secure default; no insecure flag in any script).
- Role separation:
  - **Writer**: PutObject (+AbortMultipartUpload) + HeadObject on the bucket/prefix only —
    append-oriented with scoped metadata verification. No content GetObject, no DeleteObject,
    no ListAllMyBuckets, no lifecycle/Object-Lock admin, no other prefix.
  - **Reader (restore)**: GetObject + HeadObject on the prefix; prefix-scoped ListBucket only if
    restore-point selection needs it. No Put/Delete. Distinct principal from the writer.
  - Retention/Object-Lock administration is a **separate** principal.
- The production app never holds backup credentials; the backup runner never holds
  marketplace/provider/JWT/Sentry secrets or the restore private identity.
- Object Lock (immutability, ransomware defence) is **recommended and contract-guarded here**,
  but is **not claimed enabled**: the real bucket must be created with Object Lock beforehand;
  confirming Selectel support/enablement is a **3C** infrastructure gate.
- Logs contain only timestamp/status/size/duration/checksum/object-key. Manifests and filenames
  contain **no** PII (no email/store/user IDs), no credentials, no SQL, no row data, no hostnames.
- Dumps/ciphertext/private keys are never committed and never uploaded as CI artifacts
  (`.gitignore` / `.dockerignore` patterns + guard).

## 152-FZ (personal data)

A database backup is **another copy of personal data** (user emails, hashed passwords,
marketplace-credential ciphertext). Therefore: store in the Russian Federation (Selectel RU
region); apply retention and deletion; restrict access (role separation above); log access;
have an incident procedure. **No legal compliance guarantee is claimed** — retention period,
deletion SLA, processing agreement, and incident handling are organizational decisions for the
owner/legal, not settled by this code.

## What `pg_dump` does NOT capture

`pg_dump` backs up one database's schema+data, **not** cluster globals (roles, tablespaces).
The `pult_backup` read-only role and any other required roles are **infrastructure-as-code**:
the restore runbook/CI recreates required roles from a documented manifest **before**
`pg_restore` — production password hashes are never carried in the dump. A database dump is not
a full cluster backup.

## Backup DB role (`pult_backup`)

Read-only: LOGIN/CONNECT + `pg_read_all_data` (PG16 built-in) + read on sequences/schema. No
write, no CREATEDB/CREATEROLE/REPLICATION/BYPASSRLS, no object ownership. The project uses no
row-level security on the backed-up tables (evidence: no `ENABLE ROW LEVEL SECURITY` /
`CREATE POLICY` in migrations), so `pg_read_all_data` sees every needed row. The role is created
by infrastructure, never by application migrations; synthetic CI creates it temporarily.

## Correctness (a bare upload is NOT success)

Backup success requires: pg_dump exit 0 + non-empty dump + `pg_restore --list` parses +
plaintext SHA-256 + encryption exit 0 + non-empty ciphertext + ciphertext SHA-256 + upload exit
0 + object exists with remote size == ciphertext size (HeadObject-equivalent). A manifest
records format/created_at/run-uuid/server-version/system-identifier(if permitted)/alembic-head/
both SHA-256s/sizes/object-key/tool versions.

## Verified restore

Restore fetches, checks remote size + ciphertext SHA-256 against the manifest, decrypts with the
private identity, checks plaintext SHA-256, `pg_restore --list`, asserts the target is **empty**
(never `--clean`), restores, then runs integrity checks (Alembic head `rob1a2b3c4d01`, full
schema-set checksum, presence of critical tables, `users` count + row-checksum, UNIQUE enforced,
fresh insert works). Proven end-to-end on synthetic data in
`.github/workflows/backup_restore_synthetic.yml` against a pinned MinIO
(`minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`).

## Retention (policy only until 3C)

Default: daily 14 days, weekly 8 weeks, monthly by separate legal/business decision. **Not
enforced by any tool yet** — enforcement + monitoring + scheduling land in 3C. A storage
lifecycle policy is not a substitute for a proven retention tool.

## Roadmap

- **3E1B-3B** — PITR: continuous WAL archive + point-in-time synthetic restore (T1/T2/T3). Likely
  needs a thin custom PostgreSQL image (pinned base + pgBackRest/WAL-G) and `archive_mode`.
- **3E1B-3C** — operations: host systemd timer (or a dedicated one-shot Compose service),
  retention enforcement, monitoring/alerts (last-success age, WAL delay, dead-man switch — infra,
  never an app feature flag), production restore runbook, launch checklist, real Selectel bucket
  with Object Lock. Both 3B and 3C are launch blockers.
