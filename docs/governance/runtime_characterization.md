# Runtime Characterization (PULT — Sprint 70)

**Purpose:** freeze current runtime behavior so it cannot silently change.
Current behavior is the reference behavior — even if imperfect. These tests
*observe and protect*; they do not change, optimize, or fix runtime logic.

- Suite: `backend/tests/test_runtime_characterization.py` (golden master),
  `backend/tests/test_runtime_contract.py` (orchestration contracts).
- Fixture driver / catalog: `backend/tests/characterization/cases.py`.
- Reference snapshot: `backend/tests/characterization/snapshots/runtime.json`
  (47 cases, UTF-8).

## How it works

`cases.build_cases()` runs the frozen functions against deterministic fixtures
and returns `{case_name: output}`. The test recomputes live and asserts equality
against the committed snapshot, per case. Divergence → failure.

**Intentional** behavior change → regenerate the snapshot explicitly and review
the diff:

```bash
cd backend
CHAR_UPDATE=1 python -m pytest tests/test_runtime_characterization.py
```

Without `CHAR_UPDATE=1` the snapshot is read-only. Proven: tampering one
reference value makes the matching case fail; restoring it passes.

## Determinism

`signal_lifecycle` and `signal_decay` call `datetime.utcnow()` internally.
Fixtures pass dates as **relative offsets** (`days_ago(n)`), so derived integer
day-counts are stable across runs. All other frozen functions are pure (no time,
no I/O, no randomness).

---

## Behavior map

| Entry point | Inputs | Outputs | Side effects | Protection |
|---|---|---|---|---|
| `logic.signal_lifecycle.compute_signal_lifecycle` | kw: insight_key, rule_category, first_seen, resolved_at, notif_count, outcome_state, confidence_band | `SignalLifecycle` (stage, weights, notes, day-counts) | none (pure) | **Golden master — 100% line cov.** 6 cases (all 5 stages) |
| `logic.signal_decay.compute_signal_decay` | kw: insight_type, lifecycle_stage, first_detected, last_confirmed, recurrence_count, confidence_band | `SignalDecay` (state, age, factor, penalty, note) | none (pure) | **Golden master — 100% line cov.** 6 cases (all 5 states) |
| `logic.observability_recovery.compute_observability_recovery` | insight (duck-typed), concurrent_active:int | `ObservabilityRecovery` (state, window, condition, blocking, note) | none (pure) | **Golden master — 82% line cov.** 8 cases |
| `logic.operational_doctrine.compute_operational_doctrine` | insights:list, regime, phase, topology_state, energy_state | `OperationalDoctrine` (state, pattern, mode, level, flexibility, note, confidence) | none (pure) | **Golden master — 88% line cov.** 3 cases |
| `routers.action_engine` pure helpers (`_clevel`, `_fmt_rub`, `_fmt_k`, `_impact_score`, `_extract_category`, `_mp_label`, `_normalize_cat`, `_growth_maturity`, `_ad_degradation_context`) | scalars / daily dicts | scalars / tuples | none (pure) | **Golden master** — 11 cases |
| `tasks.intelligence_loop` pure formatters (`_fmt_seo_opportunity`, `_fmt_sales_growth`, `_fmt_high_rating`, `_fmt_critical_alert`, `_fmt_digest`, `_fmt_retention`, `_memory_line`, `_behavior_line`, `_certainty_line`, `_lifecycle_line`, `_decay_note`, `_feedback_line`, `_outcome_line`) | insight dict / lists | `(text, keyboard)` or footnote str | none (pure) | **Golden master** — 13 cases (Telegram message text frozen) |
| `routers.action_engine._compute_insights` | uid, db, statuses, resolved_history, notif_counts, rebuild_outcomes | `list[InsightItem]` | **reads DB**, `utcnow` | **Contract only** — signature + coroutine frozen. Output NOT golden-mastered |
| `tasks.intelligence_loop._process_user` | user, tg_settings, db | int (messages sent) | **reads DB, writes notification log, sends Telegram** | **Contract only** — signature + coroutine |
| `tasks.intelligence_loop.run_intelligence_loop` | — (infinite loop) | none | **DB + Telegram + sleep loop** | **Contract only** — coroutine asserted |

---

## Fixture catalog (47 cases)

- **lifecycle.\*** (6): emerging, confirmed_by_notif, confirmed_by_age, resolved, recurring, stabilized
- **decay.\*** (6): fresh, aging, fading, stale, persistent_recurring, persistent_structural
- **obs.\*** (8): clear_none, clear_ready, distorted, fragmented, reset_structural, recovering_stabilizing, distorted_escalating, recovering_reopening
- **doctrine.\*** (3): empty, recurring_bias, stabilization_dependency
- **ae.\*** (11): _clevel, _fmt_rub, _fmt_k, _impact_score, _extract_category, _mp_label, _normalize_cat, _growth_maturity.{mature,too_short}, _ad_degradation.{young,sustained}
- **il.\*** (13): the four full alert formatters, digest, retention, and seven footnote-line builders

---

## Coverage report (frozen logic modules)

```
logic/signal_lifecycle.py        31 stmts    0 miss   100%
logic/signal_decay.py            30 stmts    0 miss   100%
logic/operational_doctrine.py    85 stmts   10 miss    88%
logic/observability_recovery.py  72 stmts   13 miss    82%
TOTAL                           218 stmts   23 miss    89%
```

`action_engine` and `intelligence_loop` are characterized at the function level
(the pure helpers/formatters), not module level — their module-wide coverage %
is low and meaningless because the DB-bound orchestration bodies are
deliberately out of scope (see Risk Map).

---

## Risk map — what is NOT protected

| Area | Status | Why | Residual risk |
|---|---|---|---|
| `_compute_insights` orchestration body (~1000 LOC, ranking/enrichment pipeline) | ❌ behavior unprotected | DB + `utcnow`; can't golden-master without code changes (forbidden this sprint) | A regression in ranking/enrichment ordering passes CI silently. **Highest residual risk.** |
| `_process_user` dispatch logic (gating, cooldowns, daily caps) | ❌ behavior unprotected | DB writes + Telegram send | Anti-spam / cap regressions undetected |
| The other ~38 `logic/` modules (cascade, resilience, regime, phase, energy, trajectory, etc.) | ❌ unprotected | not in Sprint 70 scope | Pure & golden-master-able later; currently free to drift |
| `obs.*` lines 28-36 (`_window_label`), 119/137/140/190 | ⚠️ partial | branches not hit by current fixtures | Minor — display banding |
| `doctrine.*` higher-severity states (rigid / structurally_embedded / defensive) | ⚠️ partial | fixtures cover default/bias/dependency only | Severe-state classification can drift undetected |

### Recommended follow-ups (next sprints, not done here)
1. Extend `doctrine.*` and `obs.*` fixtures to cover remaining branches → push both to ~100%.
2. Golden-master the remaining pure `logic/` modules (highest value, lowest effort).
3. For `_compute_insights`: introduce an injectable clock + a seeded in-memory DB fixture so its *output* (not just signature) can be frozen — requires a small, reviewed seam in the code (out of Sprint 70's observe-only mandate).
