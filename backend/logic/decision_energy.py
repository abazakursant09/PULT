"""
Decision Energy Model — Sprint 56.
Measures the operational energy cost of stabilization interventions.
NOT productivity coaching. NOT management consulting.
Operational systems intelligence: coordination + observability + execution load.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Weight delta applied to recurring active insights before focus ranking
ENERGY_WEIGHT_DELTA: dict[str, int] = {
    "lightweight":            0,
    "manageable":             2,
    "draining":               6,
    "disruptive":            10,
    "structurally_exhausting": 15,
}

_ENERGY_NOTES: dict[str, str] = {
    "lightweight":
        "Текущее вмешательство требует ограниченной операционной координации"
        " и не создаёт выраженного дополнительного давления на систему.",
    "manageable":
        "Стабилизация остаётся управляемой в рамках текущего operational regime.",
    "draining":
        "Текущее вмешательство постепенно начинает требовать устойчивой"
        " операционной координации между несколькими зонами системы.",
    "disruptive":
        "Часть стабилизационных действий начинает создавать вторичное давление"
        " на соседние операционные процессы.",
    "structurally_exhausting":
        "Система постепенно теряет часть долгосрочной операционной гибкости"
        " из-за высокой стоимости повторяющихся стабилизационных вмешательств.",
}

_COORDINATION_LOADS: dict[str, str] = {
    "lightweight":            "minimal",
    "manageable":             "moderate",
    "draining":               "elevated",
    "disruptive":             "high",
    "structurally_exhausting": "structurally_distorted",
}

_OBS_LOADS: dict[str, str] = {
    "lightweight":            "isolated",
    "manageable":             "localized",
    "draining":               "degraded",
    "disruptive":             "fragmented",
    "structurally_exhausting": "structurally_distorted",
}

_STAB_BURDENS: dict[str, str] = {
    "lightweight":            "absorbable",
    "manageable":             "sustained",
    "draining":               "cumulative",
    "disruptive":             "expanding",
    "structurally_exhausting": "structurally_depleting",
}

_EXEC_COMPLEXITIES: dict[str, str] = {
    "lightweight":            "contained",
    "manageable":             "multi-step",
    "draining":               "cross-functional",
    "disruptive":             "systemic",
    "structurally_exhausting": "structurally_coupled",
}


@dataclass
class DecisionEnergy:
    energy_state:         str   # lightweight | manageable | draining | disruptive | structurally_exhausting
    coordination_load:    str   # minimal | moderate | elevated | high | structurally_distorted
    observability_load:   str   # isolated | localized | degraded | fragmented | structurally_distorted
    stabilization_burden: str   # absorbable | sustained | cumulative | expanding | structurally_depleting
    execution_complexity: str   # contained | multi-step | cross-functional | systemic | structurally_coupled
    energy_note:          str
    energy_confidence:    int


def _classify(
    n_recurring:          int,
    n_struct_degrading:   int,
    n_compounding:        int,
    n_coupled_cascade:    int,
    n_fragmented_obs:     int,
    n_struct_locked:      int,
    n_rigid_adapt:        int,
    n_total_active:       int,
    capacity_state:       Optional[str],
    regime:               Optional[str],
) -> str:
    # ── structurally_exhausting (highest priority) ─────────────────────────────
    if (
        regime == "containment"
        or (n_struct_degrading >= 1 and n_compounding >= 1)
        or (n_struct_locked >= 2 and n_compounding >= 1)
        or n_struct_degrading >= 2
    ):
        return "structurally_exhausting"

    # ── disruptive ─────────────────────────────────────────────────────────────
    if (
        (n_coupled_cascade >= 1 and n_fragmented_obs >= 1)
        or n_coupled_cascade >= 2
        or (n_fragmented_obs >= 2 and n_recurring >= 2)
        or (n_struct_locked >= 1 and n_coupled_cascade >= 1)
    ):
        return "disruptive"

    # ── draining ──────────────────────────────────────────────────────────────
    if (
        (n_recurring >= 2 and capacity_state in ("overloaded", "saturated"))
        or n_recurring >= 3
        or (n_rigid_adapt >= 2 and n_recurring >= 1)
        or (n_fragmented_obs >= 1 and n_recurring >= 2)
    ):
        return "draining"

    # ── manageable ────────────────────────────────────────────────────────────
    if n_recurring >= 1 or n_total_active >= 2:
        return "manageable"

    # ── lightweight (default) ─────────────────────────────────────────────────
    return "lightweight"


def _compute_confidence(
    n_recurring:          int,
    n_aligned_trajectory: int,
    n_regime_aligned:     int,
    has_memory_continuity: bool,
    n_fragmented_obs:     int,
    n_conflict_recovery:  int,
) -> int:
    score = 60
    if n_aligned_trajectory >= 2:
        score += 10
    if n_recurring >= 2:
        score += 8
    if n_regime_aligned >= 2:
        score += 8
    if has_memory_continuity:
        score += 6
    if n_fragmented_obs >= 2:
        score -= 10
    if n_conflict_recovery >= 1:
        score -= 8
    if n_recurring < 2:
        score -= 6
    return max(45, min(92, score))


def compute_decision_energy(
    insights:         list,         # list[InsightItem] enriched through Sprint 55
    capacity_state:   Optional[str] = None,
    regime:           Optional[str] = None,
) -> DecisionEnergy:
    """
    Aggregate portfolio signals to determine the operational energy cost
    of the current stabilization intervention set.
    Priority: structurally_exhausting → disruptive → draining → manageable → lightweight.
    """
    active = [
        i for i in insights
        if getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    recurring = [
        i for i in active
        if getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
    ]

    def _c(lst, attr, vals):
        v = vals if isinstance(vals, (list, tuple)) else (vals,)
        return sum(1 for i in lst if getattr(i, attr, None) in v)

    n_recurring       = len(recurring)
    n_struct_degrading = _c(recurring, "resilience_trajectory", "structurally_degrading")
    n_compounding     = _c(active,    "strategic_drift_state",  "compounding_repetition")
    n_coupled_cascade = _c(active,    "cascade_state",          ("coupled_instability", "structurally_cascading"))
    n_fragmented_obs  = _c(active,    "obs_recovery_state",     ("fragmented", "reset_required"))
    n_struct_locked   = sum(
        1 for i in active
        if getattr(i, "reversibility_state", None) == "structurally_locked"
        or getattr(i, "reversal_state", None) == "structurally_locked"
    )
    n_rigid_adapt     = _c(recurring, "adaptive_capacity_state", ("rigid", "deteriorating"))

    state = _classify(
        n_recurring=n_recurring,
        n_struct_degrading=n_struct_degrading,
        n_compounding=n_compounding,
        n_coupled_cascade=n_coupled_cascade,
        n_fragmented_obs=n_fragmented_obs,
        n_struct_locked=n_struct_locked,
        n_rigid_adapt=n_rigid_adapt,
        n_total_active=len(active),
        capacity_state=capacity_state,
        regime=regime,
    )

    # ── Confidence signals ─────────────────────────────────────────────────────
    n_traj_aligned = _c(recurring, "trajectory_state", ("escalating", "structurally_accumulating", "persistent"))
    n_recovering   = _c(recurring, "resilience_trajectory", "recovering")
    n_timing_ready = _c(recurring, "timing_state", ("intervention_ready", "stabilization_phase"))
    n_aligned_trajectory = n_traj_aligned + n_recovering + n_timing_ready

    n_regime_aligned = 0
    if regime in ("containment", "constrained", "defensive") and state in ("structurally_exhausting", "disruptive", "draining"):
        n_regime_aligned = 2
    elif regime in ("recovery_transition", "expansion") and state in ("lightweight", "manageable"):
        n_regime_aligned = 2

    has_memory_continuity = _c(active, "strategic_drift_state", ("aligned", "drifting")) >= 1
    n_conflict_recovery   = (
        _c(active, "reversal_state",     "overextended")
        + _c(active, "obs_recovery_state", "reset_required")
    )

    conf = _compute_confidence(
        n_recurring=n_recurring,
        n_aligned_trajectory=n_aligned_trajectory,
        n_regime_aligned=n_regime_aligned,
        has_memory_continuity=has_memory_continuity,
        n_fragmented_obs=n_fragmented_obs,
        n_conflict_recovery=n_conflict_recovery,
    )

    return DecisionEnergy(
        energy_state=state,
        coordination_load=_COORDINATION_LOADS[state],
        observability_load=_OBS_LOADS[state],
        stabilization_burden=_STAB_BURDENS[state],
        execution_complexity=_EXEC_COMPLEXITIES[state],
        energy_note=_ENERGY_NOTES[state],
        energy_confidence=conf,
    )
