"""
Operational Capacity Intelligence — Sprint 37.

Models operator bandwidth: how much simultaneous pressure the operator can handle.
NOT burnout detection. NOT productivity coaching.
Operational survivability: protect attention capacity, defer non-critical execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class OperatorCapacity:
    capacity_state:             str            # stable | loaded | saturated | overloaded
    operational_bandwidth_score: int           # 0-100
    overload_risk:              str            # low | moderate | high | critical
    defer_categories:           list[str]      # categories to temporarily defer
    capacity_note:              Optional[str]  # restrained one-sentence narrative


# ── Categories that must NEVER be deferred ───────────────────────────────────
_NEVER_DEFER = {"low_stock", "margin_crisis"}

# ── Capacity narratives ───────────────────────────────────────────────────────
_NOTES: dict[str, str] = {
    "stable": (
        "Операционная нагрузка остаётся в управляемом диапазоне."
    ),
    "loaded": (
        "Несколько параллельных стабилизаций постепенно увеличивают "
        "фрагментацию внимания."
    ),
    "saturated": (
        "Текущее количество одновременных изменений может снижать "
        "качество операционных решений."
    ),
    "overloaded": (
        "Система рекомендует сократить количество параллельных "
        "вмешательств до стабилизации ключевых зон."
    ),
}

# ── Overload risk by state ────────────────────────────────────────────────────
_OVERLOAD_RISK: dict[str, str] = {
    "stable":    "low",
    "loaded":    "moderate",
    "saturated": "high",
    "overloaded": "critical",
}

# ── Categories that can be deferred when bandwidth is low ────────────────────
_DEFERRABLE = ["seo_opportunity", "sales_growth"]


def _cat(key: str) -> str:
    return key.split(":")[0]


def compute_operator_capacity(
    insights:          list[Any],
    portfolio_patterns: list,
    fatigue_score:     float = 0.0,
    stability_credit:  float = 0.0,
) -> OperatorCapacity:
    """
    Compute operator capacity from current insight state.
    Uses structural, lifecycle, recovery, and sequencing signals.
    NEVER modifies insights. Returns OperatorCapacity dataclass.
    """
    active = [
        i for i in insights
        if getattr(i, "status", "active") not in ("resolved", "dismissed")
    ]

    score = 100

    # ── Penalties ─────────────────────────────────────────────────────────────
    for ins in active:
        if getattr(ins, "resolution_difficulty", None) == "hard":
            score -= 12
        if getattr(ins, "signal_lifecycle_stage", None) == "recurring":
            score -= 8
        if getattr(ins, "recovery_state", None) == "structural":
            score -= 15
        if (getattr(ins, "signal_decay_state", None) == "fading"
                and ins.status not in ("resolved", "dismissed")):
            score -= 4
        if (getattr(ins, "sequence_stage", None) or 0) >= 2:
            score -= 6

    # Systemic portfolio patterns
    systemic_count = sum(
        1 for p in portfolio_patterns
        if getattr(p, "stabilization_complexity", "") == "systemic"
    )
    score -= systemic_count * 14

    # Overload tradeoff cluster: 2+ moderate/significant tradeoffs
    heavy_tradeoffs = sum(
        1 for i in active
        if getattr(i, "tradeoff_severity", None) in ("moderate", "significant")
    )
    if heavy_tradeoffs >= 2:
        score -= 8

    # ── Recoveries ────────────────────────────────────────────────────────────
    if stability_credit > 0.7:
        score += 8
    has_resolved_positive = any(
        getattr(i, "outcome_state", None) in ("improved", "stabilized")
        for i in insights
    )
    if has_resolved_positive:
        score += 6

    # Fatigue score already captures alert overload — reflect it
    score -= int(fatigue_score * 15)

    score = max(0, min(100, score))

    # ── State thresholds ──────────────────────────────────────────────────────
    if score >= 75:
        state = "stable"
    elif score >= 55:
        state = "loaded"
    elif score >= 35:
        state = "saturated"
    else:
        state = "overloaded"

    # ── Defer list ────────────────────────────────────────────────────────────
    defer_categories: list[str] = []
    if state in ("saturated", "overloaded"):
        active_cats = {_cat(i.key) for i in active}
        for cat in _DEFERRABLE:
            if cat not in _NEVER_DEFER and cat in active_cats:
                defer_categories.append(cat)

        # Also defer monitor-only, fading low-impact, unstable low-impact
        for ins in active:
            cat = _cat(ins.key)
            if cat in _NEVER_DEFER or cat in defer_categories:
                continue
            is_monitor   = getattr(ins, "intervention_tier", None) == "monitor"
            is_fading    = getattr(ins, "signal_decay_state", None) == "fading"
            is_unstable  = getattr(ins, "recovery_state", None) == "unstable"
            low_impact   = (getattr(ins, "impact_score", 100) or 100) < 40
            if is_monitor or (is_fading and low_impact) or (is_unstable and low_impact):
                if cat not in defer_categories:
                    defer_categories.append(cat)

    return OperatorCapacity(
        capacity_state=state,
        operational_bandwidth_score=score,
        overload_risk=_OVERLOAD_RISK[state],
        defer_categories=defer_categories,
        capacity_note=_NOTES[state],
    )
