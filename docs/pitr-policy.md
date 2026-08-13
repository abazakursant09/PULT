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

## archive_command semantics / S3 outage — THREE distinct states
The standard config is **`archive-async=y`**. Three states must never be conflated:

1. **sync success** (`archive-async=n`): the foreground `archive-push` returns **exit 0 only after** the WAL segment is durably uploaded+checksummed to the **offsite** S3 repository; nonzero → PostgreSQL retains the WAL in `pg_wal` and retries.
2. **async foreground success** (`archive-async=y`, the standard): the foreground `archive-push` **returns 0 as soon as the segment is accepted into the LOCAL spool** (`/var/spool/pgbackrest`, same VDS). A background async worker uploads it later. **A foreground exit 0 under async therefore means LOCAL acceptance only — NOT that the WAL is offsite.**
3. **offsite durable**: the segment is actually present in the S3 repository (visible in `pgbackrest info` archive max / repository listing).

**Never `... || true`.** During an S3 outage under async: foreground keeps returning 0 and PostgreSQL's `pg_stat_archiver.last_archived_wal` advances, while the segment sits in the local spool (and/or `pg_wal`) on the same VDS; the longer the outage, the larger the same-VDS backlog. `ops/pitr/status.sh` reports **LOCAL** signals (`pitr_local_spool_bytes`, `pitr_pg_wal_bytes`) **separately** from **OFFSITE** signals (`pitr_offsite_archive_min/max`, `pitr_check_status`); **`pitr_continuity=intact` is emitted only on CONFIRMED remote continuity (remote `pgbackrest check` ok AND a non-empty offsite WAL max) — a local spool or a foreground exit 0 NEVER by themselves yield `intact`.** All output is allowlisted (no PII/creds/endpoint/SQL/object-listing).

**Residual risk (honest):** with `archive-async=y`, **loss of the whole VDS while WAL is spooled-but-not-yet-offsite destroys that WAL** (the spool lives on the same VDS). Therefore **RPO=0 during an S3 outage is NOT promised** until an independent durable spool / HA path exists (3C+). **Alert/freeze thresholds are NOT hardcoded** — 3C measures the real VDS volume + WAL rate and sets an alert threshold, a freeze threshold, and an absolute reserved free-space floor. No automatic WAL/spool deletion to free space.

## Encryption & IAM honesty
Repository cipher = pgBackRest AES-256-**CBC**. This gives confidentiality + repository checksum/`verify` integrity detection; it is **not** self-authenticating AEAD and does **not** prove trusted origin — a compromised writer substituting a whole valid repository is an out-of-scope residual (Object Lock / immutable storage = 3C mitigation). S3 role split (contract; NOT least-privilege-proven here): writer = Put/Head/multipart + prefix-scoped List (pgBackRest requires ListBucket on its prefix), no content Get/Delete/lifecycle; reader = Get/Head/prefix List, no Put/Delete; retention/Object-Lock admin = separate principal. Synthetic CI uses MinIO admin creds — **not** a least-privilege proof. **Selectel IAM canary is a mandatory 3C blocker.** TLS verification is mandatory (`repo1-storage-verify-tls=y`); the CI serves MinIO over TLS with a mounted CA so verification is never disabled.

## Coexistence with 3A
3A `pg_dump` = logical, portable recovery (different bucket/prefix/credentials/retention). 3B pgBackRest = physical base backup + WAL PITR. **Neither replaces the other**; both are required before launch. 3A is unchanged by 3B1.

## Failure coverage
B1 (this PR, runtime-integration, PROVEN fail-closed in `pitr_synthetic.yml`, `pass=N wrong=0`):
A missing base backup / wrong stanza; B wrong repository cipher key; C non-empty restore target
refused; D missing synthetic marker refused; E S3 unavailable during archive — **sync mode**
(network-isolate MinIO → `pg_switch_wal()` → `pg_stat_archiver.failed_count` increases + the WAL
segment is retained in `pg_wal` → restore MinIO → drain proven via `pgbackrest check`); F missing
required WAL (segment removed from a scratch repo copy → recovery cannot reach target); G corrupt
required WAL object (mutated → recovery fails); H wrong system identifier (a foreign cluster's
`pgbackrest check` against the repo fails on system-id mismatch); I target before available base
backup (refused); J WAL continuity gap (mid segment removed → recovery does not silently reach the
requested target); K fail-closed refusal of a **non-empty** target PGDATA (any pre-existing
`PG_VERSION`, incl. a foreign-major stamp, is refused — this is the non-empty guard, not a dedicated
major-version comparison); L S3 unavailable during archive — **async mode** (the standard
`archive-async=y`: isolate the source, prove the foreground `archive-push` succeeds as LOCAL spool
acceptance — `last_archived_wal` advances, `failed_count` flat — while the exact segment is proven
absent offsite and `status.sh` reports `continuity!=intact`; then reconnect, prove that exact
segment drains to S3, and `status.sh` flips to `check=ok`/`continuity=intact`). Each uses a
disposable target/prefix and asserts fail-closed with no promoted target and no false durability.
**B2 COVERED synthetically (`.github/workflows/pitr_extended.yml`, workflow_dispatch + nightly; NOT
production):** a **probe** proving the not-yet-offsite backlog physically lives in `pg_wal` (the
spool holds only transient status); **long async S3 outage** with N=8 segments — foreground
local-accept (`failed_count` flat), each exact segment retained in `pg_wal`, none offsite, status
unsafe, then measured **sequential drain** of every exact segment after S3 returns (drain-rate is a
SYNTHETIC measurement, NOT an SLO); **concurrent writers + multi-LSN restore** (≥3 writers, three
ordered checkpoints LSN1<LSN2<LSN3, two independent restores to LSN2 and LSN3 with row assertions;
authoritative order = numeric LSN + marker rows, NOT commit/filename/S3-appearance order);
**restart/recreate/retry** (PG restart empty-backlog; PG restart WITH backlog preserving exact segs;
source-container recreation on preserved volume; restore-retry into a non-empty target fail-closed);
**corruption on a scratch repo copy** (truncation / zero-length / missing-middle WAL — all
fail-closed, canonical repo untouched).

**B2 attempted but DEFERRED to 3C (NOT faked here):** local **spool/disk-full** (bounding the real
backlog filesystem needs a privileged mount / risks the shared runner disk — a small spool-only
tmpfs would not create real WAL-backlog pressure since the backlog is in `pg_wal`); **`kill -9` of
the async archive worker** (PID/timing not deterministically catchable without a race);
**multipart-upload interruption**; **timeline switch**; extended multi-timeline cases.

**3C production-only (unchanged):** real Selectel bucket, least-privilege **IAM** permutations
(PUT/LIST/GET split), **Object Lock**/immutability, scheduling, retention enforcement,
monitoring/dead-man alerts, production `archive_mode` activation, RPO/RTO. **MinIO ≠ Selectel** —
B2 behavior on MinIO does not prove Selectel; test throughput/drain-rate are not production SLOs;
production PITR is still NOT activated.

## Restore (fail-closed) & production
`ops/pitr/restore.sh` (B1 synthetic, marker-gated) restores into a NEW empty PGDATA to a target LSN. Before restoring it runs `pgbackrest info` and requires that a `full backup` exists for the stanza (repository reachable + base backup present) — it does **not** perform a separate explicit system-id check (system-id mismatch is exercised at the workflow level, case H, via `pgbackrest check` on a foreign cluster). It never uses `--clean`/restores over an existing DB (refuses any target with a `PG_VERSION`), never deletes source/repo, and performs no production cutover. A major upgrade is NOT a restore. Production activation (image swap + archive_mode restart + initial base backup + first-WAL wait + repo check) is 3C.

## Roadmap
**B2** reliability + **3C** operations (Selectel bucket/IAM/Object-Lock canary, scheduling, retention, monitoring/dead-man, production activation, restore runbook, launch checklist, restore drill) are mandatory launch blockers.
