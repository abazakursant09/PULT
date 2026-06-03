"""
Adaptive Intervention Timing Intelligence — Sprint 48.

Determines when to intervene, when to wait, and how timing quality changes.
NOT urgency engine. NOT deadline system. NOT pressure tactics.

Operational timing intelligence: what is the current intervention window quality,
what happens if you act now vs wait, when does observability improve.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class InterventionTiming:
    timing_state:                str            # observation_phase | stabilization_phase | emerging_window | narrowing_window | immediate | structurally_late | optimal
    intervention_readiness:      str            # ready | nearly_ready | unstable | elevated | late | monitor
    timing_note:                 Optional[str]
    optimal_window_days:         Optional[int]  # approximate; always displayed as label, not raw number
    premature_intervention_risk: str            # low | moderate | high
    premature_risk_note:         Optional[str]
    delayed_intervention_risk:   str            # low | moderate | high | structural
    delayed_risk_note:           Optional[str]
    waiting_benefit:             Optional[str]  # shown for observation_phase only
    readiness_condition:         Optional[str]  # prerequisite for safe intervention


# ── Timing state notes ────────────────────────────────────────────────────────
_TIMING_NOTES: dict[str, Optional[str]] = {
    "observation_phase":   "Система продолжает отделять эффект предыдущих изменений от текущего давления.",
    "stabilization_phase": "Операционная динамика постепенно стабилизируется, но часть эффектов ещё формируется.",
    "emerging_window":     "Операционное окно постепенно формируется — наблюдение подтверждает развитие сигнала.",
    "narrowing_window":    "Часть операционной гибкости постепенно сужается — окно стабилизации продолжает сокращаться.",
    "immediate":           "Сигнал требует раннего вмешательства для предотвращения дальнейшего накопления давления.",
    "structurally_late":   "Часть гибкости уже утрачена — вероятна более длительная стабилизация.",
    "optimal":             None,
}

# ── Readiness by state ────────────────────────────────────────────────────────
_READINESS: dict[str, str] = {
    "observation_phase":   "unstable",
    "stabilization_phase": "nearly_ready",
    "emerging_window":     "monitor",
    "narrowing_window":    "elevated",
    "immediate":           "ready",
    "structurally_late":   "late",
    "optimal":             "ready",
}

# ── Risk narratives ───────────────────────────────────────────────────────────
_PREMATURE_NOTES: dict[str, Optional[str]] = {
    "high":     "Раннее вмешательство может дополнительно исказить наблюдаемость системы.",
    "moderate": None,
    "low":      None,
}

_DELAYED_NOTES: dict[str, Optional[str]] = {
    "structural": "Дальнейшее ожидание, вероятно, увеличит стоимость последующей стабилизации.",
    "high":       "Дальнейшее ожидание может увеличить стоимость последующей стабилизации.",
    "moderate":   None,
    "low":        None,
}


def compute_intervention_timing(
    insight:                              object,
    commitment_state:                     Optional[str],
    trajectory_state:                     Optional[str],
    reversibility_state:                  Optional[str],
    recovery_signal_state:                Optional[str],
    obs_recovery_state:                   Optional[str],
    counterfactual_pressure_state:        Optional[str],
    signal_lifecycle_stage:               Optional[str],
    stabilization_window_days:            Optional[int],
    counterfactual_transition_window_days: Optional[int],
    sequence_stage:                       Optional[int],
    forecast_fragility_state:             Optional[str],
    lock_reentry_condition:               Optional[str],
) -> InterventionTiming:
    cat           = getattr(insight, "key", "").split(":")[0].replace("demo_", "")
    is_low_stock  = cat == "low_stock"
    is_stage1_fragile = (
        sequence_stage == 1
        and forecast_fragility_state in ("fragile", "critical")
        and signal_lifecycle_stage == "recurring"
    )

    # ── Timing state — highest severity wins ──────────────────────────────────
    if is_low_stock or is_stage1_fragile:
        state = "immediate"

    elif (
        reversibility_state == "structurally_locked"
        or counterfactual_pressure_state == "structurally_locked"
    ):
        state = "structurally_late"

    elif (
        reversibility_state == "narrowing_window"
        or (
            counterfactual_pressure_state == "accelerating"
            and trajectory_state in ("escalating", "persistent", "structurally_accumulating")
        )
    ):
        state = "narrowing_window"

    elif (
        counterfactual_pressure_state == "narrowing"
        and reversibility_state not in ("structurally_locked", "narrowing_window")
        and signal_lifecycle_stage in ("confirmed", "recurring")
    ):
        state = "emerging_window"

    elif (
        recovery_signal_state == "stabilizing"
        or trajectory_state == "stabilizing"
        or obs_recovery_state == "recovering"
    ):
        state = "stabilization_phase"

    elif (
        recovery_signal_state == "waiting"
        or obs_recovery_state in ("fragmented", "distorted", "reset_required")
    ):
        state = "observation_phase"

    else:
        state = "optimal"

    # ── Optimal window days ───────────────────────────────────────────────────
    base = stabilization_window_days or counterfactual_transition_window_days
    if base is not None:
        if state == "narrowing_window":
            window: Optional[int] = max(3, int(base * 0.6))
        elif state == "structurally_late":
            window = max(7, int(base * 0.3))
        elif state == "stabilization_phase":
            window = max(3, int(base * 0.8))
        else:
            window = base
    else:
        window = None

    # ── Premature risk ────────────────────────────────────────────────────────
    if (
        state == "observation_phase"
        and obs_recovery_state in ("fragmented", "distorted", "reset_required")
        and commitment_state in ("fragmented", "abandoned")
    ):
        premature = "high"
    elif (
        state in ("observation_phase", "stabilization_phase")
        or obs_recovery_state in ("recovering", "distorted")
    ):
        premature = "moderate"
    else:
        premature = "low"

    # ── Delayed risk ──────────────────────────────────────────────────────────
    if state == "structurally_late":
        delayed = "structural"
    elif (
        state == "narrowing_window"
        and trajectory_state in ("escalating", "structurally_accumulating")
        and counterfactual_pressure_state in ("accelerating", "structurally_locked")
    ):
        delayed = "high"
    elif (
        state in ("narrowing_window", "emerging_window")
        or counterfactual_pressure_state == "narrowing"
    ):
        delayed = "moderate"
    else:
        delayed = "low"

    return InterventionTiming(
        timing_state=state,
        intervention_readiness=_READINESS[state],
        timing_note=_TIMING_NOTES[state],
        optimal_window_days=window,
        premature_intervention_risk=premature,
        premature_risk_note=_PREMATURE_NOTES[premature],
        delayed_intervention_risk=delayed,
        delayed_risk_note=_DELAYED_NOTES[delayed],
        waiting_benefit=(
            "Дополнительное время наблюдения повысит точность следующего решения."
            if state == "observation_phase" else None
        ),
        readiness_condition=(
            lock_reentry_condition or "После завершения окна наблюдения"
            if state == "observation_phase" else None
        ),
    )
