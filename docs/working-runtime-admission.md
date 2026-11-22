# Working runtime admission: existing local resources, operation gate closed

This additive stage holds a fresh captured source closure across **actual local
role construction**. It is not working-service activation, dispatch, enrollment,
purge/restore or public launch. No existing synthetic profile, loader, catalogue,
global EOL metadata or predecessor byte pin is changed. No dependency is added.

The preceding `VERIFIED_OFFLINE_NOT_LAUNCHED` receipt is not an input capability.
`working_runtime_admission.py` performs its own fresh intake and returns a held
`RuntimeAdmission`, not a saved check result. `working_runtime.py` owns resources;
the public owner exposes only `check`, `snapshot` and `close`, never an underlying
client/anchor, callback, transport, request, server or operation method. Python
private attributes are not an authorization sandbox: the fixed trusted embedding,
not an untrusted application/RPC caller, must own this process.

## Trust and profile

OS/root, this fixed entry, CPython3.12/stdlib, the OS CA store and an independently
approved profile digest remain trust roots. Entry hashing is consistency checking
of already trusted code, not self-attestation or defence against hostile root.
The source root is derived from the exact entry location, never from a remote
request. The unchanged checker is root/descriptor-read and checked against
`01d4023f...` before execution; only its validation/table helpers are reused.
Its old `verify_profile` and synthetic PG admission loaders are never called.

The canonical ASCII JSON profile, trailing newline included, has exactly:

| Field | Meaning |
| --- | --- |
| `format` | `pult-working-runtime-admission-v1` |
| `release` | Exact protected closed source root and local device/inode |
| `entry_sha256` | Independently approved bytes of the trusted new entry |
| `role` | Exactly `custodian` or `writer` |
| `actors` | Both dedicated UID/GID pairs; different UIDs and GIDs |
| `namespace` | Exact accepted working endpoint/region/bucket/scope/generation grammar |
| `peer_ipv4` | Independently approved canonical public S3 IPv4; not resolved/discovered here |
| `resources` | Role-specific existing private inputs below |

Both roles pin a credential path/device/inode and SHA256 of its access-key value.
The credential file itself contains only explicit `access_key` and `secret_key`.
Custodian additionally pins existing `anchor.sqlite`, its sibling `owner.lock`,
and an expected head sequence/digest/revision. Writer has no custodian-state or
lease field. Source/profile paths and private writable/credential trees cannot
overlap. No generation, state, lock, key, head or configuration is created,
repaired, discovered, chmodded, chowned, replaced or adopted by admission.

The entire process environment is exactly `PATH=/usr/bin:/bin` and
`LANG=C.UTF-8`. `-I -S -B` alone is insufficient: the inherited TLS constructor
uses OpenSSL defaults before HTTPX, so `SSL_CERT_FILE`, `SSL_CERT_DIR`, key-log,
proxy and other hidden environment inputs must be excluded. The fixed live
constructor keeps verified OS trust, provider Host/SNI, pinned-IP TCP, no proxy,
no redirects and no retry. Construction performs no remote request or DNS.

## Closure and identity

The distinct closed release includes the entry and fifteen pinned parent/payload
files, seven accepted wheels and their exact extracted tree. The predecessor
checker layout is not widened: placing this addon into its old release makes the
old checker refuse extras. Release assembly supplies exact LF Git bytes; runtime
capture never normalizes them. Only synthetic fixture assembly may normalize a
Windows checkout. The frozen global `.gitattributes` remains unchanged.

The existing `Capture` primitive retains descriptor-relative ancestry, file/ACL
identity and full source bytes. The existing captured importer uses an explicit
module table and origin checks, not ambient Python paths. Generic codec, Lease,
anchor and live-store primitives load once under the required captured aliases;
cross-module class identity is checked before resource construction. Old
synthetic runtime/admission objects are never manufactured or promoted.

## Resource lifecycle

1. Verify the independent profile, actor, source root, complete source/wheel/tree
   closure and closed interpreter before importing the role payload.
2. Retain the owner **before** opaque constructors can raise. Recheck source,
   role, PID, thread, environment and deadline at construction boundaries.
3. Custodian validates private existing state/lock, acquires the actual exclusive
   `Lease`, opens existing schema2 through `V2Anchor`, and verifies namespace,
   exact approved head/revision and no pending attempt **before** reading a key.
   Writer never opens custodian SQL or Lease.
4. Descriptor-read the private role credential with bounded no-follow/nonblocking
   reads, exact identity/modes/ACL checks and a retained byte snapshot. Construct
   only the fixed GET-only reader or PUT-only writer client. Do not call it.
5. Keep captured sources, private descriptors and Lease through the lifetime.
   Recheck before/after public observations. State identity may remain stable
   across future legitimate transactions; identity is not rollback freshness.
6. Close the client first, rollback owned import registrations, close private
   descriptors and Lease, then close source Capture. A completed-close receipt
   appears only after every required cleanup succeeds.

Known state sidecars/extra directory entries are refused before SQL. Nevertheless,
the inherited SQLite connection uses `mode=rw` and may perform engine recovery;
admission does not promise physically byte-for-byte read-only SQLite access.
It does not reset pending history or infer bootstrap from missing objects.

The 40-second guard is **cooperative**, not a hard stop of a blocking constructor.
An independently enforced controller/cgroup stop remains a separate full-contour
gate. Failed/partial construction, changed source or ambiguous cleanup poisons
the graph until process exit. A missing constructor return is never evidence of
no allocated object. There is one attempt per dedicated process, no rearm/reset.

`owned_host` registers an at-fork callback during import. Namespace rollback
cannot unregister it; this is not restoration of a pristine interpreter. The
dedicated process must exit after completion/refusal. No embedding/reuse/forked
worker pool is admitted. Same-account root/backup administrators remain trusted;
this local source/Lease check does not establish independent rollback custody.

## Check interface and proof

Only a trusted, already provisioned launcher may supply protected paths and the
independent digest. The only CLI verb is `check`:

```text
env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/python3.12 -I -S -B <fixed-entry> check <protected-profile> <independent-sha256>
```

This documents an interface, not an instruction to run it against the real stand.
Its successful status is `LOCAL_RUNTIME_CHECKED_AND_CLOSED_NOT_OPERATIONAL`.
All operation, dispatch, enrollment, purge/restore and launch flags remain false.
The receipt is an unsigned observation, not a transferable operation capability.

Portable fault models and a separately reviewed Linux fixture prove different
layers. Linux uses actual distinct numeric actors, existing artificial SQLite,
private key descriptors, flock ownership and fixed TLS-client construction in a
network namespace. No live provider/IAM or application/user data is involved.
Unsupported/optimized native runtime is NOT_RUN, not PASS. Existing CI gates and
their strict tail are preserved; both new proof layers are mandatory.

Remaining full-contour gates: independently enforced process stop; authenticated
writer/controller/keeper coordination; real least-rights IAM/TLS/provider tests;
trusted exclusive location/bootstrap and anti-clone/recovery custody; review to
registry and mutation-time freshness/fencing; finite safety-state retention and
capacity. The owner's RKN/public-launch gates remain open. Account30-day,
backup7-day and ordinary diagnostic14-day periods are not safety-state retention.
No new paid resource or cloud change is made by this source preparation.
