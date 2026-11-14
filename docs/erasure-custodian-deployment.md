# Custodian deployment preparation — not an installation approval

This kit adds an explicit host entry point, an inactive systemd unit and a minimal
hash-pinned runtime. It does **not** create accounts/directories, install a unit,
bootstrap SQLite, read actual keys, contact Selectel, connect the application,
start purge/restore admission or authorize public launch. The example is
deliberately invalid until a trusted operator supplies independently verified pins.

## Trust and placement gate

The current protocol uses a local Unix socket. Writer and custodian therefore
share one Linux kernel, with distinct dedicated non-root UIDs; it is not a remote
RPC service. A writer with host-root, Docker-daemon, backup-admin or equivalent
authority can defeat this separation. Do not claim independent rollback custody
merely because a directory or service UID differs. Placement, administrators,
storage and exclusions from application backup/restore/clone domains require a
separate reviewed decision before real state or credentials are introduced.

Root/kernel and the operator-controlled interpreter/stdlib are trusted. The
launcher cannot validate the interpreter before that interpreter executes it.
The unit, interpreter, venv, source and their ancestors must be installed under
root control, outside writer/app write authority. No production bootstrap head,
generation, bucket, UID/GID or actual IAM claim is inferred by this kit.

## Prepared artifacts

- `ops/backup/erasure_custodian_host.py`: only `--check` or explicit `--serve`.
- `ops/backup/custodian/host.example.json`: non-secret root-owned configuration;
  exact schema, version, opaque scope/generation and process UID/GID pins.
- `ops/backup/custodian/requirements.lock`: seven exact records/hashes copied from
  the accepted backend lock; no application dependencies or new versions.
- `ops/backup/custodian/pult-erasure-custodian.service`: no install target,
  automatic restart, directory management, socket activation or socket cleanup.

The **future, separately authorized** installation should create a fresh venv
without pip/system-site packages and install the lock with a trusted external pip:

```sh
python3 -m venv --without-pip /opt/pult-erasure-custodian/venv
python3 -m pip --python /opt/pult-erasure-custodian/venv install \
  --ignore-installed --only-binary=:all: --no-deps --require-hashes \
  -r /opt/pult-erasure-custodian/ops/backup/custodian/requirements.lock
```

These are preparation instructions, **not commands to run on the present stand**.
Use a trusted Python 3.12+ and installer; verify interpreter, wheel supply and
root ownership independently. Only the lock's seven packages belong in the venv.
Install only the seven paths listed in the launcher's `CODE_FILES` into the source
tree; its root may additionally contain only the actual runtime `venv` directory.
Keep the unit, example, documentation and tests outside that source tree. Never
reuse the application checkout/environment or allow a writer to update this tree.

The unit invokes `-I -S -B`: it does not process `PYTHONPATH`, user site or `.pth`
startup files. Before project imports, the launcher validates an exact closed source
tree, rejecting namespace initializers, same-stem packages, root dependency shadows,
all project `__pycache__` and bytecode/native alternatives. It also rejects
unexpected runtime entries, links, non-root/writable files,
extra distributions or mismatched versions. Runtime traversal is bounded. It then
adds only the validated source and runtime paths. This supports a fresh dedicated
process, not embedding into an application process with preloaded `ops` modules.
Version metadata is not an anti-root tamper signature; trusted installation and
filesystem custody remain mandatory.

## Explicit inputs and privileges

After host/IAM approval, the operator must independently prepare:

| Input | Required custody |
| --- | --- |
| Unit/source/venv | root-controlled, no writer/app modification or startup hooks |
| `/etc/pult-erasure-custodian/host.json` | root-owned `0644` or `0444`, non-secret; protected ancestors |
| Original `/etc/pult-erasure-custodian/reader.json` | private to the system manager; never a writer key |
| `/var/lib/pult-erasure-custodian/` and existing `anchor.sqlite` | dedicated service UID, `0700` directory / `0600` DB and sidecars |
| `/run/pult-erasure-custodian/` | dedicated service UID, shared socket GID, `0750`; no pre-existing socket |

No input is repaired, chmodded, chowned, initialized or replaced by the launcher.
Config and credentials are bounded descriptor-validated JSON; duplicates,
unknown fields, non-finite numbers, links, hardlinks, special files and unsafe
permissions are rejected. A credential contains **only** `access_key` and
`secret_key`. There is no environment/default credential chain, transport factory,
TLS override, command, bootstrap or pending-reset field.

The unit uses an explicit absolute `LoadCredential` source, then passes only its
projected **path**, never key contents, in argv. systemd may project a root-owned
credential with a named-user ACL or use private ownership on a read-only mount;
see [systemd credentials](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#Credentials).
The loader accepts only service-owned `0400`/`0600` without ACL, or root-owned
`0440` with the exact ACL granting one service UID read access, no group/other
access and no additional named readers. Broader ACLs fail closed. The source
credential must still be protected separately; the unit does not audit its
pre-projection history or prove that Selectel IAM is actually GET-only.

The service UID/GID and writer UID in config must match the provisioned accounts.
No supplementary groups other than the socket GID are accepted, even if NSS adds
them despite an empty unit `SupplementaryGroups`. Core checks also require equal
real/effective/saved/fs UIDs, zero capabilities and `no_new_privs`. Keep dedicated
IDs reserved across the entire life of trusted state; do not use `DynamicUser`.

## Offline preflight and explicit start

The same unprivileged identity runs `ExecStartPre --check` and `ExecStart --serve`.
No condition silently skips a missing requirement. Do not install/start this unit
until the separate host review and owner authorization are complete.

`--check` loads local configuration/credential/runtime and validates existing
SQLite metadata against scope, generation and exact allowlisted location. It
does not bind/connect a socket, perform DNS/HTTP/GET, call `current`, `reconcile`
or `prepare`, run a subprocess or initialize state. SQLite may recover an existing
hot rollback journal on open; this is not promised to be byte-for-byte read-only.
Valid pending state remains pending. The fixed `HOST_PREFLIGHT_OK_OFFLINE_ONLY`
marker appears only after cleanup succeeds, and certifies **none** of IAM,
reachability, journal freshness, anti-rollback, backup isolation or launch readiness.
All failures use fixed diagnostics, without secrets, paths or echoed argv.

`--serve` explicitly opens the existing UID-authenticated listener. Startup does
not read the remote journal. The unit allows the socket families needed by the
existing reader, not arbitrary IAM rights; egress/destination controls and real
provider tests are separate gates. No API sync, SMTP, application wiring or writer
credential is introduced. The old protocol and reader implementation are reused.

## Failure, restart and rollback

There is deliberately no managed `RuntimeDirectory`/`StateDirectory`, `ExecStopPost`
cleanup or automatic restart: those can repair permissions or remove evidence
before the launcher can reject it. Provisioning is manual and separately approved.
After exit, a remaining socket causes refusal. After a machine reboot, a missing
runtime directory also causes refusal. A trusted operator must inspect the
listener/process and durable receipts before considering exact socket cleanup;
never delete a SQLite journal, reset pending, adopt remote bytes or rebuild a ticket.

Stopping the service is not permission to restore/replace/delete its DB, directory,
credentials or generation. Rollback means preserve evidence and stop admission;
schema/backwards compatibility and trusted recovery are not inferred from Git
rollback. Never place the state back inside application backup/clone procedures.

## Verification and remaining gates

Offline contracts cover schema, ACL grammar, lock equivalence and unit constraints.
The separate root-orchestrated Linux proof runs actual unprivileged children with
synthetic files/credentials. It checks ordinary and systemd-style ACL positives,
wrong identities/groups, unsafe files/FIFOs, poisoned environments, missing state,
pending preservation, foreign import side effects, explicit Unix listening and
stale-socket refusal. Network/subprocess boundaries are instrumented to fail
before external IO; this is not a real IAM/reachability test.

The unit is parsed on a temporary copy with fixture paths/executable substitutions;
it is not installed or started under PID1 by this proof. Thus actual systemd
credential projection, namespaces/cgroups/LSM support and real host/egress setup
remain unproved. Host provisioning, independently trusted bootstrap/recovery,
live Selectel GET-only permission tests, root/backup-domain custody, purge/restore
fencing, full deletion mapping/replay, retention and PITR admission remain open.
