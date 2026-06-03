"""
Adaptive Capacity Intelligence — Sprint 53.
Understands whether the operational system is becoming more or less capable of handling
pressure over time. NOT a snapshot. NOT a forecast. A cycle-based adaptation direction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# State → adaptation_direction
_DIRECTIONS: dict[str, str] = {
    "strengthening": "improving",
    "adaptive":      "stable",
    "plateauing":    "plateauing",
    "rigid":         "constrained",
    "deteriorating": "declining",
}

# State → adaptation_note
_NOTES: dict[str, str] = {
    "strengthening":
        "Система постепенно проходит повторное давление легче.",
    "adaptive":
        "Система сохраняет стабильную способность к операционной адаптации.",
    "plateauing":
        "Операционная адаптация сохраняется на стабильном уровне без дальнейшего ускорения восстановления.",
    "rigid":
        "Повторяющиеся циклы давления требуют сопоставимого объёма стабилизации без признаков ускорения адаптации.",
    "deteriorating":
        "Некоторые повторяющиеся циклы начинают требовать большего времени и ресурса на стабилизацию.",
}

# State → (stabilization_trend, observability_trend, recurrence_trend)
_TRENDS: dict[str, tuple[Optional[str], Optional[str], Optional[str]]] = {
    "strengthening": ("сокращается",   "улучшается",    "снижается"),
    "adaptive":      (None,            None,            None),
    "plateauing":    ("без изменений", None,            None),
    "rigid":         ("не улучшается", "фрагментируется", "нарастает"),
    "deteriorating": ("увеличивается", "деградирует",   "нарастает"),
}


@dataclass
class AdaptiveCapacity:
    state:                str
    adaptation_direction: str
    stabilization_trend:  Optional[str]
    observability_trend:  Optional[str]
    recurrence_trend:     Optional[str]
    adaptation_note:      str
    adaptation_confidence: int
    adaptation_cycles:    int


def compute_adaptive_capacity(
    signal_lifecycle_stage:        Optional[str],
    signal_recurrence_count:       Optional[int],
    recovery_state:                Optional[str],
    recovery_probability:          Optional[int],
    outcome_state:                 Optional[str],
    pressure_accumulation:         Optional[str],
    reversibility_state:           Optional[str],
    obs_recovery_state:            Optional[str],
    reversal_state:                Optional[str],
    timing_state:                  Optional[str],
    resilience_state:              Optional[str],
    resilience_trajectory:         Optional[str],
    trajectory_direction:          Optional[str],
    cascade_state:                 Optional[str],
) -> AdaptiveCapacity:
    """
    Classify the direction of operational adaptation capacity.
    Priority: deteriorating > rigid > strengthening > plateauing > adaptive.
    Only recurring/confirmed/persistent signals have meaningful adaptation cycles.
    """
    rec_count    = signal_recurrence_count or 0
    is_recurring = signal_lifecycle_stage in ("recurring", "confirmed", "persistent")
    cycles       = min(rec_count + 1, 8) if is_recurring else 1

    if not is_recurring:
        # No cycle history — default to adaptive, low confidence
        return _build("adaptive", 40, cycles)

    # ── deteriorating ──────────────────────────────────────────────────────────
    # Each cycle requires more time/resources; adaptation capacity declining
    det_flags = sum([
        outcome_state in ("failed", "repeated") and recovery_state in ("unstable", "structural"),
        resilience_trajectory == "structurally_degrading" and rec_count >= 2,
        pressure_accumulation == "compounding"
            and reversal_state in ("overextended", "structurally_locked")
            and obs_recovery_state in ("fragmented", "reset_required"),
        recovery_state == "unstable" and resilience_state in ("brittle", "collapsing", "exhausted"),
        obs_recovery_state == "reset_required" and outcome_state in ("failed", "repeated"),
    ])
    if det_flags >= 1:
        conf = 82 if det_flags >= 2 else 68
        return _build("deteriorating", conf, cycles)

    # ── rigid ──────────────────────────────────────────────────────────────────
    # Recurring pressure with unchanged recovery burden — no improvement over cycles
    rigid_flags = sum([
        recovery_state in ("structural", "unstable"),
        reversal_state == "structurally_locked",
        obs_recovery_state in ("fragmented", "distorted") and timing_state in ("structurally_late", "narrowing_window"),
        resilience_state in ("narrowing", "brittle") and outcome_state in ("temporary", "failed", None),
        pressure_accumulation in ("accumulating", "compounding") and recovery_state == "gradual",
        cascade_state in ("coupled_instability", "structurally_cascading") and rec_count >= 2,
    ])
    if rigid_flags >= 1:
        conf = 74 if rigid_flags >= 2 else 62
        return _build("rigid", conf, cycles)

    # ── strengthening ──────────────────────────────────────────────────────────
    # System handles recurring pressure progressively easier
    is_improving_outcome   = outcome_state in ("improved", "stabilized")
    is_fast_recovery       = recovery_state in ("quick", "gradual")
    is_good_obs            = obs_recovery_state in ("clear", "recovering", None)
    is_positive_trajectory = resilience_trajectory in ("recovering",)
    is_pressure_easing     = pressure_accumulation in ("dissipating", "stable")
    recovery_pct           = recovery_probability or 50

    if (
        is_improving_outcome and is_fast_recovery
        and is_good_obs and is_positive_trajectory
        and is_pressure_easing and recovery_pct >= 60
    ):
        return _build("strengthening", 78, cycles)

    # Partial strengthening: good outcome + good recovery, without full set
    if is_improving_outcome and is_fast_recovery and recovery_pct >= 55:
        return _build("strengthening", 64, cycles)

    # ── plateauing ─────────────────────────────────────────────────────────────
    # Improvement stopped, metrics flat, no degradation
    is_stable_outcome   = outcome_state in ("stabilized", "temporary", None)
    is_moderate_recovery = recovery_state in ("gradual", None)
    no_worsening        = trajectory_direction not in ("worsening", "critical")

    if is_stable_outcome and is_moderate_recovery and no_worsening:
        return _build("plateauing", 55, cycles)

    # ── adaptive (default for recurring) ───────────────────────────────────────
    return _build("adaptive", 58, cycles)


def _build(state: str, confidence: int, cycles: int) -> AdaptiveCapacity:
    stab_t, obs_t, rec_t = _TRENDS[state]
    return AdaptiveCapacity(
        state=state,
        adaptation_direction=_DIRECTIONS[state],
        stabilization_trend=stab_t,
        observability_trend=obs_t,
        recurrence_trend=rec_t,
        adaptation_note=_NOTES[state],
        adaptation_confidence=confidence,
        adaptation_cycles=cycles,
    )
