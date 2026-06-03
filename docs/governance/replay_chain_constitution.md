# Replay Chain Constitution (Sprint 74)

**Status: FROZEN.** The Replay Chain is a **read-only constitutional verifier**
that reconstructs the full runtime chain from an event log and a schema baseline,
and seals it into one deterministic `replay_chain_hash`.

Built this sprint. No existing layer modified — `backend/logic/*`,
`runtime_envelope/*`, collector semantics, and cognition logic are untouched
(observe-only mandate preserved).

## Replay chain definition

```
event_log + baseline_anchor
   -> signal_set            (deterministic structural aggregation of events)
   -> cognition topology    (deterministic structural projection of signal_set)
   -> runtime envelope      (frozen Runtime Envelope, Sprint 73)
   -> replay_chain_hash
```

Stage hashes, each domain-separated SHA-256 over canonical bytes:
- `event_log_hash` = `PULT-REPLAY-EVENTLOG/1`(ordered event log)
- `signal_set_signature` = `PULT-REPLAY-SIGNALSET/1`(signal_set)
- `cognition_hash` = `PULT-REPLAY-COGNITION/1`(cognition_topology)
- `envelope_hash` = Runtime Envelope hash, with
  collector_signature=`event_log_hash`, signal_set_signature, cognition_v2_runtime_hash=`cognition_hash`, baseline_anchor
- `replay_chain_hash` = `PULT-REPLAY-CHAIN/1`({event_log_hash, signal_set_signature, cognition_hash, envelope_hash, baseline_anchor})

**This is a verifier, not a re-implementation.** It does not run logic/cognition
modules or touch the database. `signal_set` and `cognition_topology` are
deterministic structural projections of the event log used solely to bind the
chain — never to generate recommendations or predictions.

## Replay boundary

Inherited from the Runtime Envelope (Sprint 73). `reconstruct_signal_set` calls
`assert_replay_safe` on every event, so no non-deterministic field
(time/pid/random/env/network/uuid) can enter any chain hash. Event logs carry
only fixed structural fields: `event_type`, `entity`, `weight`, `marketplace`.

## Constitutional guarantees

- **Determinism**: identical (event_log, baseline_anchor) → identical
  `replay_chain_hash`. Proven across 10 independent interpreter runs (single
  hash) and cross-process.
- **No clocks / randomness / uuids / env / network / external services / AI**:
  enforced by `test_no_forbidden_constructs` scanning the verifier package.
- **Tamper-evidence**: `verify_attestation` recomputes the entire chain from the
  attested event log; tampering the event log, signal-set signature, cognition
  hash, envelope hash, or chain hash all fail verification.
- **Canonicalization**: JSON sorted-keys, compact separators, UTF-8; dict order
  irrelevant; event-log order significant (the log is ordered).

## Canonical fixtures (frozen)

Six regimes under `tests/replay_chain_fixtures/<name>.json`, each freezing event
log, signal_set, cognition_topology, and all five stage hashes:
`steady_state`, `cascading_failure`, `drift_storm`, `intervention_collapse`,
`propagation_fracture`, `replay_instability_burst`. All six produce distinct
chain hashes.

## Frozen contracts (no change without governance unlock)

1. The 5-stage chain order and its domain tags.
2. `reconstruct_signal_set` / `reconstruct_cognition_topology` field sets.
3. The envelope-anchor mapping (collector←event_log_hash, cognition←cognition_hash).
4. The six canonical fixtures and their pinned hashes.
5. The replay boundary (inherited, Sprint 73).

Any change alters fixture hashes and breaks
`tests/replay_chain/test_replay_chain.py`.
