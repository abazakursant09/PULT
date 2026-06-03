"""
Operational Phase Transition — Sprint 57.
Tracks which systemic phase the portfolio is transitioning into.
NOT outcome prediction. NOT collapse forecasting.
Operational systems intelligence: phase direction + coordination load + structural momentum.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Weight multiplier applied to recurring active insights before focus ranking
PHASE_WEIGHT_MULTIPLIER: dict[str, float] = {
    "adaptive_equilibrium":          1.00,
    "stabilization_cycle":           1.03,
    "defensive_convergence":         1.08,
    "structural_pressure_formation": 1.14,
    "resilience_fragmentation":      1.22,
    "constrained_operation":         1.30,
    "recovery_reentry":              0.96,
}

_PHASE_NOTES: dict[str, str] = {
    "adaptive_equilibrium":
        "Система сохраняет устойчивую способность адаптироваться без выраженного накопления structural pressure.",
    "stabilization_cycle":
        "Система постепенно входит в цикл последовательной стабилизации с сохранением operational flexibility.",
    "defensive_convergence":
        "Несколько operational signals постепенно смещают систему в более защитный режим управления.",
    "structural_pressure_formation":
        "Повторяющееся давление начинает постепенно формировать признаки устойчивого structural load.",
    "resilience_fragmentation":
        "Система начинает терять согласованность между восстановлением, наблюдаемостью и execution stability.",
    "constrained_operation":
        "Operational flexibility постепенно снижается из-за накопленного coordination pressure и повторяющихся stabilization cycles.",
    "recovery_reentry":
        "Система постепенно возвращается в фазу, где возможны более устойчивые stabilization decisions.",
}

_TRANSITION_DIRECTIONS: dict[str, str] = {
    "adaptive_equilibrium":          "stabilizing",
    "stabilization_cycle":           "stabilizing",
    "defensive_convergence":         "restrictive",
    "structural_pressure_formation": "deteriorating",
    "resilience_fragmentation":      "deteriorating",
    "constrained_operation":         "deteriorating",
    "recovery_reentry":              "recovering",
}

_TRANSITION_VELOCITIES: dict[str, str] = {
    "adaptive_equilibrium":          "stable",
    "stabilization_cycle":           "gradual",
    "defensive_convergence":         "gradual",
    "structural_pressure_formation": "gradual",
    "resilience_fragmentation":      "accelerating",
    "constrained_operation":         "gradual",
    "recovery_reentry":              "gradual",
}

_TRANSITION_STABILITIES: dict[str, str] = {
    "adaptive_equilibrium":          "stable",
    "stabilization_cycle":           "moderate",
    "defensive_convergence":         "moderate",
    "structural_pressure_formation": "unstable",
    "resilience_fragmentation":      "fragmented",
    "constrained_operation":         "fragmented",
    "recovery_reentry":              "moderate",
}

_TRANSITION_DRIVERS: dict[str, str] = {
    "adaptive_equilibrium":          "stable adaptive capacity",
    "stabilization_cycle":           "recovering operational resilience",
    "defensive_convergence":         "recurring coordination pressure",
    "structural_pressure_formation": "accumulating structural load",
    "resilience_fragmentation":      "fragmented recovery consistency",
    "constrained_operation":         "compounding stabilization burden",
    "recovery_reentry":              "reopening stabilization window",
}


@dataclass
class OperationalPhaseTransition:
    phase:                str   # one of the 7 states above
    transition_direction: str   # stabilizing | restrictive | deteriorating | recovering
    transition_velocity:  str   # stable | gradual | accelerating
    transition_stability: str   # stable | moderate | unstable | fragmented
    transition_driver:    str   # short phrase describing the primary driver
    phase_note:           str
    phase_confidence:     int


def _classify(
    n_recurring:          int,
    n_struct_degrading:   int,
    n_compounding:        int,
    n_coupled_cascade:    int,
    n_fragmented_obs:     int,
    n_obs_reset:          int,
    n_cf_locked:          int,
    n_deteriorating_adapt: int,
    n_narrowing_res:      int,
    n_rigid_adapt:        int,
    n_recovering:         int,
    n_strengthening:      int,
    n_structurally_acc:   int,
    n_lock_reopening:     int,
    n_failed_outcomes:    int,
    n_resilient:          int,
    n_total_active:       int,
    regime:               Optional[str],
    capacity_state:       Optional[str],
    energy_state:         Optional[str],
) -> str:
    # ── constrained_operation (highest severity) ───────────────────────────────
    if (
        regime == "containment"
        and energy_state == "structurally_exhausting"
        and n_coupled_cascade >= 1
        and n_compounding >= 1
    ) or (
        regime == "containment"
        and capacity_state in ("overloaded",)
        and n_struct_degrading >= 1
        and n_compounding >= 1
    ) or (
        n_failed_outcomes >= 2
        and n_compounding >= 1
        and n_coupled_cascade >= 1
        and n_struct_degrading >= 1
    ):
        return "constrained_operation"

    # ── resilience_fragmentation ───────────────────────────────────────────────
    if (
        n_struct_degrading >= 1
        and n_compounding >= 1
        and n_obs_reset >= 1
        and n_cf_locked >= 1
    ) or (
        n_struct_degrading >= 1
        and n_fragmented_obs >= 2
        and n_deteriorating_adapt >= 1
    ):
        return "resilience_fragmentation"

    # ── structural_pressure_formation ─────────────────────────────────────────
    if (
        n_recurring >= 2
        and n_struct_degrading >= 1
        and (n_structurally_acc >= 1 or n_coupled_cascade >= 1)
    ) or (
        n_recurring >= 3
        and n_fragmented_obs >= 1
    ) or (
        n_recurring >= 2
        and n_coupled_cascade >= 1
        and n_fragmented_obs >= 1
    ):
        return "structural_pressure_formation"

    # ── recovery_reentry ──────────────────────────────────────────────────────
    if (
        n_recovering >= 1
        and n_strengthening >= 1
        and n_struct_degrading == 0
        and n_lock_reopening >= 1
        and n_compounding == 0
    ):
        return "recovery_reentry"

    # ── defensive_convergence ─────────────────────────────────────────────────
    if (
        regime in ("defensive", "constrained")
        and n_recurring >= 1
    ) or (
        n_narrowing_res >= 2
        and n_rigid_adapt >= 1
        and n_recurring >= 1
    ) or (
        n_recurring >= 2
        and n_coupled_cascade >= 1
    ) or (
        energy_state in ("draining", "disruptive")
        and n_recurring >= 2
    ):
        return "defensive_convergence"

    # ── stabilization_cycle ───────────────────────────────────────────────────
    if (
        n_recovering >= 1
        or (
            n_recurring >= 1
            and energy_state not in ("draining", "disruptive", "structurally_exhausting")
            and regime not in ("constrained", "containment")
        )
    ):
        return "stabilization_cycle"

    # ── adaptive_equilibrium (default) ────────────────────────────────────────
    return "adaptive_equilibrium"


def _compute_confidence(
    n_recurring:         int,
    n_regime_traj_align: int,
    n_pattern_cont:      int,
    n_converging:        int,
    has_hist_continuity: bool,
    n_fragmented_obs:    int,
    n_conflict_recovery: int,
) -> int:
    score = 62
    if n_regime_traj_align >= 2:
        score += 10
    if n_pattern_cont >= 2:
        score += 8
    if n_converging >= 2:
        score += 8
    if has_hist_continuity:
        score += 6
    if n_fragmented_obs >= 2:
        score -= 10
    if n_conflict_recovery >= 1:
        score -= 8
    if n_recurring < 1:
        score -= 6
    return max(48, min(94, score))


def compute_phase_transition(
    insights:       list,           # list[InsightItem] enriched through Sprint 56
    regime:         Optional[str]  = None,
    capacity_state: Optional[str]  = None,
    energy_state:   Optional[str]  = None,
) -> OperationalPhaseTransition:
    """
    Derive the systemic operational phase the portfolio is transitioning into.
    Priority: constrained_operation → resilience_fragmentation → structural_pressure_formation
              → recovery_reentry → defensive_convergence → stabilization_cycle → adaptive_equilibrium.
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

    n_recurring           = len(recurring)
    n_struct_degrading    = _c(recurring, "resilience_trajectory",   "structurally_degrading")
    n_compounding         = _c(active,    "strategic_drift_state",   "compounding_repetition")
    n_coupled_cascade     = _c(active,    "cascade_state",           ("coupled_instability", "structurally_cascading"))
    n_fragmented_obs      = _c(active,    "obs_recovery_state",      ("fragmented", "reset_required"))
    n_obs_reset           = _c(active,    "obs_recovery_state",      "reset_required")
    n_cf_locked           = sum(
        1 for i in active
        if getattr(i, "counterfactual_pressure_state", None) == "structurally_locked"
        or getattr(i, "reversibility_state", None) == "structurally_locked"
    )
    n_deteriorating_adapt = _c(recurring, "adaptive_capacity_state", "deteriorating")
    n_narrowing_res       = _c(recurring, "resilience_state",        ("narrowing", "brittle"))
    n_rigid_adapt         = _c(recurring, "adaptive_capacity_state", ("rigid", "deteriorating"))
    n_recovering          = _c(recurring, "resilience_trajectory",   "recovering")
    n_strengthening       = _c(active,    "adaptive_capacity_state", "strengthening")
    n_structurally_acc    = _c(recurring, "trajectory_state",        "structurally_accumulating")
    n_lock_reopening      = _c(active,    "stabilization_lock_state", "reopening")
    n_failed_outcomes     = _c(recurring, "outcome_state",           ("failed", "repeated"))
    n_resilient           = _c(active,    "resilience_state",        ("adaptive", "resilient"))

    phase = _classify(
        n_recurring=n_recurring,
        n_struct_degrading=n_struct_degrading,
        n_compounding=n_compounding,
        n_coupled_cascade=n_coupled_cascade,
        n_fragmented_obs=n_fragmented_obs,
        n_obs_reset=n_obs_reset,
        n_cf_locked=n_cf_locked,
        n_deteriorating_adapt=n_deteriorating_adapt,
        n_narrowing_res=n_narrowing_res,
        n_rigid_adapt=n_rigid_adapt,
        n_recovering=n_recovering,
        n_strengthening=n_strengthening,
        n_structurally_acc=n_structurally_acc,
        n_lock_reopening=n_lock_reopening,
        n_failed_outcomes=n_failed_outcomes,
        n_resilient=n_resilient,
        n_total_active=len(active),
        regime=regime,
        capacity_state=capacity_state,
        energy_state=energy_state,
    )

    # ── Confidence ─────────────────────────────────────────────────────────────
    # regime + resilience trajectory alignment
    n_regime_traj_align = 0
    if regime in ("containment", "constrained") and phase in ("constrained_operation", "resilience_fragmentation"):
        n_regime_traj_align = 2
    elif regime in ("recovery_transition", "expansion") and phase in ("recovery_reentry", "adaptive_equilibrium"):
        n_regime_traj_align = 2
    elif regime == "defensive" and phase in ("defensive_convergence", "structural_pressure_formation"):
        n_regime_traj_align = 2
    elif n_struct_degrading >= 1 and phase in ("resilience_fragmentation", "structural_pressure_formation"):
        n_regime_traj_align = 1

    # recurring systemic pattern continuity
    n_pattern_cont = min(n_recurring, 3)

    # converging transition drivers: multiple signal types pointing same direction
    n_converging = sum([
        n_struct_degrading >= 1,
        n_compounding >= 1,
        n_coupled_cascade >= 1,
        n_deteriorating_adapt >= 1,
        n_fragmented_obs >= 1,
    ])

    has_hist_continuity = _c(active, "strategic_drift_state", ("aligned",)) >= 1

    n_conflict_recovery = (
        _c(active, "reversal_state",          "overextended")
        + _c(recurring, "resilience_trajectory", "recovering")
        * (1 if n_struct_degrading >= 1 else 0)   # recovering + struct_degrading = conflict
    )

    conf = _compute_confidence(
        n_recurring=n_recurring,
        n_regime_traj_align=n_regime_traj_align,
        n_pattern_cont=n_pattern_cont,
        n_converging=n_converging,
        has_hist_continuity=has_hist_continuity,
        n_fragmented_obs=n_fragmented_obs,
        n_conflict_recovery=n_conflict_recovery,
    )

    return OperationalPhaseTransition(
        phase=phase,
        transition_direction=_TRANSITION_DIRECTIONS[phase],
        transition_velocity=_TRANSITION_VELOCITIES[phase],
        transition_stability=_TRANSITION_STABILITIES[phase],
        transition_driver=_TRANSITION_DRIVERS[phase],
        phase_note=_PHASE_NOTES[phase],
        phase_confidence=conf,
    )
