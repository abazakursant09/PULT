# Sprint 71 — Logic Layer Constitutional Freeze

**Status: FROZEN.** Every module under `backend/logic/` is now covered by golden
characterization tests. Observe-only: no logic was changed, renamed, optimized,
or fixed. Current behavior is the reference behavior.

## What was frozen

- **43** total logic modules.
- **4** already frozen in Sprint 70 (signal_lifecycle, signal_decay,
  observability_recovery, operational_doctrine) — see
  `runtime_characterization.md`.
- **39** newly frozen this sprint, one test package each under
  `backend/tests/characterization/<module>/` (`cases.py`,
  `test_characterization.py`, `snapshot.json`).
- **103** frozen cases total across the 39 new modules.

## How it works

Shared engine: `backend/tests/characterization/_engine.py`.
Each `cases.py` calls the real logic function against deterministic fixtures and
returns `{case_name: output}`. The committed `snapshot.json` is the reference.
The test recomputes live and asserts equality — divergence fails the test.

Intentional change → regenerate explicitly and review the diff:
```bash
cd backend && CHAR_UPDATE=1 python -m pytest tests/characterization/<module>
```

### Determinism handling (no logic change)
The harness normalizes two sources of run-to-run noise so snapshots are stable:
- **Volatile ids** (`id`, `scenario_id`, `focus_id` — uuid/per-call) → `"<volatile>"`.
- **Set-derived list order** (`affected_products`, `insight_types`,
  `marketplaces`, `linked_signals`, `linked_scenarios`, `linked_chains`) → sorted.

Five modules needed hand-built fixtures (real typed inputs rather than the
generic neutral path): `cross_mp_memory`, `decision_weight`, `operator_profile`,
`portfolio_patterns`, `focus_engine`.

## Bugs discovered (frozen, NOT fixed — per Sprint 71 rule)

**Finding #1 — `focus_engine.compute_operational_focus` order-dependent `root_cause`.**
With a multi-signal chain (e.g. `margin_crisis` + `high_ad_spend` on the same
product), the returned `root_cause` flips between the two categories depending on
dict/set iteration order (hash seed). Observable, non-deterministic output. Left
unchanged. The frozen fixture uses a single dominant insight to capture
deterministic behavior; the chain-tie path remains un-frozen pending a decision
to fix (out of scope for an observe-only sprint).

No other non-deterministic or crashing behavior was observed across the 39
modules on their characterized input paths.

## Coverage

`backend/logic/` line coverage (pytest --cov=logic):
- **Before Sprint 71: 31%** (4 modules).
- **After Sprint 71: 73%** (43 modules).

Coverage is breadth-first: every module has ≥1 frozen behavior path. Depth
(branch coverage of severe/rare states) is partial for the larger
classifier modules and is the natural next increment — it requires richer
fixtures, not code changes.

## Guarantee

Any future modification of a frozen logic path that changes its output makes the
corresponding `test_characterization` fail. Proven by tampering a snapshot value
(fails) and restoring it (passes). The logic layer is harder to accidentally
change after Sprint 71 than before.
