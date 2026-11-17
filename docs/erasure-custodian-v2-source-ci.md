# Dormant schema2 source CI — not deployment approval

The additive `ops/backup/custodian_v2/` sources preserve the independently
reviewed local schema2 reference. Nothing imports them from the application,
backup/restore entry, existing custodian service, or production compose.
There is no installer, new service unit, live transport, schema conversion,
working profile, database, credential, or automatic pending-operation replay.

`source-profile.json` is an immutable **DRAFT source catalogue**, not an
operator-approved execution profile. Its historical local proof reference is
provenance only; CI runs its own current tests rather than trusting that receipt.
The cold bootstrap separately requires an independently approved root-controlled
profile and exposes local observation/close only, never write/purge/restore.
Its system Python 3.12, root/stdlib/bootstrap and trusted host process remain
trust roots. This is not anti-root or malicious-same-process protection.

Six frozen legacy inputs are taken from their unchanged repository files. The
reviewed PUT-only S3 writer variant differs from the current v1 source, so its
exact bytes are kept under `custodian_v2/frozen/ops/backup/` and copied **only**
into disposable synthetic test snapshots. The v1 runtime is not replaced.
All exact inputs are checked by SHA-256, with LF enforced by `.gitattributes`.

## CI proof

The existing synthetic workflow downloads only seven hash-pinned wheels using
the existing dormant host lock. Wheels are rehashed before any payload import;
no local archives, developer `tmp/` paths, installed app packages, credentials,
or historical proof JSON are needed. Wheel unpacking is a bounded test fixture,
not a deployed dependency installer.

Two explicit native gates run with `/usr/bin/python3.12 -I -S -B` in separate
root-owned PID/network namespaces. Child workers use synthetic UID 47003,
GID 47004, empty supplementary groups and no capabilities. A UID conflict or
missing native prerequisite fails the job. No skipped test counts as proof.
The gates require exactly 32 durable-protocol and 21 cold-host tests, no errors,
failures, skips or expected failures, stable source hashes, no remaining owned
workers and removal of the native synthetic tree. Both reject optimized `-O`
execution with `NOT_RUN` and a nonzero exit; CI checks that exact refusal.

Durable proof includes real SQLite transactions, nine actual owned-process
SIGKILL checkpoints, uncertain commit/close handling and restart-without-replay.
Cold proof includes checked captured imports, no module fallback, failed optional
imports, owner-before-SQL/credential, descriptor identity, cleanup retention,
profile drift and old-profile refusal. All S3 objects and credentials are fake;
the transport is mandatory `httpx.MockTransport`, and the network namespace has
no external interface. Reports contain hashes/counts, not raw payloads or keys.

## Remaining release gates

Source CI success is not placement, actual IAM/create-only/version/TLS proof,
authenticated IPC, a fresh restore authority, a working supervisor/stop binding,
reboot/power-loss proof, capacity certification, or a public launch decision.
The 1024-receipt/2 MiB bounds are protective limits, not measured production
capacity. Old schema/data/profiles remain unchanged. Rollback is to leave this
entire additive source path unused; no database downgrade or reset is required.

Before real deployment, recheck the exact approved release SHA, fixed runtime,
principals, inode/credential ownership, action-time cloud quote and independent
placement within the additional 3,500 RUB/month ceiling. This PR creates no
paid resource and grants no IAM, network, sudo, D-Bus, service or SSH permission.
API, SMTP, purge/restore admission and public launch remain disabled.
