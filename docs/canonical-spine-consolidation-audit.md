# Canonical Spine Consolidation Audit

**Date:** 2026-06-27
**Master HEAD at write time:** `154b5a9` (Merge PR #50)

Architectural record of the first consolidation step around the canonical Decision
Spine: which legacy/dead code was removed, which look-alike code was deliberately
kept, and which larger legacy↔canonical zones still need a product decision before
they can be touched.

---

## 1. What was verified

Two functions were manually verified (repo-wide grep, split test / non-test, plus
dynamic-import / `getattr` / reflection / string-constant / scheduler / CLI /
`__init__` re-export / alembic checks):

| Function | File | Non-test callers | Indirect refs | Verdict |
|---|---|---|---|---|
| `promote_alternatives_observed_ranked` | `backend/services/learning_os/promotion_ranking.py` | **0** | **0** | dead → removed |
| `promote_insight_alternatives` | `backend/services/insight_decision_bridge.py` | only the (now-removed) orphan above | **0** | **kept** — see §4 |

`learning_os/__init__.py` re-exported neither (file is an empty docstring), so the
removed function was never part of a public surface.

---

## 2. Removed in PR #50

- **Deleted file:** `backend/services/learning_os/promotion_ranking.py`
  (its only export was the dead `promote_alternatives_observed_ranked`).
- **Edited test:** `backend/tests/test_learning_os.py`
  - dropped the now-unused import;
  - removed the two tests that exercised the dead function only:
    - `test_promotion_alternatives_use_observed_order`
    - `test_promotion_alternatives_default_order_without_history`
  - shared helpers left untouched.

Result: full backend suite `2549 passed` (was `2551`, minus the 2 removed tests).
Alembic head unchanged (`tm1c1a2b3c4d02`). Merge commit `154b5a9`.

---

## 3. Kept

- `promote_insight_alternatives` — `backend/services/insight_decision_bridge.py:250`.

## 4. Why kept

`promote_insight_alternatives` is **not** dead cruft — it is a *designed-not-wired*
canonical Spine primitive:

1. It promotes ALL declared alternatives of an insight into separate Decisions —
   a canonical multi-decision capability, not a legacy duplicate.
2. It lives next to the live `promote_insight_to_decision` in the same active
   `insight_decision_bridge.py` module.
3. It has its own dedicated test suites
   (`test_multi_decision_promotion.py`, `test_ranked_promotion.py`,
   `test_learning_loop_audit.py`).
4. Its only non-test caller was the orphan removed in PR #50; that makes it
   currently test-only, but removing a designed-not-wired Spine primitive is a
   **product / architecture decision, not a mechanical cleanup**.

## 5. Current status

- **Cannot be removed without Phase 0** (the product decision on which surface
  survives — see §6).
- **Revisit after** the legacy ↔ canonical surface decision is made.

---

## 6. Open legacy / canonical zones (need a future decision)

Two parallel, both-live, both-frontend-facing worlds exist for the same margin
problem. Consolidating them is blocked on a **Phase 0** product decision:

| World | Vocabulary | Producer | Outcome store | API |
|---|---|---|---|---|
| **Legacy** | `margin_crisis` | `tasks/intelligence_loop.py` (scheduled) | `DecisionMemory` | `/insights`, `/learning/alternatives`, `/learning/evidence` |
| **Canonical** | `pricing_negative_margin` | `services/pricing/generator.py` | `EngineEffectObservation` + `EngineSignalDecisionLink` | `/decision-feed`, `/decision-outcome/*`, `/promotion-activation/run` |

These are not confirmed legacy-vs-canonical — they may be intentionally separate
surfaces (Telegram alerts vs in-app Decision Feed). Phase 0 must confirm intent
before any vocabulary / outcome-store / ranking consolidation.

---

## 7. Rule for future consolidation

1. **One component per PR.**
2. **grep / non-test verification first** (direct + dynamic import + `getattr` +
   string-const + scheduler + CLI + `__init__` re-export).
3. Remove **only** after zero non-test callers AND zero indirect refs are proven.
4. Run the **full backend suite**; it must stay green.
5. **Alembic head must not change** (no schema churn in a consolidation step).
6. If any doubt or a single non-test caller exists → mark **not ready** and stop.

---

## 8. Do not touch without a separate investigation

- canonical alternatives;
- Decision Spine (`Decision`, `EngineSignalDecisionLink`, `EngineEffectObservation`);
- Learning OS model;
- Effect model;
- `promote_insight_alternatives`.
