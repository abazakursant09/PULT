"""
Resilience Snapshot Intelligence — Sprint 51.
Point-in-time assessment of operational shock absorption capacity.
Answers: how resilient is the system RIGHT NOW.
Sprint 52 (trajectory) uses these outputs to understand how resilience evolves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Category → weakest operational layer
_WEAKEST_LAYER: dict[str, str] = {
    "margin_crisis":    "маржинальная устойчивость",
    "high_ad_spend":    "рекламная эффективность",
    "low_stock":        "складской резерв",
    "seo_opportunity":  "SEO-видимость",
    "high_rating":      "рейтинговая стабильность",
    "sales_growth":     "маржинальная модель",
}

# resilience_state → approximate days until state shift
_RESILIENCE_WINDOWS: dict[str, Optional[int]] = {
    "adaptive":   None,
    "resilient":  21,
    "moderate":   14,
    "narrowing":  10,
    "brittle":    7,
    "collapsing": 4,
    "exhausted":  None,
}

# resilience_state → restrained note
_RESILIENCE_NOTES: dict[str, str] = {
    "adaptive":   "Система демонстрирует высокую способность к компенсации давления.",
    "resilient":  "Операционная устойчивость сохраняется на достаточном уровне для стабилизации.",
    "moderate":   "Уровень устойчивости позволяет компенсировать умеренное давление без дестабилизации.",
    "narrowing":  "Способность системы поглощать давление постепенно снижается.",
    "brittle":    "Система находится в зоне повышенной операционной хрупкости.",
    "collapsing": "Несколько операционных слоёв одновременно теряют устойчивость.",
    "exhausted":  "Абсорбционный ресурс системы близок к операционному пределу.",
}


@dataclass
class ResilienceSnapshot:
    resilience_state:          str
    absorption_capacity:       str
    weakest_operational_layer: Optional[str]
    resilience_window:         Optional[int]
    resilience_score:          int
    resilience_note:           str


def compute_resilience_snapshot(
    insight_category:              str,
    trajectory_state:              Optional[str],
    trajectory_direction:          Optional[str],
    recovery_state:                Optional[str],
    recovery_probability:          Optional[int],
    outcome_state:                 Optional[str],
    reversibility_state:           Optional[str],
    pressure_accumulation:         Optional[str],
    counterfactual_pressure_state: Optional[str],
    signal_lifecycle_stage:        Optional[str],
    signal_recurrence_count:       Optional[int],
    signal_decay_state:            Optional[str],
    cascade_state:                 Optional[str],
    obs_recovery_state:            Optional[str],
    reversal_state:                Optional[str],
    timing_state:                  Optional[str],
    tradeoff_severity:             Optional[str],
) -> ResilienceSnapshot:
    """
    Compute resilience snapshot for a single insight.
    Score-based composite → maps to resilience_state and absorption_capacity.
    """
    rec_count    = signal_recurrence_count or 0
    recovery_pct = recovery_probability or 50

    # Composite score — base 55
    score = 55

    # Positive contributions
    if trajectory_direction == "improving":
        score += 12
    if trajectory_state in ("reversible", "stabilizing"):
        score += 8
    if outcome_state == "improved":
        score += 10
    if outcome_state == "stabilized":
        score += 5
    if recovery_state == "quick":
        score += 8
    if recovery_state == "gradual":
        score += 4
    if recovery_pct >= 75:
        score += 6
    if counterfactual_pressure_state == "stable":
        score += 8
    if reversibility_state == "easily_reversible":
        score += 8
    if signal_lifecycle_stage == "stabilized":
        score += 6
    if signal_decay_state in ("fresh",):
        score += 4
    if pressure_accumulation == "dissipating":
        score += 8

    # Negative contributions
    if trajectory_direction == "worsening":
        score -= 10
    if trajectory_direction == "critical":
        score -= 18
    if trajectory_state == "escalating":
        score -= 8
    if trajectory_state == "structurally_accumulating":
        score -= 12
    if pressure_accumulation == "accumulating":
        score -= 8
    if pressure_accumulation == "compounding":
        score -= 15
    if recovery_state == "unstable":
        score -= 12
    if recovery_state == "structural":
        score -= 8
    if recovery_pct < 30:
        score -= 8
    if outcome_state == "failed":
        score -= 10
    if outcome_state == "repeated":
        score -= 14
    if reversibility_state == "narrowing_window":
        score -= 8
    if reversibility_state == "structurally_locked":
        score -= 15
    if cascade_state == "coupled_instability":
        score -= 8
    if cascade_state == "structurally_cascading":
        score -= 16
    if obs_recovery_state == "fragmented":
        score -= 6
    if obs_recovery_state == "reset_required":
        score -= 10
    if reversal_state == "overextended":
        score -= 6
    if reversal_state == "structurally_locked":
        score -= 12
    if timing_state == "structurally_late":
        score -= 8
    if tradeoff_severity == "significant":
        score -= 6
    if rec_count >= 3:
        score -= 6
    if rec_count >= 5:
        score -= 6  # cumulative

    score = max(0, min(100, score))

    # Map score → resilience_state
    if score >= 75:
        resilience_state = "adaptive"
    elif score >= 60:
        resilience_state = "resilient"
    elif score >= 42:
        resilience_state = "moderate"
    elif score >= 27:
        resilience_state = "narrowing"
    elif score >= 12:
        resilience_state = "brittle"
    elif score >= 4:
        resilience_state = "collapsing"
    else:
        resilience_state = "exhausted"

    # Map score → absorption_capacity
    if score >= 68:
        absorption_capacity = "high"
    elif score >= 42:
        absorption_capacity = "moderate"
    elif score >= 18:
        absorption_capacity = "narrowing"
    else:
        absorption_capacity = "exhausted"

    return ResilienceSnapshot(
        resilience_state=resilience_state,
        absorption_capacity=absorption_capacity,
        weakest_operational_layer=_WEAKEST_LAYER.get(insight_category),
        resilience_window=_RESILIENCE_WINDOWS[resilience_state],
        resilience_score=score,
        resilience_note=_RESILIENCE_NOTES[resilience_state],
    )
