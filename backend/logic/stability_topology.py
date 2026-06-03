"""
Stability Topology — Sprint 58.
Maps which execution layers are carrying operational stability and which are failing.
NOT collapse forecasting. NOT management alarm system.
Operational systems intelligence: structural load distribution across execution layers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

TOPOLOGY_WEIGHT_MULTIPLIER: dict[str, float] = {
    "balanced_stability":      0.95,
    "compensating_structure":  1.02,
    "narrowing_support":       1.08,
    "fragmented_stability":    1.16,
    "structurally_unbalanced": 1.24,
    "collapsing_compensation": 1.34,
}

_TOPOLOGY_NOTES: dict[str, str] = {
    "balanced_stability":
        "Operational stability остаётся относительно равномерно распределённой между основными execution layers.",
    "compensating_structure":
        "Часть operational stability постепенно удерживается за счёт ограниченного числа still-stable execution layers.",
    "narrowing_support":
        "Operational flexibility постепенно сужается, а stability increasingly зависит от ограниченных support layers.",
    "fragmented_stability":
        "Разные части operational system постепенно теряют согласованность между recovery, observability и execution.",
    "structurally_unbalanced":
        "Система increasingly удерживает краткосрочную operational continuity за счёт growing structural compensation pressure.",
    "collapsing_compensation":
        "Remaining operational stability постепенно удерживается лишь за счёт временных compensating structures.",
}

_DOMINANT_LAYERS: dict[str, str] = {
    "balanced_stability":      "execution_continuity",
    "compensating_structure":  "observability",
    "narrowing_support":       "execution_continuity",
    "fragmented_stability":    "localized_execution",
    "structurally_unbalanced": "short_term_execution",
    "collapsing_compensation": "temporary_execution_support",
}

_WEAKEST_LAYERS: dict[str, str] = {
    "balanced_stability":      "none",
    "compensating_structure":  "adaptation",
    "narrowing_support":       "resilience",
    "fragmented_stability":    "observability",
    "structurally_unbalanced": "resilience",
    "collapsing_compensation": "systemic_resilience",
}

_COMPENSATION_BEHAVIORS: dict[str, str] = {
    "balanced_stability":      "distributed",
    "compensating_structure":  "localized_compensation",
    "narrowing_support":       "narrowing",
    "fragmented_stability":    "fragmented",
    "structurally_unbalanced": "structural_overcompensation",
    "collapsing_compensation": "depleting",
}

_STRUCTURAL_BALANCES: dict[str, str] = {
    "balanced_stability":      "balanced",
    "compensating_structure":  "moderate",
    "narrowing_support":       "fragile",
    "fragmented_stability":    "unstable",
    "structurally_unbalanced": "unbalanced",
    "collapsing_compensation": "collapsed",
}

_REMAINING_FLEXIBILITIES: dict[str, str] = {
    "balanced_stability":      "high",
    "compensating_structure":  "moderate",
    "narrowing_support":       "narrowing",
    "fragmented_stability":    "limited",
    "structurally_unbalanced": "low",
    "collapsing_compensation": "minimal",
}


@dataclass
class StabilityTopology:
    topology_state:           str   # balanced_stability | compensating_structure | narrowing_support | fragmented_stability | structurally_unbalanced | collapsing_compensation
    dominant_stability_layer: str
    weakest_stability_layer:  str
    compensation_behavior:    str
    structural_balance:       str
    remaining_flexibility:    str
    topology_note:            str
    topology_confidence:      int


def _classify(
    n_recurring:          int,
    n_struct_degrading:   int,
    n_compounding:        int,
    n_coupled_cascade:    int,
    n_fragmented_obs:     int,
    n_obs_reset:          int,
    n_cf_locked:          int,
    n_collapsing_res:     int,
    n_failed_outcomes:    int,
    n_narrowing_res:      int,
    n_degrading_traj:     int,
    n_rigid_adapt:        int,
    n_cf_narrowing:       int,
    n_structurally_acc:   int,
    n_resilient:          int,
    n_strengthening:      int,
    n_total_active:       int,
    regime:               Optional[str],
    capacity_state:       Optional[str],
    energy_state:         Optional[str],
    phase:                Optional[str],
) -> str:
    # ── collapsing_compensation (highest severity) ─────────────────────────────
    if (
        n_cf_locked >= 1
        and n_collapsing_res >= 1
        and (n_obs_reset >= 1 or n_fragmented_obs >= 2)
        and (capacity_state in ("overloaded",) or n_failed_outcomes >= 2)
    ) or (
        phase == "constrained_operation"
        and n_coupled_cascade >= 1
        and n_struct_degrading >= 1
        and n_collapsing_res >= 1
    ):
        return "collapsing_compensation"

    # ── structurally_unbalanced ────────────────────────────────────────────────
    if (
        n_struct_degrading >= 1
        and regime == "containment"
        and (energy_state == "structurally_exhausting" or n_compounding >= 1)
    ) or (
        n_struct_degrading >= 1
        and n_compounding >= 1
        and n_structurally_acc >= 1
        and n_recurring >= 2
    ):
        return "structurally_unbalanced"

    # ── fragmented_stability ──────────────────────────────────────────────────
    if (
        n_fragmented_obs >= 1
        and n_coupled_cascade >= 1
        and n_recurring >= 2
        and n_compounding >= 1
    ) or (
        n_obs_reset >= 1
        and n_coupled_cascade >= 1
        and n_rigid_adapt >= 1
    ) or (
        n_fragmented_obs >= 2
        and n_recurring >= 2
        and n_rigid_adapt >= 1
    ):
        return "fragmented_stability"

    # ── narrowing_support ─────────────────────────────────────────────────────
    if (
        n_recurring >= 2
        and n_narrowing_res >= 1
        and n_rigid_adapt >= 1
    ) or (
        n_degrading_traj >= 1
        and n_cf_narrowing >= 1
        and n_recurring >= 1
    ) or (
        n_recurring >= 3
        and energy_state in ("draining", "disruptive")
    ):
        return "narrowing_support"

    # ── compensating_structure ────────────────────────────────────────────────
    if (
        n_narrowing_res >= 1
        and n_recurring >= 1
        and n_struct_degrading == 0
    ) or (
        n_recurring >= 1
        and n_rigid_adapt >= 1
        and n_fragmented_obs == 0
    ):
        return "compensating_structure"

    # ── balanced_stability (default) ──────────────────────────────────────────
    return "balanced_stability"


def _compute_confidence(
    n_recurring:         int,
    n_res_topo_align:    int,
    n_comp_continuity:   int,
    n_phase_align:       int,
    has_hist_stability:  bool,
    n_fragmented_obs:    int,
    n_conflict_recovery: int,
    n_isolated:          int,
) -> int:
    score = 64
    if n_res_topo_align >= 2:
        score += 10
    if n_comp_continuity >= 2:
        score += 8
    if n_phase_align >= 2:
        score += 8
    if has_hist_stability:
        score += 6
    if n_fragmented_obs >= 2:
        score -= 10
    if n_conflict_recovery >= 1:
        score -= 8
    if n_isolated >= 2 and n_recurring < 1:
        score -= 6
    return max(50, min(95, score))


def compute_stability_topology(
    insights:       list,           # list[InsightItem] enriched through Sprint 57
    regime:         Optional[str]  = None,
    capacity_state: Optional[str]  = None,
    energy_state:   Optional[str]  = None,
    phase:          Optional[str]  = None,
) -> StabilityTopology:
    """
    Determine the structural load distribution across operational execution layers.
    Priority: collapsing_compensation → structurally_unbalanced → fragmented_stability
              → narrowing_support → compensating_structure → balanced_stability.
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

    n_recurring        = len(recurring)
    n_struct_degrading = _c(recurring, "resilience_trajectory",    "structurally_degrading")
    n_compounding      = _c(active,    "strategic_drift_state",    "compounding_repetition")
    n_coupled_cascade  = _c(active,    "cascade_state",            ("coupled_instability", "structurally_cascading"))
    n_fragmented_obs   = _c(active,    "obs_recovery_state",       ("fragmented", "reset_required", "distorted"))
    n_obs_reset        = _c(active,    "obs_recovery_state",       "reset_required")
    n_cf_locked        = sum(
        1 for i in active
        if getattr(i, "counterfactual_pressure_state", None) == "structurally_locked"
        or getattr(i, "reversibility_state", None) == "structurally_locked"
    )
    n_collapsing_res   = _c(active,    "resilience_state",         ("collapsing", "exhausted"))
    n_failed_outcomes  = _c(recurring, "outcome_state",            ("failed", "repeated"))
    n_narrowing_res    = _c(recurring, "resilience_state",         ("narrowing", "brittle"))
    n_degrading_traj   = _c(recurring, "resilience_trajectory",    ("degrading", "structurally_degrading"))
    n_rigid_adapt      = _c(recurring, "adaptive_capacity_state",  ("rigid", "deteriorating", "plateauing"))
    n_cf_narrowing     = _c(active,    "counterfactual_pressure_state", "narrowing")
    n_structurally_acc = _c(recurring, "trajectory_state",         "structurally_accumulating")
    n_resilient        = _c(active,    "resilience_state",         ("adaptive", "resilient"))
    n_strengthening    = _c(active,    "adaptive_capacity_state",  "strengthening")

    state = _classify(
        n_recurring=n_recurring,
        n_struct_degrading=n_struct_degrading,
        n_compounding=n_compounding,
        n_coupled_cascade=n_coupled_cascade,
        n_fragmented_obs=n_fragmented_obs,
        n_obs_reset=n_obs_reset,
        n_cf_locked=n_cf_locked,
        n_collapsing_res=n_collapsing_res,
        n_failed_outcomes=n_failed_outcomes,
        n_narrowing_res=n_narrowing_res,
        n_degrading_traj=n_degrading_traj,
        n_rigid_adapt=n_rigid_adapt,
        n_cf_narrowing=n_cf_narrowing,
        n_structurally_acc=n_structurally_acc,
        n_resilient=n_resilient,
        n_strengthening=n_strengthening,
        n_total_active=len(active),
        regime=regime,
        capacity_state=capacity_state,
        energy_state=energy_state,
        phase=phase,
    )

    # ── Confidence ─────────────────────────────────────────────────────────────
    n_res_topo_align = 0
    if regime in ("containment", "constrained") and state in ("collapsing_compensation", "structurally_unbalanced"):
        n_res_topo_align = 2
    elif regime in ("recovery_transition", "expansion") and state == "balanced_stability":
        n_res_topo_align = 2
    elif n_struct_degrading >= 1 and state in ("structurally_unbalanced", "fragmented_stability"):
        n_res_topo_align = 1

    n_comp_continuity = min(n_recurring, 3)

    n_phase_align = 0
    if phase in ("constrained_operation", "resilience_fragmentation") and state in ("collapsing_compensation", "structurally_unbalanced", "fragmented_stability"):
        n_phase_align = 2
    elif phase in ("adaptive_equilibrium", "recovery_reentry") and state == "balanced_stability":
        n_phase_align = 2

    has_hist_stability = _c(active, "strategic_drift_state", ("aligned",)) >= 1

    n_conflict_recovery = (
        _c(active, "reversal_state", "overextended")
        + (_c(recurring, "resilience_trajectory", "recovering") if n_struct_degrading >= 1 else 0)
    )

    n_isolated = _c(active, "cascade_state", "isolated")

    conf = _compute_confidence(
        n_recurring=n_recurring,
        n_res_topo_align=n_res_topo_align,
        n_comp_continuity=n_comp_continuity,
        n_phase_align=n_phase_align,
        has_hist_stability=has_hist_stability,
        n_fragmented_obs=n_fragmented_obs,
        n_conflict_recovery=n_conflict_recovery,
        n_isolated=n_isolated,
    )

    return StabilityTopology(
        topology_state=state,
        dominant_stability_layer=_DOMINANT_LAYERS[state],
        weakest_stability_layer=_WEAKEST_LAYERS[state],
        compensation_behavior=_COMPENSATION_BEHAVIORS[state],
        structural_balance=_STRUCTURAL_BALANCES[state],
        remaining_flexibility=_REMAINING_FLEXIBILITIES[state],
        topology_note=_TOPOLOGY_NOTES[state],
        topology_confidence=conf,
    )
