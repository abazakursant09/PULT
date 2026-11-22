# Working journal v2: separate storage namespace contract

This is **source-only, socket-free storage support**, not an activated working
contour. It creates no account, directory, database, bucket, key, anchor or
generation. It does not admit real identifiers, enrollment, journal dispatch,
purge/restore, API/SMTP or public launch. Existing synthetic closures and the
test-only mutable v1 host/S3 allowlists remain unchanged.

## Why v2, rather than renaming v1

Mutable v1 uses one `pult/erasure-test/<scope>/journal.json` object and conditional
replacement by exact ETag. The accepted v2 codec already uses immutable objects:

```text
pult/erasure-v2/<scope>/<canonical-generation-UUID4>/events/<20-digit-sequence>.json
```

The working path keeps that codec and key shape. It is **not** a new wire format
or a mutable `pult/erasure-working/.../journal.json` adapter. Separation is the
complete endpoint/bucket/scope/generation tuple, not a replacement of the literal
`erasure-v2` prefix. No existing synthetic record is migrated, adopted or reset.

`ops/backup/working_journal_v2.py` adds a distinct `WorkingNamespace` value and
reader/writer capability types. Its constructor requires every component:

| Component | Contract |
| --- | --- |
| endpoint/region | Exact reviewed HTTPS endpoint for ru-3 |
| bucket | Canonical 3–63-character bucket name beginning `pult-custodian-working-` |
| scope | `pult-work-` followed by 1–54 ASCII letters, digits, `_` or `-` |
| generation | Canonical lowercase UUID4, supplied independently by a future trusted operator |
| sequence | Exact integer, not bool, from 1 through the existing v2 maximum |

The value is frozen; location and object keys are derived. There is no arbitrary
key, prefix, URL, query, mode, discovery, environment fallback or default
generation. Different buckets, scopes or generations produce different locations.
All validation precedes credential validation and client creation.

Names only express working intent. They do **not** prove that the bucket exists,
is private, is owned by the operator or has the necessary IAM. UUID syntax does
not establish freshness or provenance. The unchanged v2 record digest binds scope
and generation, **not the bucket URL**: copying identical records between buckets
with the same scope/generation can still verify as bytes. Independently trusted
location binding and anti-clone/custody checks remain necessary.

## Bounded reader and writer capabilities

`WorkingEventReader` and `WorkingEventWriter` require an exact `WorkingNamespace`,
an explicit credential and an exact `httpx.MockTransport`. There is deliberately
**no live/default transport branch**, arbitrary client, transport subclass or
TLS/proxy option. A MockTransport handler is trusted test code; this is not a
sandbox against a malicious Python caller. Python object privacy is not an
authorization boundary.

The adapter reuses the accepted v2 `Binding`/`Head`/`Event` codec identity and the
existing SigV4 signer through ordinary fully qualified imports. It does not
monkeypatch module aliases or adapt its types into the frozen synthetic runtime.

- Writer exposes only typed `create(previous, event)`. It derives the immutable
  next key and canonical bytes, signs one PUT with `If-None-Match: *`, and never
  uses `If-Match` or overwrites a previous sequence object.
- Reader exposes only `read_next(previous)`. It signs GET for the exact next key,
  rejects non-identity encoding, bounds decoded bytes by the existing 2048-byte
  record limit, and checks canonical bytes, scope, generation and chain digest.
- A received HTTP200 after successful stream close means
  `PUT_HTTP200_UNCONFIRMED`, **not** a durable ACK, head advance or admission.
- A received 412 after successful close means conflict. A timeout, arbitrary
  handler exception, failed stream close or other status means UNKNOWN; a handler
  cannot forge a conflict by raising the adapter's exception type.
- Missing GET is not initialization. No redirect, automatic retry, list, delete,
  reset, bootstrap, rearm, compaction or key-discovery method exists. Close failure
  leaves the capability closed and emits a fixed diagnostic.

The wrapper has a small independent request/status path because the frozen
synthetic store imports its codec under a captured module name. Altering that
loader or aliasing it into this API would change the accepted closure. The codec
and signer are reused; frozen source files, hashes and launcher profiles are not
rewritten to make the new capability fit.

## Compatibility and rollback

Old v1 entry points still reject working object keys. Existing v2 storage helpers
use a generic bucket grammar; their filename or synthetic description was never
a working-bucket IAM deny. This addition does not claim otherwise. Existing
synthetic protocol exact-type and source checks are not relaxed to accept these
new classes. They cannot be selected by changing a string in an old host config.

Mixed-version operation is additive: older code does not know the new module;
the new module does not modify old state. Rolling back this source slice removes
the optional contract only; it is not permission to delete/adopt state or rewind a
generation. There is no data migration or destructive contraction in this stage.

## Proof and remaining activation gates

The focused pytest suite uses artificial identifiers and credentials with socket
connects denied. It exercises real codec/SigV4/HTTP serialization against the
mock handler and verifies namespace rejection, exact key derivation, split
capabilities, bounded reads, status/cleanup ambiguity and no automatic retry.
The existing backup synthetic workflow runs it without skips/continue-on-error;
all existing native predecessor gates still run unchanged. These are not provider
IAM, Linux ownership, deployment or sustained availability proofs.

Before real data: independently admitted source/runtime/configuration, exclusive
fresh bootstrap and location binding, separate real IAM keys and provider
positive/negative tests, authenticated review-to-registry linkage, mutation-time
fencing, independent backup/clone/recovery custody and finite safety-state
retention/capacity rules must pass. Ordinary 30-day recovery, 7-day backup and
14-day diagnostic-log periods are not automatically safety-state retention.

No real bucket/UUID/key is allocated by the tests. This source slice adds no paid
resources or cloud usage and does not authorize an actual provider test.
