"""
Institutional Inertia — Sprint 60.
Detects degree of system resistance to operational change.
NOT regime. NOT doctrine. NOT resilience.
How expensive structural adaptation has become; how locked execution patterns are.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

INERTIA_WEIGHT_MULTIPLIER: dict[str, float] = {
    "flexible_structure":          0.95,
    "adaptive_inertia":            1.04,
    "operational_hardening":       1.12,
    "structural_inertia":          1.22,
    "locked_operational_behavior": 1.32,
    "institutional_freeze":        1.42,
}

_INERTIA_NOTES: dict[str, str] = {
    "flexible_structure":
        "Operational system сохраняет достаточную structural elasticity и способность к adaptive behavioral change.",
    "adaptive_inertia":
        "Часть operational responses начинает постепенно повторяться как устойчивый execution preference.",
    "operational_hardening":
        "Operational behavior постепенно становится менее adaptive и increasingly повторяет ограниченный набор stabilization responses.",
    "structural_inertia":
        "Система increasingly поддерживает continuity через recurring structural compensation вместо adaptive restructuring.",
    "locked_operational_behavior":
        "Operational behavior increasingly воспроизводится независимо от результата intervention cycles.",
    "institutional_freeze":
        "Operational system increasingly сохраняет continuity за счёт rigid structural repetition, ограничивая adaptive mobility.",
}

_ADAPTATION_RESISTANCES: dict[str, str] = {
    "flexible_structure":          "low",
    "adaptive_inertia":            "moderate",
    "operational_hardening":       "elevated",
    "structural_inertia":          "high",
    "locked_operational_behavior": "very_high",
    "institutional_freeze":        "extreme",
}

_BEHAVIORAL_REPEATABILITIES: dict[str, str] = {
    "flexible_structure":          "low",
    "adaptive_inertia":            "emerging",
    "operational_hardening":       "high",
    "structural_inertia":          "persistent",
    "locked_operational_behavior": "locked",
    "institutional_freeze":        "institutionalized",
}

_STRUCTURAL_ELASTICITIES: dict[str, str] = {
    "flexible_structure":          "high",
    "adaptive_inertia":            "moderate",
    "operational_hardening":       "narrowing",
    "structural_inertia":          "low",
    "locked_operational_behavior": "minimal",
    "institutional_freeze":        "collapsed",
}

_RECOVERY_MOBILITIES: dict[str, str] = {
    "flexible_structure":          "high",
    "adaptive_inertia":            "moderate",
    "operational_hardening":       "slowing",
    "structural_inertia":          "constrained",
    "locked_operational_behavior": "restricted",
    "institutional_freeze":        "immobile",
}

_INERTIA_DRIVERS: dict[str, str] = {
    "flexible_structure":          "none",
    "adaptive_inertia":            "behavioral_continuity",
    "operational_hardening":       "defensive_repetition",
    "structural_inertia":          "structural_pressure",
    "locked_operational_behavior": "path_dependency",
    "institutional_freeze":        "systemic_locking",
}

_INERTIA_WINDOWS: dict[str, Optional[int]] = {
    "flexible_structure":          None,
    "adaptive_inertia":            21,
    "operational_hardening":       30,
    "structural_inertia":          45,
    "locked_operational_behavior": 60,
    "institutional_freeze":        90,
}


@dataclass
class InstitutionalInertia:
    inertia_state:            str
    adaptation_resistance:    str
    behavioral_repeatability: str
    structural_elasticity:    str
    recovery_mobility:        str
    inertia_driver:           str
    inertia_window_days:      Optional[int]
    inertia_note:             str
    inertia_confidence:       int


def _classify(
    n_recurring:           int,
    n_compounding:         int,
    n_fragmented_obs:      int,
    n_failed_outcomes:     int,
    n_cf_locked:           int,
    n_rigid_adapt:         int,
    n_structurally_acc:    int,
    n_resilience_degraded: int,
    n_cascade_systemic:    int,
    n_obs_reset:           int,
    n_lock_waiting:        int,
    n_timing_compressed:   int,
    n_narrowing_res:       int,
    n_resilient:           int,
    n_strengthening:       int,
    n_plateauing_adapt:    int,
    n_total_active:        int,
    regime:                Optional[str],
    phase:                 Optional[str],
    topology_state:        Optional[str],
    energy_state:          Optional[str],
    doctrine_state:        Optional[str],
) -> str:
    # ── institutional_freeze (highest severity) ──────────────────────────────────
    if (
        doctrine_state in ("stabilization_dependency", "structurally_embedded_doctrine", "rigid_operational_doctrine")
        and n_resilience_degraded >= 1
        and energy_state == "structurally_exhausting"
        and phase == "constrained_operation"
    ) or (
        n_compounding >= 1
        and n_resilience_degraded >= 1
        and n_obs_reset >= 2
        and n_cascade_systemic >= 1
    ) or (
        doctrine_state == "rigid_operational_doctrine"
        and n_cf_locked >= 1
        and n_failed_outcomes >= 2
        and n_resilience_degraded >= 1
    ):
        return "institutional_freeze"

    # ── locked_operational_behavior ──────────────────────────────────────────────
    if (
        doctrine_state == "rigid_operational_doctrine"
        and n_cf_locked >= 1
        and n_failed_outcomes >= 1
        and phase == "constrained_operation"
    ) or (
        n_rigid_adapt >= 2
        and n_compounding >= 1
        and n_failed_outcomes >= 1
    ) or (
        n_rigid_adapt >= 1
        and n_cf_locked >= 1
        and n_recurring >= 2
        and n_failed_outcomes >= 1
    ):
        return "locked_operational_behavior"

    # ── structural_inertia ───────────────────────────────────────────────────────
    if (
        doctrine_state == "structurally_embedded_doctrine"
        and topology_state in ("structurally_unbalanced", "collapsing_compensation")
        and n_recurring >= 2
    ) or (
        n_compounding >= 1
        and n_fragmented_obs >= 1
        and n_recurring >= 2
        and n_structurally_acc >= 1
    ) or (
        n_recurring >= 3
        and n_lock_waiting >= 1
        and doctrine_state in (
            "structurally_embedded_doctrine",
            "rigid_operational_doctrine",
            "stabilization_dependency",
        )
    ):
        return "structural_inertia"

    # ── operational_hardening ────────────────────────────────────────────────────
    if (
        doctrine_state == "defensive_patterning"
        and n_timing_compressed >= 2
        and n_recurring >= 2
    ) or (
        n_narrowing_res >= 1
        and n_recurring >= 2
        and (n_rigid_adapt >= 1 or n_plateauing_adapt >= 1)
    ) or (
        regime in ("defensive", "constrained")
        and n_lock_waiting >= 1
        and n_recurring >= 2
        and doctrine_state in ("defensive_patterning", "recurring_operational_bias")
    ):
        return "operational_hardening"

    # ── adaptive_inertia ─────────────────────────────────────────────────────────
    if (
        n_recurring >= 2
        and doctrine_state in ("recurring_operational_bias", "defensive_patterning")
    ) or (
        n_recurring >= 1
        and n_lock_waiting >= 1
        and n_resilient >= 1
    ) or (
        n_recurring >= 2
        and n_structurally_acc == 0
        and n_compounding == 0
    ):
        return "adaptive_inertia"

    # ── flexible_structure (default) ─────────────────────────────────────────────
    return "flexible_structure"


def _compute_confidence(
    n_recurring:                    int,
    n_compounding:                  int,
    n_lock_waiting:                 int,
    n_fragmented_obs:               int,
    n_isolated:                     int,
    n_strengthening:                int,
    n_reversal_window:              int,
    has_structural_continuity:      bool,
    has_doctrine_regime_topo_align: bool,
    has_hist_persistence:           bool,
    phase:                          Optional[str],
) -> int:
    score = 68
    if has_structural_continuity:
        score += 10
    if has_doctrine_regime_topo_align:
        score += 10
    if n_lock_waiting >= 2 or (n_recurring >= 3 and n_lock_waiting >= 1):
        score += 8
    if has_hist_persistence:
        score += 8
    if phase == "constrained_operation" and n_recurring >= 2:
        score += 6
    if n_fragmented_obs >= 2:
        score -= 10
    if n_isolated >= 2 and n_recurring < 1:
        score -= 8
    if n_reversal_window >= 1 or n_strengthening >= 1:
        score -= 6
    return max(54, min(97, score))


def compute_institutional_inertia(
    insights:       list,
    regime:         Optional[str] = None,
    phase:          Optional[str] = None,
    topology_state: Optional[str] = None,
    energy_state:   Optional[str] = None,
    doctrine_state: Optional[str] = None,
) -> InstitutionalInertia:
    """
    Detect degree of resistance to operational change.
    Priority: institutional_freeze → locked_operational_behavior → structural_inertia
              → operational_hardening → adaptive_inertia → flexible_structure.
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
    n_compounding         = _c(active,    "strategic_drift_state",    "compounding_repetition")
    n_fragmented_obs      = _c(active,    "obs_recovery_state",       ("fragmented", "reset_required", "distorted"))
    n_failed_outcomes     = _c(recurring, "outcome_state",            ("failed", "repeated"))
    n_cf_locked           = sum(
        1 for i in active
        if getattr(i, "counterfactual_pressure_state", None) == "structurally_locked"
        or getattr(i, "reversibility_state", None) == "structurally_locked"
    )
    n_rigid_adapt         = _c(recurring, "adaptive_capacity_state",  ("rigid", "deteriorating"))
    n_structurally_acc    = _c(recurring, "trajectory_state",         "structurally_accumulating")
    n_narrowing_res       = _c(recurring, "resilience_state",         ("narrowing", "brittle"))
    n_timing_compressed   = _c(recurring, "timing_state",             ("narrowing_window", "structurally_late"))
    n_lock_waiting        = _c(active,    "stabilization_lock_state", ("waiting", "locked"))
    n_obs_reset           = _c(active,    "obs_recovery_state",       "reset_required")
    n_resilient           = _c(active,    "resilience_state",         ("adaptive", "resilient"))
    n_strengthening       = _c(active,    "adaptive_capacity_state",  "strengthening")
    n_plateauing_adapt    = _c(active,    "adaptive_capacity_state",  "plateauing")
    n_resilience_degraded = _c(active,    "resilience_state",         ("exhausted", "collapsing"))
    n_cascade_systemic    = _c(active,    "cascade_state",            "structurally_cascading")
    n_isolated            = _c(active,    "cascade_state",            "isolated")
    n_reversal_window     = _c(active,    "reversal_state",           "reversal_window")

    state = _classify(
        n_recurring=n_recurring,
        n_compounding=n_compounding,
        n_fragmented_obs=n_fragmented_obs,
        n_failed_outcomes=n_failed_outcomes,
        n_cf_locked=n_cf_locked,
        n_rigid_adapt=n_rigid_adapt,
        n_structurally_acc=n_structurally_acc,
        n_resilience_degraded=n_resilience_degraded,
        n_cascade_systemic=n_cascade_systemic,
        n_obs_reset=n_obs_reset,
        n_lock_waiting=n_lock_waiting,
        n_timing_compressed=n_timing_compressed,
        n_narrowing_res=n_narrowing_res,
        n_resilient=n_resilient,
        n_strengthening=n_strengthening,
        n_plateauing_adapt=n_plateauing_adapt,
        n_total_active=len(active),
        regime=regime,
        phase=phase,
        topology_state=topology_state,
        energy_state=energy_state,
        doctrine_state=doctrine_state,
    )

    has_structural_continuity = (
        n_compounding >= 1
        or n_structurally_acc >= 2
    )
    has_doctrine_regime_topo_align = (
        doctrine_state in ("structurally_embedded_doctrine", "rigid_operational_doctrine", "stabilization_dependency")
        and regime in ("containment", "constrained")
        and topology_state in ("structurally_unbalanced", "collapsing_compensation")
    )
    has_hist_persistence = (
        n_compounding >= 1
        or n_failed_outcomes >= 2
    )

    conf = _compute_confidence(
        n_recurring=n_recurring,
        n_compounding=n_compounding,
        n_lock_waiting=n_lock_waiting,
        n_fragmented_obs=n_fragmented_obs,
        n_isolated=n_isolated,
        n_strengthening=n_strengthening,
        n_reversal_window=n_reversal_window,
        has_structural_continuity=has_structural_continuity,
        has_doctrine_regime_topo_align=has_doctrine_regime_topo_align,
        has_hist_persistence=has_hist_persistence,
        phase=phase,
    )

    return InstitutionalInertia(
        inertia_state=state,
        adaptation_resistance=_ADAPTATION_RESISTANCES[state],
        behavioral_repeatability=_BEHAVIORAL_REPEATABILITIES[state],
        structural_elasticity=_STRUCTURAL_ELASTICITIES[state],
        recovery_mobility=_RECOVERY_MOBILITIES[state],
        inertia_driver=_INERTIA_DRIVERS[state],
        inertia_window_days=_INERTIA_WINDOWS[state],
        inertia_note=_INERTIA_NOTES[state],
        inertia_confidence=conf,
    )
