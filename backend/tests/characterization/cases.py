"""Runtime characterization fixtures + driver (Sprint 70).

`build_cases()` runs the FROZEN runtime functions against deterministic fixtures
and returns a JSON-serializable dict {case_name: output}. It is the single
source of:
  - the fixture catalog (the inputs below)
  - the live behavior (its return value)

The committed snapshot at snapshots/runtime.json is the reference behavior.
`tests/test_runtime_characterization.py` compares this driver's live output to
that snapshot. A behavior change makes them diverge -> test fails.

CONSTRAINT (Sprint 70): we only OBSERVE. Nothing here modifies runtime logic.

Determinism note: signal_lifecycle / signal_decay read datetime.utcnow()
internally. Fixtures pass dates as *relative offsets* (days_ago), so the derived
integer day-counts are stable across runs (sub-second drift truncates away).
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from typing import Any


def _now() -> datetime:
    return datetime.utcnow()


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def build_cases() -> dict[str, Any]:
    now = _now()

    def days_ago(n: int) -> datetime:
        return now - timedelta(days=n)

    cases: dict[str, Any] = {}

    def cap(name: str, value: Any) -> None:
        cases[name] = _jsonable(value)

    # ── signal_lifecycle.compute_signal_lifecycle ─────────────────────────────
    from logic.signal_lifecycle import compute_signal_lifecycle
    LC = dict(insight_key="margin_crisis:WB:SKU1", rule_category="margin_crisis")
    cap("lifecycle.emerging", compute_signal_lifecycle(
        **LC, first_seen=days_ago(0), resolved_at=None, notif_count=0,
        outcome_state=None, confidence_band=None))
    cap("lifecycle.confirmed_by_notif", compute_signal_lifecycle(
        **LC, first_seen=days_ago(10), resolved_at=None, notif_count=2,
        outcome_state=None, confidence_band=None))
    cap("lifecycle.confirmed_by_age", compute_signal_lifecycle(
        **LC, first_seen=days_ago(10), resolved_at=None, notif_count=0,
        outcome_state=None, confidence_band="high"))
    cap("lifecycle.resolved", compute_signal_lifecycle(
        **LC, first_seen=days_ago(20), resolved_at=days_ago(3), notif_count=1,
        outcome_state="stabilized", confidence_band=None))
    cap("lifecycle.recurring", compute_signal_lifecycle(
        **LC, first_seen=days_ago(40), resolved_at=days_ago(10), notif_count=3,
        outcome_state="repeated", confidence_band=None))
    cap("lifecycle.stabilized", compute_signal_lifecycle(
        insight_key="seo_opportunity:WB:S", rule_category="margin_crisis",
        first_seen=days_ago(30), resolved_at=days_ago(10), notif_count=1,
        outcome_state="improved", confidence_band=None))

    # ── signal_decay.compute_signal_decay ─────────────────────────────────────
    from logic.signal_decay import compute_signal_decay
    cap("decay.fresh", compute_signal_decay(
        insight_type="seo_opportunity", lifecycle_stage="emerging",
        first_detected=days_ago(2), last_confirmed=None, recurrence_count=0,
        confidence_band=None))
    cap("decay.aging", compute_signal_decay(
        insight_type="seo_opportunity", lifecycle_stage="confirmed",
        first_detected=days_ago(14), last_confirmed=None, recurrence_count=0,
        confidence_band=None))
    cap("decay.fading", compute_signal_decay(
        insight_type="seo_opportunity", lifecycle_stage="confirmed",
        first_detected=days_ago(30), last_confirmed=None, recurrence_count=0,
        confidence_band=None))
    cap("decay.stale", compute_signal_decay(
        insight_type="seo_opportunity", lifecycle_stage="confirmed",
        first_detected=days_ago(60), last_confirmed=None, recurrence_count=0,
        confidence_band=None))
    cap("decay.persistent_recurring", compute_signal_decay(
        insight_type="seo_opportunity", lifecycle_stage="recurring",
        first_detected=days_ago(60), last_confirmed=None, recurrence_count=2,
        confidence_band=None))
    cap("decay.persistent_structural", compute_signal_decay(
        insight_type="margin_crisis", lifecycle_stage="confirmed",
        first_detected=days_ago(60), last_confirmed=None, recurrence_count=3,
        confidence_band=None))

    # ── observability_recovery.compute_observability_recovery ─────────────────
    from logic.observability_recovery import compute_observability_recovery

    def ins(**kw):
        base = dict(key="seo_opportunity:WB:S", recovery_signal_state=None,
                    lock_estimated_recovery_window_days=None, trajectory_state=None,
                    counterfactual_pressure_state=None, signal_decay_state=None)
        base.update(kw)
        return NS(**base)

    cap("obs.clear_none", compute_observability_recovery(ins(), 0))
    cap("obs.clear_ready", compute_observability_recovery(ins(recovery_signal_state="ready"), 0))
    cap("obs.distorted", compute_observability_recovery(
        ins(recovery_signal_state="waiting", lock_estimated_recovery_window_days=5), 0))
    cap("obs.fragmented", compute_observability_recovery(
        ins(recovery_signal_state="waiting", lock_estimated_recovery_window_days=5), 3))
    cap("obs.reset_structural", compute_observability_recovery(
        ins(recovery_signal_state="waiting", trajectory_state="structurally_accumulating",
            lock_estimated_recovery_window_days=10), 0))
    cap("obs.recovering_stabilizing", compute_observability_recovery(
        ins(recovery_signal_state="stabilizing", lock_estimated_recovery_window_days=5), 0))
    cap("obs.distorted_escalating", compute_observability_recovery(
        ins(recovery_signal_state="stabilizing", trajectory_state="escalating",
            lock_estimated_recovery_window_days=20), 0))
    cap("obs.recovering_reopening", compute_observability_recovery(
        ins(recovery_signal_state="reopening"), 0))

    # ── operational_doctrine.compute_operational_doctrine ─────────────────────
    from logic.operational_doctrine import compute_operational_doctrine

    def di(**kw):
        base = dict(status="active", signal_lifecycle_stage=None, strategic_drift_state=None,
                    obs_recovery_state=None, outcome_state=None,
                    counterfactual_pressure_state=None, reversibility_state=None,
                    adaptive_capacity_state=None, trajectory_state=None, resilience_state=None,
                    timing_state=None, stabilization_lock_state=None, reversal_state=None,
                    cascade_state=None, historical_cycles=None, category=None,
                    key="margin_crisis:WB:S")
        base.update(kw)
        return NS(**base)

    cap("doctrine.empty", compute_operational_doctrine([]))
    cap("doctrine.recurring_bias", compute_operational_doctrine([
        di(signal_lifecycle_stage="recurring", key="margin_crisis:WB:A"),
        di(signal_lifecycle_stage="recurring", key="high_ad_spend:WB:B"),
    ]))
    cap("doctrine.stabilization_dependency", compute_operational_doctrine([
        di(signal_lifecycle_stage="recurring", stabilization_lock_state="waiting", key="margin_crisis:WB:A"),
        di(signal_lifecycle_stage="recurring", stabilization_lock_state="waiting", key="high_ad_spend:WB:B"),
    ]))

    # ── action_engine pure helpers ────────────────────────────────────────────
    from routers.action_engine import (
        _clevel, _fmt_rub, _fmt_k, _impact_score, _extract_category, _mp_label,
        _normalize_cat, _growth_maturity, _ad_degradation_context,
    )
    cap("ae._clevel", [_clevel(c) for c in (40, 55, 74, 75, 90)])
    cap("ae._fmt_rub", [_fmt_rub(x) for x in (0, 999, 1500, 1234567)])
    cap("ae._fmt_k", [_fmt_k(x) for x in (0, 500, 62000, 1500000)])
    cap("ae._impact_score", [_impact_score(c, m) for c, m in ((70, 0), (70, 200000), (90, 400000), (50, 100000))])
    cap("ae._extract_category", [_extract_category(k) for k in ("margin_crisis:WB:SKU", "seo_opportunity:OZ")])
    cap("ae._mp_label", [_mp_label(m) for m in ("wildberries", "ozon", "yandex_market", "unknown_mp")])
    cap("ae._normalize_cat", [_normalize_cat(k) for k in ("margin_crisis:WB:S", "demo_margin_crisis:WB", "high_ad_spend:OZ")])
    growth = {f"2026-05-{d:02d}": v for d, v in zip(range(1, 10), [100, 100, 100, 150, 150, 150, 220, 220, 220])}
    cap("ae._growth_maturity.mature", _growth_maturity(growth))
    cap("ae._growth_maturity.too_short", _growth_maturity({"2026-05-01": 100, "2026-05-02": 100}))
    addeg = {f"2026-05-{d:02d}": 1000 for d in range(1, 13)}
    cap("ae._ad_degradation.young", _ad_degradation_context(addeg, 5))
    cap("ae._ad_degradation.sustained", _ad_degradation_context(addeg, 12))

    # ── intelligence_loop pure formatters ──────────────────────────────────────
    from tasks.intelligence_loop import (
        _fmt_seo_opportunity, _fmt_sales_growth, _fmt_high_rating, _fmt_critical_alert,
        _fmt_digest, _fmt_retention, _memory_line, _behavior_line, _certainty_line,
        _lifecycle_line, _decay_note, _feedback_line, _outcome_line,
    )
    INS = dict(product_name="Кружка", marketplace="wildberries", confidence=82,
               impact_estimate="+30k ₽/мес", reasons=["Низкий CTR", "Слабые ключи"],
               title="Карточка снижает CTR", action_params={"product": "Кружка"},
               rule_type="margin_crisis", recommendations=["Снизить рекламу"])
    cap("il._fmt_seo_opportunity", _fmt_seo_opportunity(INS))
    cap("il._fmt_sales_growth", _fmt_sales_growth(INS))
    cap("il._fmt_high_rating", _fmt_high_rating(INS))
    cap("il._fmt_critical_alert", _fmt_critical_alert(INS))
    cap("il._fmt_digest", _fmt_digest("Кружка", "wildberries", [
        {"rule_type": "margin_crisis", "impact_estimate": "-10k"},
        {"rule_type": "seo_opportunity", "impact_estimate": ""},
    ]))
    cap("il._fmt_retention", _fmt_retention("Иван", 3))
    cap("il._memory_line", [_memory_line({}), _memory_line({"memory_context": "ранее было"})])
    cap("il._behavior_line", [
        _behavior_line({"confidence": 50, "marketplace_behavior_note": "x"}),
        _behavior_line({"confidence": 80, "marketplace_behavior_note": "Ozon лагает 48ч", "marketplace": "ozon"}),
    ])
    cap("il._certainty_line", [_certainty_line({"decision_confidence_band": b}) for b in ("high", "low", "medium")])
    cap("il._lifecycle_line", [_lifecycle_line({"signal_lifecycle_stage": s}) for s in ("recurring", "emerging", "confirmed")])
    cap("il._decay_note", [_decay_note({"signal_decay_state": s}) for s in ("stale", "fresh")])
    cap("il._feedback_line", [_feedback_line({"outcome_feedback_note": "n", "recommendation_confidence_delta": d}) for d in (10, -12, 0)])
    cap("il._outcome_line", [_outcome_line({"outcome_memory_note": "n", "outcome_confidence": 80, "outcome_state": s}) for s in ("repeated", "other")])

    return cases
