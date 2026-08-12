# PITR policy (SECURITY-2D-3E1B-3B1 — synthetic foundation)

## What 3B1 is — and is NOT

3B1 is the **PITR (point-in-time recovery) FOUNDATION, proven only on synthetic data in CI**:
pgBackRest 2.59.0 built from hash-verified source into a runner image, physical base backup +
continuous WAL archive to S3-compatible storage, and a synthetic **base + WAL + LSN restore**
proof (T1/T2 present, T3 absent) after destroying the source.

3B1 is **NOT**: production `archive_mode` (the production compose is unchanged and archive_mode
stays OFF there); a confirmed Selectel bucket; least-privilege IAM proof; scheduling / retention
enforcement / alerts; a delivered RPO/RTO; ready disaster recovery. **RPO/RTO are not provided.**

## Tool & supply chain
pgBackRest **2.59.0** (musl-native; WAL-G rejected — glibc-only assets on the pinned Alpine base).
- Source integrity: `pgbackrest-2.59.0.tar.gz` sha256 `faaf8faa…` (upstream `.sha256sum`), added via BuildKit `ADD --checksum`.
- Alpine dependency closure discovered on the pinned base (Alpine 3.24.1) and **exact-pinned** `name=version-rN` for every apk build-dep and runtime lib; final runtime closure (`scanelf`): libbz2/libssl3/libcrypto3/lz4-libs/libpq(base)/libxml2/zlib/zstd-libs + musl.
- **Residual (honest):** apk packages are protected by the **signed Alpine repository** + exact-version pins — this is **weaker than a full per-APK hash-lock** and there is **no byte-reproducible-build claim**. A package aging out of the Alpine repo breaks the build fail-closed (no auto-bump). 3B1 guarantees source integrity + exact dependency versions + clean runtime closure + a reproducible successful build + tested behavior. A digest-published custom PITR image in 3C reduces this residual.

## PostgreSQL settings (dormant; `ops/pitr/postgresql.conf`)
`wal_level=replica`, `archive_mode=on`, `archive_command='pgbackrest --stanza=pult archive-push %p'`, `archive_timeout=60s`, `full_page_writes=on`. `archive-async` is a pgBackRest option (in `pgbackrest.conf`), NOT a PostgreSQL GUC. Credentials (S3 key/secret, cipher pass) reach `archive-push` via the postgres process **environment** (`PGBACKREST_*` from secret files), never in argv or in Git config. Enabling `archive_mode` needs a **restart** — production activation is 3C.

## archive_command semantics / S3 outage
`archive-push` exits 0 only after the WAL segment is durably uploaded+checksummed; nonzero → PostgreSQL **retains** the WAL in `pg_wal` and retries. **Never `... || true`.** If S3 is unavailable, WAL accumulates on the volume (disk-pressure risk): the longer the outage, the larger the backlog. `ops/pitr/status.sh` exports allowlisted signals (check status, last base backup, pg_wal/spool bytes, continuity) with no PII/creds. **Alert/freeze thresholds are NOT hardcoded** — 3C measures the real VDS volume + WAL rate and sets an alert threshold, a freeze threshold, and an absolute reserved free-space floor. No automatic WAL/spool deletion to free space.

## Encryption & IAM honesty
Repository cipher = pgBackRest AES-256-**CBC**. This gives confidentiality + repository checksum/`verify` integrity detection; it is **not** self-authenticating AEAD and does **not** prove trusted origin — a compromised writer substituting a whole valid repository is an out-of-scope residual (Object Lock / immutable storage = 3C mitigation). S3 role split (contract; NOT least-privilege-proven here): writer = Put/Head/multipart + prefix-scoped List (pgBackRest requires ListBucket on its prefix), no content Get/Delete/lifecycle; reader = Get/Head/prefix List, no Put/Delete; retention/Object-Lock admin = separate principal. Synthetic CI uses MinIO admin creds — **not** a least-privilege proof. **Selectel IAM canary is a mandatory 3C blocker.** TLS verification is mandatory (`repo1-storage-verify-tls=y`); the CI serves MinIO over TLS with a mounted CA so verification is never disabled.

## Coexistence with 3A
3A `pg_dump` = logical, portable recovery (different bucket/prefix/credentials/retention). 3B pgBackRest = physical base backup + WAL PITR. **Neither replaces the other**; both are required before launch. 3A is unchanged by 3B1.

## Failure coverage
B1 (this PR, runtime-integration): missing base backup; wrong cipher key; missing/corrupt WAL or repo object; wrong stanza/system-id; target before base; target past a continuity gap; S3 unavailable during archive (archive-push nonzero, WAL retained); non-empty target; major mismatch; corrupt repo metadata. **B2 (still open, NOT proven):** long timeout/retry-exhaustion, spool/disk-full behavior, concurrent same-segment, extended timeline cases, partial multipart upload, quantitative backlog-model validation.

## Restore (fail-closed) & production
`ops/pitr/restore.sh` (B1 synthetic, marker-gated) restores into a NEW empty PGDATA to a target LSN with repository/stanza/system-id check first, never `--clean`/over an existing DB, never deletes source/repo, no production cutover. A major upgrade is NOT a restore. Production activation (image swap + archive_mode restart + initial base backup + first-WAL wait + repo check) is 3C.

## Roadmap
**B2** reliability + **3C** operations (Selectel bucket/IAM/Object-Lock canary, scheduling, retention, monitoring/dead-man, production activation, restore runbook, launch checklist, restore drill) are mandatory launch blockers.
