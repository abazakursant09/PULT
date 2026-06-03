"""
Resilience Trajectory Intelligence — Sprint 52.
How operational elasticity evolves over time: recovering, stabilizing, degrading, structurally_degrading.
Builds on Sprint 51 resilience snapshot fields.
NOT a forecast. NOT failure prediction. An operational elasticity direction assessment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Trajectory state → restrained narrative
_TRAJECTORY_NOTES: dict[str, str] = {
    "recovering":
        "Операционная устойчивость постепенно восстанавливает способность абсорбировать давление.",
    "stabilizing":
        "Система сохраняет текущий уровень устойчивости без признаков ускоряющейся деградации.",
    "degrading":
        "За последние циклы способность системы стабилизировать давление постепенно снижается.",
    "structurally_degrading":
        "Несколько операционных слоёв одновременно теряют способность к самостоятельной стабилизации.",
}


@dataclass
class ResilienceTrajectory:
    resilience_trajectory:            str
    resilience_trajectory_velocity:   Optional[str]   # gradual | accelerating (degrading states only)
    resilience_trajectory_note:       str
    absorption_transition_note:       Optional[str]
    resilience_trajectory_confidence: int


def _infer_absorption_transition(
    absorption_capacity:   str,
    trajectory_direction:  Optional[str],
    pressure_accumulation: Optional[str],
    recovery_state:        Optional[str],
    trajectory:            str,
    resilience_score:      int,
) -> Optional[str]:
    """
    Infer recent absorption capacity movement from current context.
    Only emit when current state strongly implies a recent shift.
    Suppress if evidence is insufficient (avoids fabricated transitions).
    """
    if absorption_capacity == "exhausted":
        return "Абсорбционный ресурс системы исчерпан — дальнейшее поглощение давления ограничено."
    if absorption_capacity == "narrowing" and pressure_accumulation in ("compounding", "accumulating"):
        return "Способность системы поглощать давление сузилась — вероятно с moderate до narrowing."
    if absorption_capacity == "moderate" and trajectory == "recovering" and recovery_state in ("quick", "gradual"):
        return "После периода давления система постепенно вернулась к moderate absorption."
    if absorption_capacity == "high" and trajectory == "recovering" and trajectory_direction == "improving":
        return "Абсорбционная ёмкость системы восстановилась до высокого уровня."
    return None


def compute_resilience_trajectory(
    resilience_state:              str,
    absorption_capacity:           str,
    resilience_score:              int,
    trajectory_state:              Optional[str],
    trajectory_direction:          Optional[str],
    recovery_state:                Optional[str],
    outcome_state:                 Optional[str],
    signal_lifecycle_stage:        Optional[str],
    signal_recurrence_count:       Optional[int],
    pressure_accumulation:         Optional[str],
    reversibility_state:           Optional[str],
    counterfactual_pressure_state: Optional[str],
    cascade_state:                 Optional[str],
    obs_recovery_state:            Optional[str],
    reversal_state:                Optional[str],
    timing_state:                  Optional[str],
) -> ResilienceTrajectory:
    """
    Classify how operational elasticity is evolving.
    Priority: structurally_degrading > degrading > recovering > stabilizing.
    """
    rec_count   = signal_recurrence_count or 0
    is_recurring = signal_lifecycle_stage in ("recurring", "confirmed", "persistent")
    is_escalating = (
        trajectory_direction in ("worsening", "critical")
        or trajectory_state in ("escalating", "structurally_accumulating")
    )

    # ── structurally_degrading ────────────────────────────────────────────────
    # Primary: collapsing/exhausted always qualifies
    # brittle: needs at least one secondary structural flag
    structural_flags = sum([
        reversibility_state == "structurally_locked",
        recovery_state == "structural",
        cascade_state == "structurally_cascading",
        obs_recovery_state == "reset_required",
        rec_count >= 4,
        outcome_state in ("failed", "repeated"),
    ])

    if resilience_state in ("collapsing", "exhausted"):
        trajectory      = "structurally_degrading"
        velocity        = "accelerating"
        confidence      = 88
    elif resilience_state == "brittle" and structural_flags >= 1:
        trajectory      = "structurally_degrading"
        velocity        = "accelerating"
        confidence      = 84
    # ── degrading ─────────────────────────────────────────────────────────────
    elif (
        resilience_state in ("narrowing", "brittle")
        or (absorption_capacity in ("narrowing", "exhausted") and is_escalating)
        or (recovery_state == "unstable" and is_recurring)
        or cascade_state in ("coupled_instability", "structurally_cascading")
        or (pressure_accumulation in ("accumulating", "compounding") and is_recurring)
    ):
        trajectory = "degrading"
        # Velocity: accelerating if multiple strong signals
        accel_flags = sum([
            pressure_accumulation == "compounding",
            cascade_state in ("coupled_instability", "structurally_cascading"),
            resilience_score <= 30,
            recovery_state == "unstable" and is_recurring,
            timing_state in ("structurally_late", "immediate"),
        ])
        velocity   = "accelerating" if accel_flags >= 2 else "gradual"
        confidence = 78 if accel_flags >= 2 else 62
    # ── recovering ────────────────────────────────────────────────────────────
    elif (
        resilience_state in ("adaptive", "resilient")
        and (
            outcome_state == "improved"
            or trajectory_direction == "improving"
            or counterfactual_pressure_state == "stable"
            or recovery_state in ("quick", "gradual")
        )
        and absorption_capacity != "exhausted"
    ):
        trajectory = "recovering"
        velocity   = None
        confidence = 74
    # ── stabilizing ───────────────────────────────────────────────────────────
    else:
        trajectory = "stabilizing"
        velocity   = None
        confidence = 50

    absorption_transition = _infer_absorption_transition(
        absorption_capacity=absorption_capacity,
        trajectory_direction=trajectory_direction,
        pressure_accumulation=pressure_accumulation,
        recovery_state=recovery_state,
        trajectory=trajectory,
        resilience_score=resilience_score,
    )

    return ResilienceTrajectory(
        resilience_trajectory=trajectory,
        resilience_trajectory_velocity=velocity,
        resilience_trajectory_note=_TRAJECTORY_NOTES[trajectory],
        absorption_transition_note=absorption_transition,
        resilience_trajectory_confidence=confidence,
    )
