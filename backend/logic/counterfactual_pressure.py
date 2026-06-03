"""
Counterfactual Pressure Intelligence — Sprint 39.

Models the cost of inaction: how operational flexibility narrows over time.
NOT urgency engine. NOT pressure tactics.
Timing intelligence: what happens if current dynamics persist unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class CounterfactualPressure:
    pressure_state:                   str            # stable | narrowing | accelerating | structurally_locked
    estimated_transition_window_days: Optional[int]  # typical phase-transition horizon; None = high uncertainty
    reversibility_remaining_pct:      Optional[int]  # approximate operational flexibility remaining
    likely_next_phase:                Optional[str]  # what typically follows if dynamics persist
    inaction_cost_note:               Optional[str]  # one-sentence restrained inaction consequence
    operational_time_pressure:        Optional[str]  # low | moderate | elevated | critical
    counterfactual_note:              Optional[str]  # restrained narrative


# ── Transition horizon by category (midpoint days) ───────────────────────────
# These are typical phase-transition windows — NOT deadlines.
_TRANSITION_BASE: dict[str, int] = {
    "seo_opportunity":        12,  # 10–14 days
    "high_ad_spend":          18,  # 14–21 days
    "margin_crisis":          21,  # 14–28 days
    "low_stock":               7,  # 3–10 days
    "sales_growth":           14,  # 10–21 days
    "price_pressure_cluster": 35,  # 21–45 days
}

# ── Likely next phase per category ───────────────────────────────────────────
_NEXT_PHASE: dict[str, str] = {
    "seo_opportunity":        "Нестабильность органического трафика",
    "high_ad_spend":          "Снижение эффективности рекламных расходов",
    "margin_crisis":          "Структурное сжатие unit-экономики",
    "low_stock":              "Операционная нестабильность поставок",
    "sales_growth":           "Потеря устойчивости роста",
    "price_pressure_cluster": "Структурная ценовая нестабильность",
}

# ── Counterfactual notes by pressure state ────────────────────────────────────
_CF_NOTES: dict[str, str] = {
    "stable": (
        "Система пока сохраняет высокую операционную гибкость."
    ),
    "narrowing": (
        "Окно стабилизации постепенно сужается по мере накопления давления."
    ),
    "accelerating": (
        "При сохранении текущей динамики сигнал обычно переходит "
        "в следующую фазу в течение ближайших недель."
    ),
    "structurally_locked": (
        "Часть операционной гибкости уже утрачена, поэтому стабилизация "
        "может требовать последовательных изменений."
    ),
}

# ── Time pressure by state ────────────────────────────────────────────────────
_TIME_PRESSURE: dict[str, str] = {
    "stable":             "low",
    "narrowing":          "moderate",
    "accelerating":       "elevated",
    "structurally_locked": "critical",
}

# ── Focus weight deltas (applied only for recurring/persistent, not stale/fading) ──
COUNTERFACTUAL_WEIGHT_DELTA: dict[str, float] = {
    "stable":             0.0,
    "narrowing":          4.0,
    "accelerating":       9.0,
    "structurally_locked": 14.0,
}


def _cat(key: str) -> str:
    return key.split(":")[0]


def compute_counterfactual_pressure(
    insight:            Any,
    lifecycle:          Optional[str],
    trajectory_state:   Optional[str],
    reversibility:      Optional[str],
    decay:              Optional[str],
    recovery_state:     Optional[str],
    forecast_fragility: Optional[str],
    portfolio_patterns: list,
) -> CounterfactualPressure:
    """
    Compute counterfactual pressure for a single insight.
    Uses trajectory, reversibility, recovery, forecast, and portfolio context.
    NEVER modifies confidence scores.
    """
    cat = _cat(getattr(insight, "key", ""))
    recurrence    = getattr(insight, "signal_recurrence_count", 0) or 0
    outcome_state = getattr(insight, "outcome_state", None)
    age_days      = getattr(insight, "signal_age_days", 0) or 0

    systemic = any(
        getattr(p, "stabilization_complexity", "") == "systemic"
        for p in portfolio_patterns
    )
    systemic_count = sum(
        1 for p in portfolio_patterns
        if getattr(p, "stabilization_complexity", "") == "systemic"
    )

    # ── Pressure state ────────────────────────────────────────────────────────
    if (
        reversibility == "structurally_locked"
        or (recovery_state == "structural" and outcome_state in ("repeated", "failed"))
        or (systemic_count >= 2 and lifecycle == "recurring" and recurrence >= 3)
    ):
        state = "structurally_locked"

    elif (
        trajectory_state in ("escalating", "structurally_accumulating")
        or (lifecycle == "recurring" and recovery_state == "structural")
        or (systemic and forecast_fragility in ("fragile", "critical"))
        or (recurrence >= 3 and trajectory_state == "persistent")
    ):
        state = "accelerating"

    elif (
        lifecycle in ("recurring", "confirmed")
        and trajectory_state in ("persistent", "escalating")
    ) or (
        lifecycle == "recurring"
        and decay in ("fresh", "aging", "persistent")
    ) or (
        forecast_fragility in ("fragile",)
        and lifecycle != "stabilized"
    ):
        state = "narrowing"

    elif (
        trajectory_state in ("reversible", "stabilizing")
        or decay in ("fading", "stale")
        or outcome_state in ("improved", "stabilized")
    ):
        state = "stable"

    else:
        state = "narrowing"

    # ── Reversibility remaining pct ───────────────────────────────────────────
    rev_pct = 90

    if trajectory_state == "structurally_accumulating":
        rev_pct -= 35
    elif trajectory_state == "escalating":
        rev_pct -= 25
    elif trajectory_state == "persistent":
        rev_pct -= 15
    elif trajectory_state in ("reversible", "stabilizing"):
        rev_pct += 5

    if reversibility == "structurally_locked":
        rev_pct -= 40
    elif reversibility == "narrowing_window":
        rev_pct -= 15
    elif reversibility == "easily_reversible":
        rev_pct += 5

    if recovery_state == "structural":
        rev_pct -= 20
    elif recovery_state == "unstable":
        rev_pct -= 10
    elif recovery_state == "quick":
        rev_pct += 8

    if lifecycle == "recurring":
        rev_pct -= 12
    if recurrence >= 3:
        rev_pct -= 10
    if systemic:
        rev_pct -= 10

    if outcome_state == "repeated":
        rev_pct -= 15
    elif outcome_state in ("improved", "stabilized"):
        rev_pct += 15

    if decay in ("fading", "stale"):
        rev_pct += 10

    rev_pct = max(5, min(95, rev_pct))

    # ── Transition window ─────────────────────────────────────────────────────
    base_window = _TRANSITION_BASE.get(cat)
    if base_window is None or state == "stable":
        window_days = None
    elif state == "structurally_locked":
        window_days = None  # high uncertainty
    elif state == "accelerating":
        window_days = max(5, int(base_window * 0.8))  # compress 20%
    else:
        window_days = base_window

    # ── Next phase ────────────────────────────────────────────────────────────
    next_phase = _NEXT_PHASE.get(cat) if state in ("narrowing", "accelerating", "structurally_locked") else None

    # ── Inaction cost note ────────────────────────────────────────────────────
    if state == "stable":
        inaction_note = None
    elif state == "narrowing":
        inaction_note = "Каждый цикл без изменений постепенно уменьшает доступное операционное окно."
    elif state == "accelerating":
        inaction_note = "Часть операционной гибкости постепенно снижается по мере накопления давления."
    else:
        inaction_note = "Устойчивое давление ограничивает диапазон эффективных вмешательств."

    return CounterfactualPressure(
        pressure_state=state,
        estimated_transition_window_days=window_days,
        reversibility_remaining_pct=rev_pct,
        likely_next_phase=next_phase,
        inaction_cost_note=inaction_note,
        operational_time_pressure=_TIME_PRESSURE[state],
        counterfactual_note=_CF_NOTES[state],
    )
