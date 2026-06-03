# Runtime Envelope Constitution (Sprint 73)

**Status: FROZEN.** The Runtime Envelope is the immutable, deterministic identity
container sitting **above** the Runtime Signal Collector and **below** Operator
Cognition V2. It binds the runtime's structural anchors into one SHA-256 hash
that is byte-identical across replays.

Built this sprint. No existing module modified (cognition, collector, logic all
untouched — observe-only mandate preserved).

## Position in the runtime stack

```
upstream_events
  -> runtime_signal_collector
  -> runtime_signal_set
  -> RUNTIME_ENVELOPE          <-- this layer (index 3, frozen)
  -> operator_cognition_v2
  -> operator_console
```

Encoded in `envelope_topology.TOPOLOGY` and attested by `topology_attestation()`.

## The immutable container (FROZEN contract `runtime-envelope/1`)

Seven fields (`envelope_contract.ENVELOPE_FIELDS`):

| Field | Origin |
|---|---|
| `boot_id` | derived — `sha256(baseline_anchor, collector_signature, cognition_v2_runtime_hash)` |
| `session_id` | derived — `sha256(boot_id, signal_set_signature)` |
| `collector_signature` | supplied anchor |
| `signal_set_signature` | supplied anchor |
| `cognition_v2_runtime_hash` | supplied anchor |
| `baseline_anchor` | supplied anchor (schema baseline, Sprint 69) |
| `topology_attestation` | derived — `sha256(TOPOLOGY)` |

`runtime_envelope_hash = sha256(domain="PULT-ENVELOPE/1", {seven fields})`.

The four **supplied anchors** (`INPUT_COMPONENTS`) come from outside; the three
derived fields are pure functions of them. Nothing else participates.

## Determinism guarantees

`runtime_envelope_hash` is:
- **SHA-256** (64 hex chars).
- **byte-identical across replays** — same anchors → same hash, always.
- **independent of clocks** — no `time`/`datetime` anywhere in the core.
- **independent of environment** — no `os.environ`/`getenv`/hostname.
- **independent of process state** — no pid/thread/memory-address/randomness.

Canonicalization (`envelope_hash.canonical_bytes`): JSON, `sort_keys=True`,
`separators=(",",":")`, UTF-8, non-ASCII preserved. Dict key order does not
affect the hash. Domain tags (`PULT-BOOT/1`, `PULT-SESSION/1`,
`PULT-TOPOLOGY/1`, `PULT-ENVELOPE/1`) prevent cross-purpose collisions.

Enforced by `test_no_forbidden_imports_in_core` (scans the package for
`time`/`datetime`/`random`/`uuid`/`os.environ`/`perf_counter`/`urandom`).

## Replay boundary doctrine

`replay_boundary.py` fixes the boundary exactly.

**Inside replay scope** (`REPLAY_SCOPE`) — deterministic, hashed, part of identity:
`boot_id`, `session_id`, `collector_signature`, `signal_set_signature`,
`cognition_v2_runtime_hash`, `baseline_anchor`, `topology_attestation`,
`runtime_envelope_hash`, `contract_version`.

**Outside replay scope** (`NON_REPLAY_SCOPE`) — non-deterministic runtime reality,
never hashed: `wall_clock_time`, `process_id`, `thread_id`, `random_seed`,
`environment_variables`, `hostname`, `network_state`, `memory_address`,
`log_timestamp`, `external_api_response`, `telegram_delivery_receipt`,
`db_connection_state`.

`assert_replay_safe(payload)` raises `ReplayBoundaryViolation` if any
non-deterministic key attempts to enter the hashed payload. It runs inside
`attest()` before hashing — non-determinism cannot reach the envelope.

## Binding to the live constitution

`derive_default_components()` binds the four anchors to frozen repo state
(deterministic file reads only):
- `baseline_anchor` ← alembic baseline revision id (Sprint 69).
- `cognition_v2_runtime_hash` ← SHA-256 over all characterization snapshots
  (Sprint 70–72 frozen behavior).
- `collector_signature` ← SHA-256 of the signal-collector surface (`routers/events.py`).
- `signal_set_signature` ← SHA-256 of the signal-set record (`models/user_event.py`).

This is optional binding; the pure core and all constitutional tests are
independent of it.

## Frozen contracts (do not change without governance unlock)

1. `CONTRACT_VERSION = "runtime-envelope/1"`.
2. `ENVELOPE_FIELDS` — the seven fields and their meaning.
3. The four derivation formulas (boot, session, topology, envelope hash) and
   their domain tags.
4. `TOPOLOGY` order and the envelope's position (index 3).
5. The replay-scope / non-replay-scope partition.
6. Canonicalization rules.

Any change alters `runtime_envelope_hash` and breaks the pinned constitutional
tests in `tests/envelope/test_constitution.py`.
