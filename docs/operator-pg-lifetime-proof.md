# Synthetic PostgreSQL and operator lifetime proof

This test-only sidecar composes the accepted read-only committed-DRAFT reviewer,
the kernel-authenticated operator session and the accepted process-stop mechanism.
It does not create a production PostgreSQL mode, an approved working profile,
live credentials, a journal writer, a purge operation or a public-launch permit.

The existing eight-case PostgreSQL and eleven-case offline gates remain unchanged.
This six-case gate supplements them; it does not replace either gate or prove
an atomic erasure lease, clone resistance or freshness after a snapshot closes.

## Boundary and inputs

A trusted test `command()` hook substitutes only the worker payload while retaining
the fixed setpriv launcher, actual UID/capability attestation, original controller
binding, permanent claim, runtime/stop limits, EOF/reap and cleanup checks. This
is test composition, not approval of the substitute payload by the old profile.
The legacy receipt's approval hash must never be interpreted as authorization for
PostgreSQL dependencies, a live DSN or a production handler. Its mutation flags
remain false, and the report explicitly says source admission was not proved.

The sidecar captures fourteen accepted LF source inputs and four SHA-256-pinned
Linux CPython3.12 wheels. It builds a fresh root-protected native Linux fixture;
all dependency files and both backend modules are verified before imports.
There is no site or repository fallback. The fixture, config and source digests
are supplied by the trusted test parent, never selected by a socket request.

Only a fresh, uniquely labelled PostgreSQL16 container is used, from the exact
pinned image and with `--pull=never`, no network, no published ports, read-only
root filesystem, no capabilities and no-new-privileges. Its data/socket binds
are native Linux temporary directories. The Docker client uses the fixed local
Unix daemon socket, not an inherited remote context. No host NSS users are made.
All roles, passwords and application rows are artificial fixture-only values.

The protected fixture supplies a Unix-socket-only asyncpg/NullPool engine and a
dedicated SELECT-only role. The unchanged reviewer enforces its read-only
REPEATABLE READ transaction, identity/privilege checks, SQL deadlines and native
close audit. An observation follows actual native close, but may reach the client
before process cleanup and the separate parent stop receipt. It is not a live
erasure lease. Engine disposal remains the fixture's duty.

## Required cases

| Case | Required evidence |
| --- | --- |
| Authorized | Real peer authentication, committed eligible DRAFT, one native connection closed before observation; non-admitting parent receipt only after process cleanup. |
| Close then raise | Original native close occurs, injected failure poisons the dedicated-pool latch even with diagnostics disabled; repeat review refuses without another checkout. |
| Blocked query and resistant close | Actual owned table lock produces SQLSTATE55P03; close resists cancellation and the worker ignores TERM; actual PID1 SIGKILL is required, with no observation or parent success. |
| Controller death | Independent observer confirms the actual held reader before killing only the pinned controller; no parent receipt, no remaining worker or reader backend. |
| Wrong peer | Shared-GID stranger is refused before reviewer callback/checkout. |
| Restored DRAFT | Committed restore timestamp makes the DRAFT ineligible; no observation, normal physical connection closure. |

Faults patch only owned test-process handles. The resistant-close case also
suppresses that owned handle's terminate fallback deliberately; this is a fault
model, not a change to the production reviewer. The test hook never kills reader
backends to manufacture cleanup. Only the independently identified owner locker
may be terminated to release its synthetic table lock.

For every case an independent fixture-owner observer checks all matching reader
backends/transactions and the post-setup application-row hash before PostgreSQL
teardown. A restored case's baseline is taken after its intentional setup commit.
Server teardown cannot conceal a leak. Cleanup verifies exact container IDs,
labels, image and recorded mounts, stopped/empty owned units and reaped children;
uncertainty retains the scope and cannot become PASS.

## Running the gate

Download the four locked CPython3.12 Linux wheels without installing them globally,
as shown in the mandatory workflow step. Then, on a reviewed Linux root test host
with real PID1 systemd, cgroup v2, the pinned cached image and a local Docker daemon:

```sh
sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B \
  ops/backup/tests/operator_pg_lifetime/gate.py --wheels-dir /absolute/wheel/cache
```

The gate accepts exactly six ordered results, zero skips, unchanged source inputs,
closed owned resources and false mutation/source-admission flags. Optimized Python
is refused before resources are created. A partial run, source mutation, leaked
connection, changed row or uncertain teardown fails the gate. Evidence is printed
only after cleanup; it contains no passwords, SQL or application-row dumps.
