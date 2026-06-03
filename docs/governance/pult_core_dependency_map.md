# PULT Core Dependency Map — Post Sprint 74

Analysis-only (Sprint 75). No code/logic/schema changed. Evidence drawn from the
working tree as of the audit.

## Two planes

PULT today is **two disconnected planes** that share anchors but not runtime calls.

```
┌─ A. LIVE RUNTIME PLANE (FastAPI monolith) ───────────────────────────────┐
│  upstream events → routers/events.py (UserEvent ingest, fire-and-forget)  │
│      → DB (SQLite dev / Postgres prod)                                    │
│      → tasks/intelligence_loop.py (30-min loop)                           │
│          → routers/action_engine._compute_insights  (Cognition V2)        │
│              → backend/logic/* (44 modules)                               │
│      → Telegram dispatch + frontend operator console (Next.js)            │
└───────────────────────────────────────────────────────────────────────────┘
                              ╳  NO RUNTIME CALL  ╳
┌─ B. CONSTITUTIONAL VERIFICATION PLANE (frozen, standalone) ──────────────┐
│  alembic baseline (S69)                                                   │
│  characterization snapshots: logic + branch (S70–72)                      │
│  runtime_envelope/  → runtime_envelope_hash (S73)                         │
│  replay_chain/      → replay_chain_hash + 6 fixtures (S74)                │
└───────────────────────────────────────────────────────────────────────────┘
```

**Evidence of disconnection:** `grep -rn "runtime_envelope\|replay_chain"
backend --include=*.py` outside `runtime_envelope/`, `replay_chain/`, `tests/`
returns **nothing**. The live app (`main.py`, routers, tasks, logic) never
imports, invokes, emits, or verifies any constitutional hash at runtime. Binding
is by **repo anchor** (`derive_default_components()` reads committed files), not
by live execution.

## Frozen subsystems (Plane B)

| Subsystem | Artifact | Freeze mechanism | Coverage (measured) |
|---|---|---|---|
| Schema | `alembic/versions/47beea1df0c1_baseline_full_schema.py` | `test_schema_drift` (model==head) | baseline = 36 tables |
| Logic behavior | `tests/characterization/*/snapshot.json` | golden snapshots | logic 81% line, 5 targets 97% branch |
| Runtime identity | `runtime_envelope/` | `runtime_envelope_hash` + 12 constitutional tests | 89% |
| End-to-end replay | `replay_chain/` | `replay_chain_hash` + 6 fixtures + tamper attestation | 99% |

## Dependencies (Plane B internal)

```
replay_chain → runtime_envelope (build_runtime_envelope, replay_boundary, envelope_hash)
runtime_envelope → (stdlib only: hashlib, json, dataclasses)
characterization snapshots → backend/logic/* + routers/action_engine + tasks/intelligence_loop (read-only, via tests)
alembic baseline → backend/models/* (via env.py model registry)
derive_default_components → alembic versions + characterization snapshots + routers/events.py + models/user_event.py  (deterministic file reads)
```

Plane B depends on Plane A artifacts **statically** (file content as anchors).
Plane A depends on Plane B **not at all**.

## Runtime boundary

- **Inside replay scope** (deterministic, hashed): boot/session/collector/
  signal_set/cognition/baseline/topology signatures → envelope → replay chain.
- **Outside replay scope** (never hashed): wall clock, pid, random, env, network,
  Telegram delivery, DB connection state, external API responses. Enforced by
  `replay_boundary.assert_replay_safe`.
- **Live runtime reality** (Plane A) operates entirely outside any replay scope —
  it has no attestation surface.

## Governance boundary

- **Codified & enforced-by-test (if run):** schema drift, logic behavior, runtime
  identity, replay chain. Governance docs in `docs/governance/`.
- **Codified, NOT enforced:** all of the above run **only on manual `pytest`** —
  there is no CI (`.github` absent), no pre-merge gate, no deploy-time check.
- **Not codified:** runtime emission/verification of hashes, operator-facing
  attestation, incident/rollback execution.
