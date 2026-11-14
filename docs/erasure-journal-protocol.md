# External erasure journal: bounded, dormant storage slice

This is **not** an enabled deletion worker, authoritative latest-head service,
complete retention implementation, or restore-admission procedure. It does not
change the application, database, restore runner, Docker image or cloud settings.
Only synthetic offline data has been exercised until a separate provider test.

## Protocol

`ops/backup/erasure_journal.py` implements one canonical JSON object per installation
scope. It contains a genesis bound to version/scope and a contiguous SHA-256 chain
of `intent` and `complete` records. Only canonical opaque UUIDv4 subject/purge IDs
are permitted; there are no names, email addresses, reports or credentials. IDs
remain private/pseudonymous, not a claim of anonymization. The same stable subject
ID must survive a database rollback; allocating that identity in the application
is deliberately not implemented by this storage slice.

Completion requires a matching intent and matching subject. It records a caller's
assertion, **not proof of deletion**. No code here deletes data. Exact repeated
events are read-only observations; conflicting identities/transitions fail.
The journal cannot be used as a substitute for the full ownership/purge mapping.

The reader returns validated bounded bytes and the strong opaque ETag from that
same GET. A new append requires the exact independently supplied current head,
then performs one `If-Match` PUT. Explicit initialization uses `If-None-Match: *`;
ordinary missing-object reads never initialize. No retry loop, deletion, listing,
multipart, Object Lock, TTL, automatic compaction or history truncation exists.
At 128 records or 64 KiB it stops; capacity is an availability limit, **not a
retention period**. A reviewed extension is required before that limit is used.

A PUT acknowledgement is followed by one separate-reader verification. Even a
positive result is named `STORED_UNANCHORED` (or `ALREADY_STORED_UNANCHORED`): this
component has not durably advanced any independently trusted anchor. CAS controls
concurrent honest writers; it does **not** make an overwritable object immutable
against a compromised writer. ETags are preconditions, not SHA-256 identities.

## Rollback, ambiguity and missing production obligations

Every observation requires an independently trusted `(sequence, digest)` floor.
Lower heads, equal-sequence forks, and descendants lacking that exact ancestor
fail. A stale floor **cannot prove latest-head freshness**: an older valid history
at/above that floor could hide later erasures. Never bootstrap the floor from the
restored DB, the backup, or a file transported beside the journal.

An independent head custodian, its persistence/acknowledgement protocol, complete
historical coverage, durable attempt receipts across crashes, and fencing of
concurrent purges/restores remain unimplemented. No service/CLI/scheduler restarts
or retries this library. A caller must not treat a restart as permission to retry
an unknown attempt. These are required before irreversible purge or admission;
there is intentionally **no production integration** to bypass these gaps.

Timeout or unexpected write response is `UNKNOWN`. There is at most one read-only
reconciliation GET after dispatch, never a second PUT. Even seeing the candidate
after a lost response keeps `UNKNOWN`; absence is not permission to retry. An
explicit CAS conflict produces `CONFLICT` and no automatic refresh/retry. Storage
outcomes never authorize purge, reuse of an identity, or release of restored data.

## Concrete transport and verification boundary

`erasure_journal_s3.py` reuses the existing reviewed canary SigV4 signer **only**;
it never runs canary orchestration. Only allowlisted Selectel HTTPS region endpoints
and `pult/erasure-test/<scope>/journal.json` keys are accepted. The test-prefix
restriction is intentional; production locations require separate review.
Credentials are explicit, separate reader/writer keys (IAM principal separation
must independently be provisioned/verified). There is no environment/default
credential discovery. TLS verification is mandatory, proxies/redirects are off,
GET/PUT retries are zero, read size is bounded before JSON parsing, compressed
responses are rejected, and raw provider/credential/payload errors are not logged.
Arbitrary clients/transports cannot be injected: the private test seam accepts
only an exact `httpx.MockTransport`, never a live or subclassed transport. The
component constructs its real transport with explicit TLS verification, no
environment proxies and zero retries. No executable live CLI or credential
loader is added.

Tests exercise canonical encoding, state transitions, rollback/forks, concurrency,
lost responses, read-back failures and the actual httpx request/signing path with
sockets blocked. These are **offline protocol/serialization proofs**, not provider
durability, IAM, revocation or live Selectel CAS proof. A fresh bounded live test
requires new separately approved credentials/grants; revoked old keys are not reused.

The PR434 restore guard remains unchanged and accepts only its independently
trusted, complete **empty** checkpoint. This journal exports no checkpoint and
cannot disguise nonempty history as empty. Physical purge/replay, independent
latest-head fencing, seven-day backup retention and PITR admission remain open.
