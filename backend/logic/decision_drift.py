"""
Decision Drift Engine — Sprint 47.

Assesses the quality of the operator's operational decision sequence over time.
NOT psychological analysis. NOT blame layer.
Operational observability + intervention coherence monitoring.

Language restrained throughout. No "error", "chaos", "wrong strategy".
Only: observability, stabilization continuity, recovery consistency,
intervention overlap, sequence coherence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DecisionDrift:
    drift_state:             str  # stable_execution | reactive_switching | fragmented_recovery | oscillating_pressure | stabilization_breakdown
    drift_note:              str  # restrained narrative
    intervention_overlap:    str  # none | low | moderate | high
    sequencing_continuity:   str  # stable | partial | fragmented | broken
    observation_reset_count: int  # signals currently in reset/reopening state


# ── Narratives ────────────────────────────────────────────────────────────────
_DRIFT_NOTES: dict[str, str] = {
    "stable_execution":        "Последовательность изменений остаётся наблюдаемой.",
    "reactive_switching":      "Часть изменений меняет направление до завершения окна стабилизации.",
    "fragmented_recovery":     "Новые вмешательства периодически перезапускают фазу наблюдения.",
    "oscillating_pressure":    "Операционная последовательность начинает терять устойчивость между фазами стабилизации.",
    "stabilization_breakdown": "Структура операционных вмешательств требует пересмотра последовательности стабилизации.",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _intervention_overlap(active: list) -> str:
    """Estimate how many signals are competing for stabilization attention simultaneously."""
    competing = sum(
        1 for i in active
        if getattr(i, "recovery_signal_state", None) in ("waiting", "stabilizing")
        or getattr(i, "obs_recovery_state", None) in ("recovering", "distorted", "fragmented", "reset_required")
    )
    total = len(active)
    if total == 0:
        return "none"
    ratio = competing / total
    if ratio >= 0.6:
        return "high"
    if ratio >= 0.35:
        return "moderate"
    if competing > 0:
        return "low"
    return "none"


def _sequencing_continuity(active: list) -> str:
    """Assess how consistently the stabilization sequence is progressing."""
    reset_states = sum(
        1 for i in active
        if getattr(i, "obs_recovery_state", None) == "reset_required"
        or getattr(i, "recovery_signal_state", None) == "reopening"
    )
    recurring_without_stage = sum(
        1 for i in active
        if getattr(i, "signal_lifecycle_stage", None) == "recurring"
        and getattr(i, "sequence_stage", None) is None
    )
    has_staged = any(getattr(i, "sequence_stage", None) is not None for i in active)

    if reset_states >= 2 or recurring_without_stage >= 3:
        return "broken"
    if recurring_without_stage >= 2 or reset_states >= 1:
        return "fragmented"
    if has_staged and recurring_without_stage >= 1:
        return "partial"
    return "stable"


# ── Main computation ──────────────────────────────────────────────────────────

def compute_decision_drift(
    insights:              list,
    commitment_state:      Optional[str],   # from StrategyCommitment
    commitment_shift_type: Optional[str],   # strategy_shift.shift_type or None
    interruption_risk:     Optional[str],   # from StrategyCommitment
    observability_quality: Optional[str],   # from StrategyCommitment
    intervention_style:    Optional[str],   # from OperatorStrategyProfile
    pacing_discipline:     Optional[str],   # from OperatorStrategyProfile
) -> DecisionDrift:
    active = [
        i for i in insights
        if getattr(i, "status", "") not in ("resolved", "dismissed")
    ]

    # ── Metrics ──────────────────────────────────────────────────────────────
    structurally_locked_count = sum(
        1 for i in active
        if getattr(i, "reversibility_state", None) == "structurally_locked"
    )
    obs_problematic_count = sum(
        1 for i in active
        if getattr(i, "obs_recovery_state", None) in ("fragmented", "distorted", "reset_required")
    )
    has_reset_required = any(
        getattr(i, "obs_recovery_state", None) == "reset_required"
        for i in active
    )
    has_structural_recurring_fail = any(
        getattr(i, "recovery_state", None) == "structural"
        and getattr(i, "signal_lifecycle_stage", None) == "recurring"
        and getattr(i, "outcome_state", None) in ("repeated", "failed")
        for i in active
    )
    recurring_cats = {
        getattr(i, "key", "").split(":")[0]
        for i in active
        if getattr(i, "signal_lifecycle_stage", None) == "recurring"
    }
    has_repeated_outcome = any(
        getattr(i, "outcome_state", None) in ("failed", "repeated")
        for i in active
    )
    fragmented_recurring = sum(
        1 for i in active
        if getattr(i, "signal_lifecycle_stage", None) == "recurring"
        and getattr(i, "sequence_stage", None) is None
    )
    observation_reset_count = sum(
        1 for i in active
        if getattr(i, "obs_recovery_state", None) == "reset_required"
        or getattr(i, "recovery_signal_state", None) == "reopening"
    )

    overlap     = _intervention_overlap(active)
    continuity  = _sequencing_continuity(active)

    # ── State classification — highest severity wins ──────────────────────────

    # stabilization_breakdown: multiple locked signals + reset/structural failures
    if (
        structurally_locked_count >= 2
        and (has_reset_required or has_structural_recurring_fail)
    ) or (
        structurally_locked_count >= 1
        and has_reset_required
        and has_structural_recurring_fail
    ):
        state = "stabilization_breakdown"

    # fragmented_recovery: observability breakdown + concurrency + resets
    elif (
        obs_problematic_count >= 2
        and (has_reset_required or len(active) >= 3)
        and observability_quality in ("degraded", "unclear")
    ):
        state = "fragmented_recovery"

    # oscillating_pressure: recurring category churn + repeated outcomes
    elif (
        len(recurring_cats) >= 2
        and has_repeated_outcome
        and (
            intervention_style in ("oscillating", "reactive")
            or fragmented_recurring >= 2
        )
    ):
        state = "oscillating_pressure"

    # reactive_switching: commitment fragmented + high interruption + direction change
    elif (
        commitment_state in ("fragmented", "abandoned")
        and interruption_risk in ("moderate", "high")
        and commitment_shift_type in ("fragmentation", "tactical_switch", "escalation")
    ) or (
        commitment_state == "fragmented"
        and intervention_style in ("reactive", "oscillating")
        and pacing_discipline == "weak"
    ):
        state = "reactive_switching"

    else:
        state = "stable_execution"

    return DecisionDrift(
        drift_state=state,
        drift_note=_DRIFT_NOTES[state],
        intervention_overlap=overlap,
        sequencing_continuity=continuity,
        observation_reset_count=observation_reset_count,
    )
