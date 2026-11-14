# Independent erasure-head custodian: dormant reference implementation

This slice adds a **local, independently read-back, durable storage protocol**.
It does not deploy a custodian, authenticate an RPC boundary, grant IAM access,
execute a purge, export a restore checkpoint, admit a restored database, enable
API/SMTP, or authorize public launch. Synthetic local tests are not live Selectel
proof or evidence that the actual infrastructure is isolated.

## Ownership and bootstrap

`S3JournalReader` receives only the reader credential and exposes no write method.
It shares the existing reviewed TLS/SigV4 path, not the writer credential. Its
private test seam accepts only exact `httpx.MockTransport`. The existing
`S3JournalStore` split-key constructor and unanchored behavior remain compatible.

The trusted custodian owns `SQLiteAnchor`, its private directory and its own
reader-only capability. Both scope and exact location are bound to the configured
reader and the persisted metadata. A writer runner receives candidate bytes/ETag,
not authority to change custodian storage. The reference runner uses in-process
objects to exercise the protocol; **Python object privacy is not a security
boundary**. Production requires a separate OS principal/service and authenticated
ephemeral-session protocol, neither implemented nor claimed here. The journal
writer must have no filesystem/backup/administrative access to the custodian.

Creating a custodian file is an explicit operation with operator-supplied scope,
UUIDv4 generation, location and independently trusted bootstrap head. It uses
exclusive file creation; it never overwrites or repairs existing/partial state.
Opening missing, corrupt, linked, wrong-scope, wrong-generation, wrong-location or
unsupported-schema state fails closed. Never derive the bootstrap from a backup,
the untrusted journal transport, or an accompanying file. A known empty synthetic
fixture is the only empty history asserted by tests.

The generation is a binding identifier, **not an anti-rollback authority**.
Restoring an old custodian file together with an old journal can still defeat a
same-generation check. A dedicated limitation test demonstrates this; do not call
it rollback protection. Custodian storage must stay outside application backups,
journal-writer authority, database restore/clone procedures and their rollback
domain. Infrastructure isolation and recovery controls remain an explicit gate.

## Persistence and linearization

The local file uses SQLite rollback journal mode `DELETE`, `synchronous=EXTRA`,
`trusted_schema=OFF` and explicit transactions. `BEGIN IMMEDIATE` serializes state
transitions with a zero busy timeout: storage contention fails, it does not retry
or dispatch a write. Confirmation revalidates the full expected state inside the
same transaction that advances the head and finalizes its receipt.

`EXTRA` is selected because SQLite documents that, in rollback/DELETE mode, it
also synchronizes the journal directory after unlink; `FULL` alone is not always
durable across power loss. These guarantees still depend on correct filesystem,
VFS and hardware flush behavior. Tests below terminate processes, **not machine
power**. See [SQLite synchronous settings](https://www.sqlite.org/pragma.html#pragma_synchronous),
[transactions](https://www.sqlite.org/lang_transaction.html) and
[atomic-commit assumptions](https://www.sqlite.org/atomiccommit.html#_hardware_assumptions).

Only a pre-provisioned, private, local filesystem directory is supported. The code
does not change Windows ACLs, provision a service account or prove access isolation.
The main database and rollback sidecar require the same directory protection;
never delete/rename a hot journal to "repair" a pending attempt. No schema
migration, automatic restore, compaction, retention expiry or pending-reset API
exists. Receipts remain bounded by the journal's 128-event capacity; these are
safety state, not ordinary 14-day diagnostic logs. No new production retention
decision is inferred. Real identifiers must not be introduced before that gate.

## One acknowledged attempt

1. The custodian reads its committed head and rejects any pending attempt.
2. `Journal.prepare` independently reads/validates canonical complete history and
   builds immutable candidate bytes extending the exact head by one event. An
   existing unanchored descendant is never adopted, including an exact repeat of
   the caller's event. A repeat already at the trusted head returns `NO_NEW_ATTEMPT`,
   not a newly confirmed write or permission to purge.
3. In one transaction the custodian stores `PREPARED`: attempt UUID, exact base
   and target heads, canonical payload digest and reader-observed ETag. Scope,
   location and generation are fixed by the custodian metadata. The revision
   advances; pending now blocks all other new attempts.
4. A durable atomic claim changes `PREPARED` to `DISPATCHED` **before** any PUT.
   In-process tickets are single-use and cannot be reconstructed from receipts.
   A crash after claiming but before sending sacrifices availability, never
   permits an automatic replay.
5. The runner sends exactly one conditional PUT using the bound ETag. Conflict,
   timeout, lost response or other ambiguity never triggers a second PUT.
6. Only after a normal acknowledgement does the custodian independently GET the
   journal through its own reader. It requires the exact canonical candidate,
   not merely a matching ancestor or a descendant beyond the prepared target.
7. Confirmation consumes the live ticket before doing IO. A transaction rechecks
   generation/revision/pending identity and advances the head, marks `COMMITTED`
   and clears pending atomically. Successful COMMIT is the linearization point;
   the returned snapshot is as of that point, not a perpetual latest-head lease.

The invariant is `revision = 3 * committed_new_events`, plus 1 for PREPARED or
2 for DISPATCHED. Persisted receipts and metadata must agree on every load.
Cancellation revokes the complete live ticket lineage in a `finally` path;
`KeyboardInterrupt`/`SystemExit` propagate without leaving a confirmable dispatch.

## Unknown is not the same as absent

| Outcome | Meaning | Next automatic mutation |
| --- | --- | --- |
| `ANCHORED_STORAGE_ONLY` | Known acknowledgement, independent exact GET and acknowledged custodian COMMIT. | None; no purge/admission authority. |
| `NO_NEW_ATTEMPT` | Exact event was already in the independently anchored history. | None. |
| `CONFLICT` | Conditional PUT conflicted; DISPATCHED receipt remains pending. | None. |
| `UNKNOWN` | Dispatch/readback was ambiguous before confirmation COMMIT; pending blocks progress. | None. |
| `ANCHOR_RESPONSE_UNKNOWN` | Confirmation COMMIT was attempted but its response/cleanup failed. The receipt may already be COMMITTED or remain DISPATCHED. | None; inspect the durable receipt, never resend. |

An interrupted process must not infer acknowledgement from remote bytes. On
restart no live ticket is restored, even when the candidate is visible. Read-only
`reconcile(attempt_id)` reports `OBSERVED_COMMITTED_RECEIPT` or pending diagnostic
state, never changes an earlier unknown result into success, creates a ticket,
resets a blocker or sends a write. SQLite may recover a hot rollback journal while
opening the file; "read-only" here means no protocol-state mutation or network
write, not a prohibition on the storage engine's crash recovery.

`current()` rejects pending attempts, requires the exact independently anchored
head and checks custodian state again after its GET. It is an observation, not a
lease or a purge/restore fence. Production must restrict journal mutation to the
coordinated writer path and validate provider consistency; a compromised writer
and stale reads are not solved by CAS or SQLite alone.

## Proof and remaining gates

Unit tests cover immutable preparation, role/location/scope binding, rollback and
unanchored descendants, exact readback, contention, cancellation, commit-error
boundaries and terminal receipt diagnosis. Separate-process tests forcibly exit
before/after prepare/claim/confirmation commits and around PUT persistence; two
processes also race from the same captured base. They reopen actual SQLite and
assert no automatic replay/reset. A file-backed remote fixture is **not S3**.

The old unanchored protocol and its transport regression tests run before and
after extraction. Existing restore/quarantine, backup and canary guards remain
unchanged and are included in the combined local gate. The CI workflow adds these
new tests but does not package them into the backup image or execute a live CLI.

Still open: independently provisioned/isolated custodian and authenticated service
boundary, bootstrap custody and anti-rollback recovery controls, live provider
protocol/IAM tests, explicit recovery of unresolved attempts, stable subject IDs,
full purge/replay mapping, purge/restore fencing, retention enforcement and PITR
admission. Existing restore guards remain unchanged and accept no journal export
from this code. A `complete` journal event is still a caller assertion, not proof
that every related record was erased.
