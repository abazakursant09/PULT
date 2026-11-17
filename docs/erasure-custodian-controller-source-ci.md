# Read only controller CI proof

The additive `ops/backup/custodian_v2/controller/` package preserves the four
reviewed local controller sources byte for byte. The existing schema2 source
catalogue, cold bootstrap and v1 runtime remain unchanged. The application and
backup or restore entry points do not import this package.

Its source profile is a draft catalogue, not an approved working profile. It
contains no credential, database or pending operation and admits no operation.
Successful reads can repeat only within the same live controller process and
original thread. Durable fences remain after success or uncertainty; a new
controller cannot adopt even a completed session. The 64 call bound is a
protective limit, not a production capacity measurement.

## CI execution

The existing synthetic workflow runs this command on an Ubuntu 24.04 host with
systemd as PID 1 and cgroup v2:

```sh
sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B \
  ops/backup/tests/custodian_v2/controller/ci_gate.py --reviewed-by-main
```

The controller gate retains the host PID namespace because it exercises actual
systemd transient units. It uses an isolated network namespace, and its parent
test service independently sets `PrivateNetwork=yes`. Only loopback is present;
S3 uses mandatory fake transport and synthetic credentials. Seven existing
hash-pinned wheels are rehashed and unpacked in a disposable fixture. No local
developer paths, Git metadata, historical receipts or installed app packages
are required.

Before the full gate, cancellation during scope allocation and during its handoff
must leave no owned directory. Two further real fault scenarios interrupt a test
containing a descendant process, transient helper unit, a partial unpublished
record and synthetic files. Timeout and cancellation must both prove cleanup.
The full gate then requires exactly 52
tests, including 30 actual process kill boundaries, with no errors, failures,
skips or expected failures. All inputs are hashed before and after execution.
The wrapper independently validates the current receipt and rehashes its exact
input set. Extra report fields and optimized Python execution are refused.

## Owned resource cleanup

Each CI invocation creates exclusive root owned scope directories and records
their inode identities. Before requesting a helper unit, it durably records
that unit in its scope. Helpers require the scope's active marker and are
ordered after, and stopped with, the parent test service. The parent has a
900 second runtime limit, a two second stop timeout, no restart and whole cgroup
termination.

Cleanup runs after success, failure, timeout and caught cancellation. It removes
the active marker first, stops only the parent and recorded helper units,
checks that their cgroups are empty, and removes only the identity checked
synthetic trees. Unproven cleanup fails the gate. An uncatchable host or wrapper
kill is not reported as success; PID 1 still bounds the parent runtime, but
leftover scope files require inspection before another gate can pass. Nothing
installs a persistent service, timer, account or cleanup job.

## Deployment gates remain separate

This source CI proof does not deploy the custodian, authorize live S3 or IAM,
admit purge or restore, certify reboot recovery or capacity, or open the public
service. Root, system Python, systemd and the trusted host remain trust roots.
The proof does not protect against a hostile root administrator.

Rollback is to leave the additive package unused; no database conversion or
reset is needed. Real independent placement still requires the exact approved
release, identities, working profile, cloud access tests and a fresh restore
authority. Any paid placement must remain within the additional 3,500 RUB per
month ceiling. This package creates no paid resources and changes no application,
API synchronization, SMTP, DNS or production settings.
