# Trusted working profile: offline capture and namespace preparation

This stage prepares a **trusted profile/check path**, not a working service. It
imports the captured working module and instantiates only `WorkingNamespace`.
It constructs no S3 client (not even a mock), reads no credentials or SQLite,
starts no controller/listener/unit, creates no state and admits no journal
dispatch, enrollment, purge/restore, API/SMTP or public launch.

Existing synthetic PG/host/intake formats and their frozen catalogues remain
unchanged. The new check does not promote their admission objects or add a
"working" mode to their operation APIs.

## Trust roots and profile

OS/root, the fixed interpreter/stdlib, this entry program and an independently
supplied profile SHA256 are trust roots. This is not self-attestation or defence
against hostile root/kernel/interpreter or malicious in-process code. Trusted
entry wiring supplies the path and digest; they must never come from an HTTP/RPC
caller. Computing a digest from a candidate file and immediately treating that
same digest as approval does not establish trust.

`ops/backup/working_launch_profile.example.json` is intentionally invalid: null
identities/hashes and placeholder names. Its pretty-printed bytes are not a
canonical runtime packet. It must not be installed or "fixed" automatically.

The exact canonical profile contains only:

| Field | Required binding |
| --- | --- |
| `format` | `pult-working-launch-profile-check-v1` |
| `release` | Normalized absolute closed bundle root and independently pinned local device/inode |
| `entry_sha256` | Approved exact bytes of the already trusted entry file |
| `actor` | Explicit unprivileged check-process UID and primary GID |
| `namespace` | Exact endpoint/region/bucket/scope/generation fields of `WorkingNamespace` |

The profile lives **outside** the closed release. It is canonical ASCII JSON
with a trailing newline; duplicate/extra fields, NaN, noncanonical values,
boolean-as-integer IDs and malformed paths/UUIDs are refused. Root-owned0444
single-link regular files and protected ancestors without POSIX ACLs are
required. Shared root-owned sticky ancestors may have unrelated sibling churn;
every owned directory/file and descriptor-relative child binding stays exact.

Local device/inode is a storage binding, not globally unique anti-clone identity.
Working names/UUID syntax do not prove bucket ownership, IAM, freshness or
independent generation provenance. The record codec still does not hash the
bucket URL; same-binding cross-bucket byte copies are not detected by naming.

## Fixed bundle and execution barrier

The root-owned0755 bundle contains only the entry, eight fixed parent inputs,
seven exact reviewed wheels and their exact extracted dependency tree. The
parent inputs are the accepted working module, v2 codec, S3 credential validator,
journal types, canary signer, requirements lock, PG descriptor helper and cold
captured-import helper. All eight byte pins are compiled into the new entry.
No dependencies or versions were added. Release assembly must supply exact LF
Git source bytes; an ordinary Windows checkout is not an approved Linux release.
The global EOL attributes are themselves frozen by a predecessor catalogue and
remain unchanged. Only synthetic fixture assembly may normalize checkout line
endings; the actual checker never normalizes captured bytes.

1. Require isolated CPython3.12 at `/usr/bin/python3.12`, `-I -S -B`, no
   optimization, one main thread, all real/effective/saved/fs IDs consistent,
   five zero capability sets and `no_new_privs=1`. Require the three closed
   system stdlib paths/finders and no preloaded payload/dependency modules.
2. Descriptor-read and hash the fixed PG helper before executing its captured
   bytes. Reuse **only** its `Capture` primitive, never its synthetic PG loader.
3. Capture the independently approved profile, root identity, actor and exact
   source bytes, including the entry's consistency hash. Entry hashing cannot
   establish trust in code that has already started; external entry custody is
   still required.
4. Hash the cold helper before executing it. Reuse only its captured importer
   primitives and fixed seven-wheel inventory, not `_intake`, `_activate`,
   `ColdReady` or any existing-state/credential constructor.
5. Verify wheel hashes and bounded canonical members, then every extracted
   file and directory. Reject extras, links, ACLs, namespace shadows,
   `.pth/.pyc/.so`, unsafe modes and byte mismatch. Keep the protected file
   descriptors and rehash their complete bytes through the execution lifetime.
6. Only after the full closure check, install the explicit captured module table,
   import the normal fully qualified working module and instantiate only
   `WorkingNamespace`. Verify codec/signer/validator identity and the derived
   first key and location. No alias conversion into the old synthetic runtime.
7. Recheck origins, actors, bytes and descriptors, rollback imported payload and
   dependency modules, confirm the closed interpreter, then close owned
   descriptors. Any mismatch or cleanup uncertainty refuses success.

This uses the accepted capture/import primitives rather than creating a general
launcher framework. The new schema and small wheel/layout adapter are necessary
because the old PG loader is fixed to 15 synthetic sources/four PG wheels and
the old cold intake is fixed to 13 predecessor sources. Their restrictions are
not relaxed to make this bundle fit.

## Check interface and receipt

The only verb is `check`; there is no serve/start/worker/deploy or default mode:

```text
/usr/bin/python3.12 -I -S -B <trusted-entry> check <protected-profile> <independently-approved-sha256>
```

This is an interface description, **not an instruction to run it on the current
stand**. The operator must independently establish entry, profile, interpreter,
source and dependency custody first. There is one attempt per process; no
automatic reset, fallback, source repair or alternate profile discovery exists.

The JSON receipt says `VERIFIED_OFFLINE_NOT_LAUNCHED`; all launch, remote-IO,
journal-dispatch and purge/restore flags remain false. It is an unsigned completed
observation, not a transferable capability, freshness lease or permission to
reuse a closed capture. A future working start needs a fresh validated lifecycle,
not a saved receipt. Failure is a fixed diagnostic without paths/secret values.

## Verification, compatibility and remaining gates

Portable tests distinguish mocked mechanics from actual custody. The separate
Linux proof creates only disposable root-controlled fixtures, uses distinct
numeric unprivileged children under a network namespace, and tests positive and
negative file/profile/runtime/import cases. No account, service or real key is
created. Unsupported/optimized proof runtimes must return NOT_RUN, never PASS.
The existing backup workflow runs both proof layers without suppressing old
gates. The fixture release, marker positive controls and cleanup are independently
checked by the proof harness; real Selectel rights and host provisioning are not.

Rollback here means stop and preserve evidence; no database, journal history,
generation, key or profile file is overwritten by the checker. Mixed versions
remain additive and old formats reject the new profile. No schema migration or
destructive contraction occurs.

Still open: trusted real-host installation and full runtime/operator integration,
real IAM/provider tests, fresh exclusive bootstrap/location custody, authenticated
review/enrollment and mutation-time fencing, independent backup/clone/recovery,
finite safety-state retention/capacity and the owner's deferred RKN/launch gates.
Ordinary30-day recovery/7-day backup/14-day diagnostic rules are not safety-state
retention. No new paid resource or cloud usage is committed by this preparation.
