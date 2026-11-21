# Offline synthetic operator host

Status: DRAFT source candidate for offline synthetic verification. A passing
native gate is not a deployment or a live-data test. Nothing here activates an
endpoint, approves an operational profile, or authorizes publication.

This additive host exercises exactly one fixed synthetic DRAFT review over a
local AF_UNIX socket. No real database, S3/cloud access, keys, credentials,
journal append, identity enrollment, purge, or restore is provided. The seven
existing dependency pins remain unchanged; only the four operator-host sources
are new. Root, the kernel, PID1/systemd with cgroup v2, the trusted OS tools and
fixed bootstrap/system Python, and an independently supplied manifest SHA-256
are trust roots. This is not protection against hostile root or a compromised
trusted OS.

## Source catalogue is not approval

`ops/backup/operator_host/source-profile.json` is a DRAFT catalogue with a
different format from an operational manifest. It contains no approved digest,
runtime identities, working credentials, or admitted operations. Do not pass it
to `load_profile()` or use its hash as operational approval.

The four candidate hashes describe UTF-8 source bytes with CRLF converted to LF
and no other changes; the seven dependency hashes are the fixed `profile.PINS`
values. Runtime hashes instead cover exact installed bytes: the bootstrap does
not normalize line endings. Any source change requires a catalogue refresh and
fresh verification; a catalogue refresh alone grants no authority.

## Runtime and operational manifest contract

The fixed runtime is Linux CPython 3.12 at `/usr/bin/python3.12`, with
`-I -S -B`, optimization disabled, on the original main thread with one thread.
There is no repository import, site loading, or source-path fallback.

A separately approved operational manifest must be canonical ASCII JSON:
sorted keys, compact separators, ASCII escapes, no NaN or duplicate keys, and
exactly one trailing LF. It is bounded to 16 KiB and has exactly these fields:

| Field | Required content |
| --- | --- |
| `format` | `pult-offline-operator-host-v1` |
| `mode` | `SYNTHETIC_REVIEW_ENDPOINT_ONLY` |
| `operation` | Canonical UUIDv4 for this one attempt |
| `release` | `root`, `identity` |
| `controller` | `unit`, `invocation_id`, `pid`, `start_ticks`, `boot` |
| `control` | `root`, `identity`, `lock_identity` |
| `actor` | Dedicated unprivileged `uid`, `gid` |
| `operator_uid` | Separate unprivileged operator UID |
| `ipc` | `path`, `identity` |
| `synthetic` | Exactly the five fixed values in `profile.SYNTHETIC` |
| `sources` | Exactly the eleven `profile.PATHS` roles, each with `path`, `sha256` |

Paths are normalized absolute Linux paths; source paths are their fixed
release-relative paths under `release.root`. An identity is exactly `device`
(nonnegative integer) and `inode` (positive integer). Release and control are
separate root-owned `0755` directories; IPC is a separate actor-owned `0750`
directory. The pre-existing empty control lock is root-owned `0600`. Actor UID,
actor GID and operator UID are integers from 1000 through 2^31-1; actor and
operator UIDs differ. Controller identity binds the exact unit invocation,
original main PID/start tick and boot.

The trusted loader accepts `load_profile(file, approved_sha)` for the root
controller; the worker CLI has only `bootstrap.py <manifest> <sha>`. Approval
must arrive independently, not from a field inside the manifest or catalogue.
Before executing even `profile.py`, the loader captures the protected manifest
and all eleven source bodies, checks every digest and the seven frozen pins,
and rechecks descriptor/path stability. Each source is at most 100000 bytes,
root-owned, regular, single-link, and mode `0444` or `0644`, with protected
non-symlink ancestors and no POSIX ACLs. The instantiated profile must retain
the same manifest bytes. CLI actor validation precedes worker execution; the
root API does not permit a root worker.

## One-shot lifecycle

The trusted root controller uses `profile.capture('host').Bound(profile).run()`
in its original live controller context. Its startup requires the negative
permanent fence condition
`ConditionPathExists=!<new-control-root>/constructor-stop.claim`. The claim
name is reused only by a frozen stateless checker: this host requires a NEW
control root/domain and IPC directory, not adoption of an older constructor
scope. The root controller exclusively locks and durably creates the permanent
claim before requesting its one owned worker unit. Never delete the fence to
retry, rearm, restart, or adopt an attempt.

PID1 starts the fixed `/usr/bin/setpriv` launcher BEFORE actor Python. It sets
all four real/effective/saved/filesystem UID/GID values to the actor, clears
supplementary groups, sets no-new-privileges, and clears all five capability
sets, including the bounding set. `User=0` unit metadata belongs to the OS
launcher; it is not evidence that worker Python runs as root. Kernel process
credentials and unit properties are checked independently. The root parent
attests PID1 metadata, active/running state, MainPID, InvocationID and the actual
worker cgroup before GO. The deliberately NSS-absent actor has no system-bus
access and receives none: it checks its own credentials, protected claim, boot
and the original root controller's PID/start ticks, all four UID/GID values,
cgroup and live/non-stopped process state via procfs.

The worker unit has a 15-second runtime limit, a 2-second stop timeout,
`KillMode=control-group`, SIGTERM followed by SIGKILL, and no restart. It is
bound to, ordered after, and stopped with its original controller, with private
networking and only the IPC directory writable through its filesystem policy.

READY follows actual bind, chmod `0660`, and listen on `ipc/review.sock`.
After kernel readiness checks, the parent sends exactly
`GO_OFFLINE_REVIEW_V1\n` and keeps stdin open. The worker waits at most three
seconds for GO and refuses EOF, malformed input, or queued GO on a closed
parent pipe. It accepts one real connection, enforces the fixed synthetic
account/draft pair and kernel actor checks, and calls the captured old core's
session once. Before constructing its observation, the callback exclusively
creates `ipc/synthetic.reviewed`, mode `0600`, writes exactly
`OFFLINE_SYNTHETIC_REVIEW\n`, fsyncs and closes it. That marker is fault-test
evidence, not authority. Neither marker nor socket is automatically removed.

## Observation versus parent receipt

The old core's observation can reach the operator before worker closure or
parent cleanup. It is NOT the parent's supervision receipt, a live fence, or a
reusable authorization. Worker READY/CLOSED lines are canonical and bounded to
2048 bytes; CLOSED hashes the profile-canonical serialized observation,
including its trailing LF, only after both sockets close and actor checks pass.

The parent's `pult-offline-operator-supervision-v1` receipt selects
`OFFLINE_REVIEW_STOP_CONFIRMED_NO_ADMISSION` only after valid worker frames,
successful pre-cleanup exit status, owned-unit cleanup, observed EOF on BOTH
output streams, child reaping, descriptor closure, an empty cgroup, and a final
controller/profile check. Uncertainty produces `UNKNOWN_REQUIRES_REVIEW` with
no observation digest, or no receipt if the controller dies; neither means
success. UNKNOWN never authorizes automatic retry, adoption, or fence removal.

Every worker frame and parent receipt keeps `identity_enrollment_admitted`,
`journal_dispatch_admitted`, and `purge_or_restore_admitted` false. Even a
confirmed offline stop grants no live operational authority.

## Required offline native gate

Run from the reviewed candidate release root:

```sh
sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B ops/backup/tests/operator_offline_host_proof.py
```

The gate requires all eleven cases and zero skips: authorized review, SIGSTOP,
wrong UID, changed source, same-byte profile inode replacement, stale controller,
TERM-ignoring descendant, controller death, queued GO then controller death,
cleanup uncertainty, and wrong independent approval. Repeated launch is refused
by the retained claim. SIGSTOP proves timeout termination (SIGTERM after SIGCONT
or SIGKILL); the TERM-ignoring descendant separately proves SIGKILL escalation.
Queued GO proves queuing, death cleanup and restart refusal, not an observed
handshake refusal. Parent receipts remain non-admitting even when all cases pass.

Each fixture stops and verifies only its recorded units, confirms both stopped
state and empty cgroups, and reaps owned children before deleting its temporary
directory. Cleanup uncertainty retains that fixture for review. No accounts,
service files or timers are installed. Syntax checks and launcher-only probes
are not this native proof. PostgreSQL, working journal integration and deployment
require separate gates; their readiness is not implied here.
