# Operator Console Constitution (Product Layer)

Read-only operator console over **Runtime Application v1** — the real substrate.
Built this sprint. Pure product/presentation layer: it renders, it does not act.

## Scope honesty (recorded)

The original directive referenced `semantic_runtime`, `governance_runtime`,
`observatory_runtime` and six "canonical anchor hashes". **Those layers do not
exist in this repository and were not created or fabricated.** The console
consumes only the real `backend/runtime_application/runtime_application_topology`.

## Position

```
backend/runtime_application  (Runtime Application v1, descriptive)
        ▲  read-only
frontend/operator_console     <-- this product layer (FastAPI + SVG)
```

## Modules (frontend/operator_console/)

| Module | Role |
|---|---|
| `runtime_console_state` | builds ConsoleState from a fixed, append-only event log (read-only) |
| `runtime_console_topology` | structured console topology + `operator_console_hash` + SVG primitives |
| `runtime_region_renderer` | deterministic SVG bars of accumulation regions |
| `pressure_visualization_renderer` | accumulation regions + dissipation surfaces |
| `drift_visualization_renderer` | drift regions + instability markers |
| `intervention_surface_renderer` | operator-visible surfaces (observation only) |
| `replay_timeline_renderer` | ordinal timeline + replay windows |
| `runtime_dashboard_renderer` | composite HTML+SVG dashboard w/ replay identity |
| `runtime_console_routes` | read-only GET routes |
| `runtime_console_server` | FastAPI app (no background workers, no push channels) |

## Routes (all GET, read-only, deterministic)

```
GET /runtime/topology      -> JSON (console topology + operator_console_hash)
GET /runtime/pressure      -> SVG
GET /runtime/drift         -> SVG
GET /runtime/interventions -> SVG
GET /runtime/replay        -> SVG
GET /runtime/regions       -> SVG
GET /runtime/dashboard     -> HTML (composite)
GET /health                -> JSON
```

The operator can see: runtime state, pressure accumulation, intervention
surfaces, drift surface, replay identity, and `runtime_application_hash`.

## Guarantees (enforced by 218 tests)

- **Read-only**: only GET routes exist; no POST/PUT/DELETE/PATCH (verified by
  route-method scan). No mutation paths, no execution authority.
- **Deterministic**: every renderer and route is byte-identical on repeat;
  `operator_console_hash` identical across 10 independent interpreter runs and
  cross-process. SVG uses integer coordinates only — no random layout.
- **Replay-aware**: dashboard/replay views surface the `runtime_application_hash`
  and ordinal replay windows; route output equals direct render.
- **Fail-closed**: a malformed/forbidden event log refused at state load
  (inherited `BoundaryViolation`).
- **Governance separation / substrate invariance**: rendering never changes the
  Runtime Envelope golden hash, any replay-chain fixture hash, or any
  `runtime_application_hash`.
- **No forbidden imports** (threading/asyncio/multiprocessing/socket/subprocess/
  requests/websocket) and **no forbidden vocabulary** in the console package.

## Frozen contracts

1. Read-only GET route set + media types.
2. `PULT-OPERATOR-CONSOLE/1` hash domain + topology payload shape.
3. Authority flags: descriptive_only=True, execution_authority=False,
   mutation_authority=False.
4. Deterministic integer-coordinate SVG (no random layout).
