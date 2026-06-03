# Operational Runtime Application Layer v1 — Constitution

Post-closure directive. The FIRST operational application layer ABOVE the frozen
substrate. Strictly descriptive, append-only, fail-closed, deterministic,
replay-reconstructable. Built this sprint; no substrate modified.

## Scope honesty (recorded)

The directive referenced upstream layers — `semantic_runtime`, `runtime_observatory`,
`governance_runtime`, `operational_substrate_closure` — and six "canonical anchor
hashes". **Those layers do not exist in this repository.** The real frozen
substrate is Sprints 69–74 (`alembic` baseline, characterization snapshots,
`runtime_envelope`, `replay_chain`). This layer is built on the REAL substrate;
the nonexistent anchors were NOT fabricated. The second directive (frontend
`operator_console`) is deferred — it depends on this layer plus those nonexistent
upstream layers.

## Position

```
frozen substrate (read-only): alembic | characterization | runtime_envelope | replay_chain
        ▲  (read-only, no upstream calls into it from below)
backend/runtime_application/   <-- this layer (descriptive, above substrate)
```

## Authority boundary (frozen)

`runtime_application_boundary.py`:
- `EXECUTION_AUTHORITY = False`
- `MUTATION_AUTHORITY = False`
- `DESCRIPTIVE_ONLY = True`
- `FAIL_CLOSED = True`, `REPLAY_COMPATIBLE = True`, `DETERMINISTIC = True`

The layer observes and renders; it never executes, schedules, corrects, or alters
anything. Upstream substrate is read-only.

## Modules

| Module | Role |
|---|---|
| `live_event_ingestion` | normalize events → canonical append-only stream; ordinal = append index; fail-closed |
| `operational_state_projection` | structural state (counts, category weight) |
| `pressure_accumulation_runtime` | accumulation regions + dissipation surfaces |
| `intervention_surface_runtime` | operator-visible regions, observation-only |
| `drift_visualization_runtime` | drift regions + instability markers |
| `replay_runtime_window` | ordinal-based replay windows + timeline |
| `runtime_console_projection` | aggregated operator view |
| `runtime_application_attestation` | attest/verify, fail-closed |
| `runtime_application_topology` | aggregator → `runtime_application_hash` |
| `runtime_application_boundary` | authority flags |

## Guarantees (enforced by tests)

- **Deterministic**: identical events → identical `runtime_application_hash`
  (10 independent interpreter runs single hash; cross-process identity).
- **Append-only**: ordinal = append index; every prefix is stable under growth;
  `append()` preserves the prior prefix.
- **Fail-closed**: malformed events, missing/extra fields, bad weight type,
  non-dict, or any non-deterministic key (`replay_boundary`) → refused.
- **Descriptive-only**: boundary flags False; `RuntimeApplication` and
  `RuntimeStream` are frozen/immutable.
- **Governance separation / substrate invariance**: building the application does
  not change the Runtime Envelope golden hash or any replay-chain fixture hash.
- **No forbidden vocabulary**: scan rejects intelligent/autonomous/agentic/
  predictive/optimizer/optimize/learning/adaptive-execution/rebalance/mutation-
  semantics/runtime-mutation/substrate-extension/etc.
- **No forbidden imports**: scan rejects threading/asyncio/multiprocessing/socket/
  requests/subprocess/inspect/importlib/pkgutil.

## Hashing

`runtime_application_hash` = domain-separated SHA-256 (`PULT-RUNTIME-APP/1`) over
{state, pressure, interventions, drift, replay}, reusing the frozen Runtime
Envelope canonicalization (no new hash algorithm).

## Frozen contracts

1. Authority flags (all False except descriptive/fail-closed/replay/deterministic).
2. `ALLOWED_FIELDS` event schema (`event_type, entity, weight, marketplace`).
3. The `PULT-RUNTIME-APP/1` hash domain + aggregated payload shape.
4. The six golden fixture hashes under `tests/runtime_application_fixtures/`.
