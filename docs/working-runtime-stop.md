# Independently stopped local runtime construction

This additive source stage joins the retained local-role runtime to an
independent PID1 lifecycle. It does **not** admit journal operations or activate
a working service. The old synthetic profiles, constructors, catalogues, source
pins and cleanup libraries remain unchanged. The inner runtime release is still
closed to exactly sixteen executable source files and seven pinned wheels.

The new root adapter and actor worker live outside that release. An independently
approved canonical outer profile binds their bytes, the fixed runtime entry,
the unchanged controller guard/supervisor/cleanup core, the inner profile SHA,
role and numeric actor, the original controller boot/PID/start/InvocationID and
a separate inode-pinned control directory with an existing empty private lock.
The root process never reads the actor's credential or opens its SQLite state.
Full inner resource admission still occurs only in the verified actor entry.

## Ownership and protocol

The controller must already run in a trusted root `Type=exec`, `ExitType=main`
unit with no restart, whole-cgroup termination and a non-trigger negative
condition on its permanent claim. Only stateless `_controller`/`manager_view`
checks are reused from the old stop module; its preparation, constructor profile
and run/claim paths are not working launchers.

Before submission the adapter locks the inode-bound control domain, exclusively
creates the immutable claim, writes all bytes and fsyncs both file and parent.
The basename `constructor-stop.claim` is retained only to reuse that fixed
stateless condition verifier. This is a new claim format in a separate control
domain, not an old synthetic fence or a migration. A success never unlinks it.
Unknown or prior state is not adopted, reset, retried or rearmed.

The fixed worker unit uses a fresh `pult-call-gate-<UUID>.service` identity. It
is bound and ordered after the original controller with `BindsTo`, `After` and
`PartOf`; PID1 enforces runtime 40 seconds and stop timeout 2 seconds, cgroup-wide
TERM then KILL, `ExitType=cgroup` and no restart. These bounds start before Python
intake, independently of the runtime's cooperative deadline.

The previously proved fixed `setpriv` chain drops numeric UID/GID, supplementary
groups and all five capability sets before actor Python. An explicit `env -i`
sets only `PATH=/usr/bin:/bin` and `LANG=C.UTF-8`, excluding systemd's additional
environment as well as OpenSSL/proxy/Python inputs. Source and credentials are
read-only to the actor. Only the custodian's private state directory is writable;
the writer has no writable state directory. Networking remains private and the
address-family policy permits only Unix sockets; no remote request is attempted.

The bounded three-frame protocol is:

1. Worker `READY`, before any runtime construction; root attests the actual
   PID/start/cgroup/argv, four kernel UID/GID values, empty groups, five zero
   capability sets, NNP and the complete required manager lifecycle.
2. Root `GO`; the actor rechecks its original controller and live parent pipe,
   then invokes unchanged `load_runtime()`. Its `HELD` binds the actual local
   snapshot, while the source/Lease/client graph remains owned.
3. Root reattests before `CLOSE`. Only successful owner closure emits `CLOSED`.
   READY/HELD must not have buffered future milestones before their root token.

The root keeps its input pipe open. A queued token accompanied by parent EOF
cannot arm a late worker. Actor checks use boot/PID/start and original controller
cgroup; root additionally verifies PID1 InvocationID. Trusted root, OS, stdlib,
PID1 and independent profile approval remain trust roots, not hostile inputs.

## Success, uncertainty and cleanup

A CLOSED frame is provisional. Success is selected only after the saved terminal
manager result is successful, the owned unit is stopped and its cgroup is empty,
both pipe EOFs are confirmed, the launcher is reaped and each owned descriptor
close is confirmed. Cleanup verifies the claim and Description/InvocationID
before reusing the pinned manager cleanup core. It is not an arbitrary-unit API.

Selector closure, owned-launcher termination, reap and each pipe close are
independent attempts. A failed close is not automatically repeated. Original
exceptions and the object graph are retained on uncertainty; no private error
message or traceback is printed. The fixed failure result is
`UNKNOWN_REQUIRES_REVIEW`, never an operation permit. A forced stop proves process
termination, **not** graceful client/source/Lease cleanup within Python.

No hook or command selector is accepted from a profile or connected caller.
Trusted native proof subclasses may record owned CI units or inject faults;
these test harnesses are not runtime dependencies or a production configuration.

## Proof and rollout boundary

Portable tests exercise the actual pump and cleanup code with explicitly fake
pipes/kernel/manager objects, including early milestones and independent cleanup
failures. They are not kernel, Lease or systemd proof. The separate native gate
uses actual artificial state/credentials, actor construction, flock, private
test IPC and PID1 transient units on a disposable Ubuntu CI runner. It retains
the host PID namespace to exercise systemd; `unshare --net` alone is not host
isolation. Its outer owned-scope ledger records units before requesting startup,
quiesces producers before cleanup and preserves evidence on unknown ownership.

The native fault matrix must prove both roles, actual frozen actors/controllers,
TERM-ignoring descendants, controller death and queued GO, permanent-fence
restart rejection, an untouched fixture-owned foreign sentinel and refusals of
source/profile/close/EOF/reap/manager/cgroup uncertainty. Exact input hashes,
zero skips and proven owned cleanup are required for the aggregate result.
Before those executions and all fresh CI pass, source preparation is not PASS.

The EOF/reap/cgroup refusal cases deliberately inject a false parent postcondition
after successful native worker closure; they are not claims of a physically
broken EOF, wait syscall or kernel cgroup. Their intended injection must be
reached and worker RETURN/restoration independently confirmed. Actual inherited
pipe/cgroup retention is exercised separately by the TERM-ignoring descendant.
Deadline-stop cases must prove timeout and empty cgroup before any controller or
harness cleanup intervention; later cleanup cannot repair a failed test oracle.

The workflow preserves all predecessor steps, exact pinned libraries and its
frozen tail, adding mandatory contracts/native steps and narrow path filters.
No tests are suppressed. The new privileged proof is not run against the user's
existing WSL/application host or the working cloud server.

The predecessor eleven-case offline-operator proof runs through a fixed CI-only
startup adapter. Its accepted original source and the three older PG proof pins
remain unchanged. The adapter copies that verified library into each owned test
fixture before startup and delays the existing READY observation until descendant
metadata has been written. This prevents a client starting protected intake while
the harness itself still changes an owned ancestor directory. All original cases,
production source guards, stop oracles and refusal behavior remain mandatory;
there is no retry or relaxed metadata check.

Rollback is to leave the additive adapter unused. There is no schema conversion,
old-fence deletion or destructive contraction. No persistent service, account,
timer, live key, S3/IAM/cloud/DNS setting, enrollment, dispatch, purge/restore,
API/SMTP or public launch is enabled. New paid resources: zero; the additional
3,500 RUB/month cap is unchanged. Provider rights, independent freshness,
bootstrap/location/clone custody, safety-state retention and owner RKN/launch
decisions remain separate gates. This receipt cannot activate them.
