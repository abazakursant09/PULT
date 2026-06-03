"""
Operational Trajectory Modeling — Sprint 33.

Models operational pressure direction, reversibility horizon,
and stabilization optionality for each insight.

NOT a prediction engine. NOT a forecast.
Operational trajectory: where is this pressure heading?
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class OperationalTrajectory:
    trajectory_state:           str           # reversible | stabilizing | persistent | escalating | structurally_accumulating
    trajectory_direction:       str           # improving | stable | worsening | critical
    reversibility_state:        str           # easily_reversible | conditionally_reversible | narrowing_window | structurally_locked
    stabilization_window_days:  Optional[int] # approximate soft-stabilization horizon; None = high uncertainty
    pressure_accumulation:      str           # dissipating | stable | accumulating | compounding
    structural_risk:            str           # low | moderate | high
    trajectory_note:            str           # one-sentence restrained narrative
    trajectory_confidence_band: str           # low | moderate | stable | high


# ── Stabilization window approximations (midpoint days, base state) ──────────
# NOT deadlines. Approximate horizon before friction escalates materially.
_WINDOW_BASE: dict[str, int] = {
    "seo_opportunity":  10,   # 7–14 days
    "high_ad_spend":    21,   # 14–30 days
    "margin_crisis":    30,   # 21–45 days
    "low_stock":         5,   # 3–7 days
    "high_rating":      18,   # 14–21 days
    "price_pressure_cluster": 45,  # 30–60 days systemic
}

# ── Trajectory notes per state ────────────────────────────────────────────────
_NOTES: dict[str, str] = {
    "reversible": (
        "Давление остаётся обратимым при своевременном вмешательстве."
    ),
    "stabilizing": (
        "Система демонстрирует признаки стабилизации."
    ),
    "persistent": (
        "Давление устойчиво, но пока не нарастает."
    ),
    "escalating": (
        "Окно мягкой стабилизации постепенно сокращается."
    ),
    "structurally_accumulating": (
        "Давление постепенно переходит из локального отклонения "
        "в структурную нестабильность."
    ),
}

# Sequence failure note — upstream unresolved
_UPSTREAM_FAILURE_NOTE = (
    "Нестабилизированная рекламная нагрузка продолжает усиливать давление "
    "на unit-экономику."
)


def _cat(key: str) -> str:
    return key.split(":")[0]


def _has_systemic_pattern(portfolio_patterns: list) -> bool:
    return any(
        getattr(p, "stabilization_complexity", "") == "systemic"
        for p in portfolio_patterns
    )


def _has_compounding_pressure(portfolio_patterns: list) -> bool:
    """True if cross-MP patterns with expanding dependency chains exist."""
    systemic_count = sum(
        1 for p in portfolio_patterns
        if getattr(p, "stabilization_complexity", "") == "systemic"
    )
    return systemic_count >= 2


def compute_operational_trajectory(
    insight:               Any,
    lifecycle:             Optional[str],
    decay:                 Optional[str],
    sequencing:            Any,          # SequencedAction or None
    portfolio_patterns:    list,
    operator_profile:      Any,
    upstream_unresolved:   bool = False,
) -> OperationalTrajectory:
    """
    Compute operational trajectory for a single insight.

    Returns an OperationalTrajectory — direction, reversibility,
    stabilization horizon, and pressure accumulation state.

    NEVER modifies raw signal confidence.
    """
    cat = _cat(getattr(insight, "key", ""))

    # Pull insight attributes
    friction        = getattr(insight, "resolution_difficulty", None)
    recurrence      = getattr(insight, "signal_recurrence_count", 0) or 0
    outcome_state   = getattr(insight, "outcome_state", None)
    age_days        = getattr(insight, "signal_age_days", 0) or 0
    signal_state    = getattr(insight, "signal_state", None)
    seq_stage       = getattr(insight, "sequence_stage", None)

    systemic        = _has_systemic_pattern(portfolio_patterns)
    compounding     = _has_compounding_pressure(portfolio_patterns)

    # ── Trajectory state determination ───────────────────────────────────────

    if outcome_state in ("improved", "stabilized") or lifecycle == "stabilized":
        state = "stabilizing"
    elif lifecycle == "resolved":
        state = "stabilizing"
    elif decay == "fading" and recurrence == 0:
        state = "stabilizing"
    elif (
        compounding
        or (systemic and lifecycle == "recurring" and friction == "hard")
        or (lifecycle == "recurring" and age_days > 45 and friction == "hard")
    ):
        state = "structurally_accumulating"
    elif (
        (lifecycle == "recurring" and systemic)
        or (lifecycle == "recurring" and upstream_unresolved)
        or (lifecycle == "recurring" and recurrence >= 3)
        or (decay == "persistent" and friction == "hard")
    ):
        state = "escalating"
    elif (
        lifecycle in ("confirmed", "recurring")
        and decay in ("persistent", "aging")
        and outcome_state not in ("improved", "stabilized")
    ):
        state = "persistent"
    elif lifecycle in ("emerging", "confirmed") and decay in ("fresh", "aging", None):
        state = "reversible"
    elif decay in ("fresh", "aging") and lifecycle is None:
        state = "reversible"
    else:
        state = "persistent"

    # ── Trajectory direction ──────────────────────────────────────────────────
    _DIRECTION_MAP = {
        "reversible":               "stable",
        "stabilizing":              "improving",
        "persistent":               "stable",
        "escalating":               "worsening",
        "structurally_accumulating": "critical",
    }
    direction = _DIRECTION_MAP.get(state, "stable")

    # ── Reversibility state ───────────────────────────────────────────────────
    if state in ("stabilizing", "reversible"):
        reversibility = "easily_reversible"
    elif state == "persistent":
        if decay == "persistent" or recurrence >= 2:
            reversibility = "conditionally_reversible"
        else:
            reversibility = "easily_reversible"
    elif state == "escalating":
        reversibility = "narrowing_window"
    else:  # structurally_accumulating
        reversibility = "structurally_locked"

    # ── Pressure accumulation ─────────────────────────────────────────────────
    if state in ("stabilizing",) or decay == "fading":
        accumulation = "dissipating"
    elif state == "reversible" and recurrence == 0:
        accumulation = "stable"
    elif state == "persistent":
        accumulation = "accumulating"
    elif state == "escalating":
        accumulation = "accumulating"
    elif state == "structurally_accumulating":
        accumulation = "compounding"
    else:
        accumulation = "stable"

    # ── Structural risk ───────────────────────────────────────────────────────
    _RISK_MAP = {
        "reversible":               "low",
        "stabilizing":              "low",
        "persistent":               "moderate",
        "escalating":               "high",
        "structurally_accumulating": "high",
    }
    structural_risk = _RISK_MAP.get(state, "moderate")

    # ── Stabilization window (approximate, NOT deadline) ─────────────────────
    base_window = _WINDOW_BASE.get(cat)
    if base_window is None:
        window_days = None
    elif state in ("stabilizing",):
        window_days = None  # already stabilizing
    elif state == "structurally_accumulating":
        window_days = None  # high uncertainty
    elif state == "escalating":
        window_days = max(5, int(base_window * 0.7))  # compress ~30%
    else:
        window_days = base_window

    # ── Trajectory confidence ─────────────────────────────────────────────────
    if lifecycle in ("confirmed", "recurring") and decay is not None:
        traj_conf = "stable"
    elif lifecycle in ("emerging",) or decay is None:
        traj_conf = "moderate"
    elif lifecycle is None:
        traj_conf = "low"
    else:
        traj_conf = "moderate"

    if compounding or systemic:
        traj_conf = "stable"

    # ── Trajectory note ───────────────────────────────────────────────────────
    # Sequence failure overrides base note for downstream insights
    if upstream_unresolved and state in ("escalating", "structurally_accumulating"):
        note = _UPSTREAM_FAILURE_NOTE
    else:
        note = _NOTES.get(state, "Операционное давление активно отслеживается.")

    return OperationalTrajectory(
        trajectory_state=state,
        trajectory_direction=direction,
        reversibility_state=reversibility,
        stabilization_window_days=window_days,
        pressure_accumulation=accumulation,
        structural_risk=structural_risk,
        trajectory_note=note,
        trajectory_confidence_band=traj_conf,
    )


# ── Weight modifiers for focus engine integration ─────────────────────────────
# Applied to insight.weight AFTER trajectory is computed.
# Never modifies raw confidence.

_TRAJECTORY_WEIGHT_DELTA: dict[str, float] = {
    "structurally_accumulating": +12.0,
    "escalating":                 +8.0,
    "persistent":                 +2.0,
    "reversible":                  0.0,
    "stabilizing":               -10.0,
}


def trajectory_weight_delta(trajectory_state: str) -> float:
    return _TRAJECTORY_WEIGHT_DELTA.get(trajectory_state, 0.0)
