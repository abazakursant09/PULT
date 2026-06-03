"""
Structural Recovery Capacity — Sprint 61.
Evaluates whether structural recovery is architecturally possible without redesign.
NOT resilience. NOT stabilization. NOT inertia.
Recoverability diagnostics: can the system structurally restore itself?
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

RECOVERY_CAPACITY_WEIGHT_MULTIPLIER: dict[str, float] = {
    "structurally_recoverable":     0.92,
    "recoverable_with_adaptation":  1.02,
    "constrained_recovery":         1.12,
    "restructuring_dependent":      1.24,
    "continuity_without_recovery":  1.36,
    "structurally_exhausted":       1.48,
}

_RECOVERY_NOTES: dict[str, str] = {
    "structurally_recoverable":
        "Система сохраняет полную структурную восстановимость без необходимости в каких-либо вмешательствах.",
    "recoverable_with_adaptation":
        "Система способна к структурному восстановлению через целевую адаптацию без изменения архитектуры.",
    "constrained_recovery":
        "Структурное восстановление остаётся возможным, но всё более ограничено и ресурсно затратное.",
    "restructuring_dependent":
        "Восстановление структуры возможно только через архитектурную или системную реструктуризацию.",
    "continuity_without_recovery":
        "Система поддерживает операционную continuity без признаков устойчивой структурной восстановимости.",
    "structurally_exhausted":
        "Система сохраняет операционную continuity, но утратила способность к структурному восстановлению.",
}

_STRUCTURAL_RECOVERABILITIES: dict[str, str] = {
    "structurally_recoverable":    "high",
    "recoverable_with_adaptation": "moderate",
    "constrained_recovery":        "limited",
    "restructuring_dependent":     "fragile",
    "continuity_without_recovery": "minimal",
    "structurally_exhausted":      "collapsed",
}

_RECOVERY_ELASTICITIES: dict[str, str] = {
    "structurally_recoverable":    "high",
    "recoverable_with_adaptation": "moderate",
    "constrained_recovery":        "narrowing",
    "restructuring_dependent":     "low",
    "continuity_without_recovery": "restricted",
    "structurally_exhausted":      "minimal",
}

_RESTRUCTURING_REQUIREMENTS: dict[str, str] = {
    "structurally_recoverable":    "minimal",
    "recoverable_with_adaptation": "targeted",
    "constrained_recovery":        "significant",
    "restructuring_dependent":     "high",
    "continuity_without_recovery": "extensive",
    "structurally_exhausted":      "transformational",
}

_CONTINUITY_DEPENDENCES: dict[str, str] = {
    "structurally_recoverable":    "low",
    "recoverable_with_adaptation": "moderate",
    "constrained_recovery":        "elevated",
    "restructuring_dependent":     "high",
    "continuity_without_recovery": "structural",
    "structurally_exhausted":      "critical",
}

_RECOVERY_HORIZONS: dict[str, str] = {
    "structurally_recoverable":    "near_term",
    "recoverable_with_adaptation": "medium_term",
    "constrained_recovery":        "extended",
    "restructuring_dependent":     "uncertain",
    "continuity_without_recovery": "distant",
    "structurally_exhausted":      "indeterminate",
}

_RECOVERY_WINDOWS: dict[str, Optional[int]] = {
    "structurally_recoverable":    7,
    "recoverable_with_adaptation": 30,
    "constrained_recovery":        90,
    "restructuring_dependent":     None,
    "continuity_without_recovery": None,
    "structurally_exhausted":      None,
}

_REVERSIBILITY_INDICES: dict[str, float] = {
    "structurally_recoverable":    0.95,
    "recoverable_with_adaptation": 0.75,
    "constrained_recovery":        0.50,
    "restructuring_dependent":     0.30,
    "continuity_without_recovery": 0.15,
    "structurally_exhausted":      0.05,
}


@dataclass
class StructuralRecoveryCapacity:
    recovery_state:                 str
    structural_recoverability:      str
    recovery_elasticity:            str
    restructuring_requirement:      str
    continuity_dependence:          str
    structural_recovery_horizon:    str
    recovery_window_days:           Optional[int]
    structural_reversibility_index: float
    recovery_capacity_note:         str
    recovery_capacity_confidence:   int


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
    inertia_state:         Optional[str],
) -> str:
    # ── structurally_exhausted (5) ───────────────────────────────────────────────
    if (
        inertia_state == "institutional_freeze"
        and n_resilience_degraded >= 1
        and energy_state == "structurally_exhausting"
        and n_failed_outcomes >= 2
    ) or (
        inertia_state in ("institutional_freeze", "locked_operational_behavior")
        and n_compounding >= 1
        and n_obs_reset >= 2
        and n_cascade_systemic >= 1
    ) or (
        doctrine_state == "rigid_operational_doctrine"
        and n_recurring >= 3
        and n_failed_outcomes >= 2
        and n_resilience_degraded >= 1
        and n_cf_locked >= 1
    ):
        return "structurally_exhausted"

    # ── continuity_without_recovery (4) ──────────────────────────────────────────
    if (
        inertia_state in ("locked_operational_behavior", "institutional_freeze")
        and n_resilience_degraded >= 1
        and n_failed_outcomes >= 2
    ) or (
        n_compounding >= 1
        and n_resilience_degraded >= 1
        and topology_state in ("collapsing_compensation", "structurally_unbalanced")
        and n_recurring >= 2
    ) or (
        doctrine_state in ("rigid_operational_doctrine", "structurally_embedded_doctrine")
        and n_rigid_adapt >= 2
        and n_lock_waiting >= 2
        and n_failed_outcomes >= 1
    ):
        return "continuity_without_recovery"

    # ── restructuring_dependent (3) ──────────────────────────────────────────────
    if (
        inertia_state in ("structural_inertia", "locked_operational_behavior")
        and n_cf_locked >= 1
        and n_recurring >= 2
    ) or (
        n_compounding >= 1
        and n_fragmented_obs >= 1
        and n_failed_outcomes >= 1
        and n_recurring >= 2
    ) or (
        doctrine_state in ("structurally_embedded_doctrine", "stabilization_dependency")
        and topology_state in ("structurally_unbalanced", "collapsing_compensation", "narrowing_support")
        and n_lock_waiting >= 2
    ):
        return "restructuring_dependent"

    # ── constrained_recovery (2) ─────────────────────────────────────────────────
    if (
        inertia_state in ("operational_hardening", "structural_inertia")
        and n_narrowing_res >= 1
        and n_recurring >= 2
    ) or (
        n_lock_waiting >= 1
        and n_recurring >= 2
        and (n_rigid_adapt >= 1 or n_plateauing_adapt >= 1)
        and n_fragmented_obs >= 1
    ) or (
        doctrine_state in ("defensive_patterning", "stabilization_dependency")
        and n_narrowing_res >= 1
        and n_recurring >= 1
    ):
        return "constrained_recovery"

    # ── recoverable_with_adaptation (1) ──────────────────────────────────────────
    if (
        inertia_state == "adaptive_inertia"
        and n_recurring >= 1
        and n_rigid_adapt == 0
    ) or (
        n_recurring >= 1
        and n_resilient >= 1
        and n_cf_locked == 0
        and n_failed_outcomes == 0
        and n_compounding == 0
    ) or (
        n_recurring >= 2
        and n_lock_waiting <= 1
        and n_fragmented_obs == 0
        and doctrine_state in ("recurring_operational_bias", "adaptive_execution")
    ):
        return "recoverable_with_adaptation"

    # ── structurally_recoverable (0, default) ────────────────────────────────────
    return "structurally_recoverable"


def _compute_confidence(
    n_recurring:                    int,
    n_compounding:                  int,
    n_fragmented_obs:               int,
    n_obs_reset:                    int,
    n_resilient:                    int,
    n_strengthening:                int,
    n_rigid_adapt:                  int,
    n_failed_outcomes:              int,
    n_isolated:                     int,
    n_stale:                        int,
    n_fresh:                        int,
    has_aligned_structural_signals: bool,
    has_hist_recovery_continuity:   bool,
    has_obs_stability:              bool,
    has_adaptive_coherence:         bool,
) -> int:
    score = 70
    if has_aligned_structural_signals:
        score += 10
    if has_hist_recovery_continuity:
        score += 10
    if has_obs_stability:
        score += 8
    if has_adaptive_coherence:
        score += 6
    if n_fragmented_obs >= 2:
        score -= 10
    if (n_resilient >= 1 and n_rigid_adapt >= 1) or (n_strengthening >= 1 and n_failed_outcomes >= 1):
        score -= 8  # contradictory recovery indicators
    if n_isolated >= 2 and n_recurring < 1:
        score -= 8  # isolated short-term instability
    if n_fragmented_obs >= 1 and n_obs_reset >= 1:
        score -= 6  # low observability quality
    if n_strengthening >= 1 and n_rigid_adapt >= 1:
        score -= 6  # conflicting recovery trajectory signals
    if n_stale >= 1 and n_fresh >= 1:
        score -= 5  # temporal inconsistency
    return max(56, min(98, score))


def compute_structural_recovery_capacity(
    insights:       list,
    regime:         Optional[str] = None,
    phase:          Optional[str] = None,
    topology_state: Optional[str] = None,
    energy_state:   Optional[str] = None,
    doctrine_state: Optional[str] = None,
    inertia_state:  Optional[str] = None,
) -> StructuralRecoveryCapacity:
    """
    Evaluate whether structural recovery is architecturally possible without redesign.
    Strict ordinal scale 0→5: full recoverability → structural exhaustion.
    Priority: structurally_exhausted → continuity_without_recovery → restructuring_dependent
              → constrained_recovery → recoverable_with_adaptation → structurally_recoverable.
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
    n_lock_waiting        = _c(active,    "stabilization_lock_state", ("waiting", "locked"))
    n_obs_reset           = _c(active,    "obs_recovery_state",       "reset_required")
    n_resilient           = _c(active,    "resilience_state",         ("adaptive", "resilient"))
    n_strengthening       = _c(active,    "adaptive_capacity_state",  "strengthening")
    n_plateauing_adapt    = _c(active,    "adaptive_capacity_state",  "plateauing")
    n_resilience_degraded = _c(active,    "resilience_state",         ("exhausted", "collapsing"))
    n_cascade_systemic    = _c(active,    "cascade_state",            "structurally_cascading")
    n_isolated            = _c(active,    "cascade_state",            "isolated")
    n_stale               = _c(active,    "signal_decay_state",       "stale")
    n_fresh               = _c(active,    "signal_decay_state",       "fresh")

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
        inertia_state=inertia_state,
    )

    # Confidence signal checks
    _high_severity_doctrine  = doctrine_state in ("structurally_embedded_doctrine", "rigid_operational_doctrine", "stabilization_dependency")
    _high_severity_inertia   = inertia_state  in ("structural_inertia", "locked_operational_behavior", "institutional_freeze")
    _high_severity_topology  = topology_state in ("structurally_unbalanced", "collapsing_compensation")
    _low_severity_doctrine   = doctrine_state in ("adaptive_execution", "recurring_operational_bias")
    _low_severity_inertia    = inertia_state  in ("flexible_structure", "adaptive_inertia")
    _low_severity_topology   = topology_state in ("balanced_stability", "compensating_structure")

    has_aligned_structural_signals = (
        (_high_severity_doctrine and _high_severity_inertia and _high_severity_topology)
        or (_low_severity_doctrine and _low_severity_inertia and _low_severity_topology)
    )
    has_hist_recovery_continuity = (
        n_compounding == 0 and n_failed_outcomes == 0
    ) or n_compounding >= 1  # consistent pattern in either direction
    has_obs_stability = n_fragmented_obs == 0 and n_obs_reset == 0
    has_adaptive_coherence = (
        (n_strengthening >= 1 and n_rigid_adapt == 0)
        or (n_resilient >= 1 and n_compounding == 0)
    )

    conf = _compute_confidence(
        n_recurring=n_recurring,
        n_compounding=n_compounding,
        n_fragmented_obs=n_fragmented_obs,
        n_obs_reset=n_obs_reset,
        n_resilient=n_resilient,
        n_strengthening=n_strengthening,
        n_rigid_adapt=n_rigid_adapt,
        n_failed_outcomes=n_failed_outcomes,
        n_isolated=n_isolated,
        n_stale=n_stale,
        n_fresh=n_fresh,
        has_aligned_structural_signals=has_aligned_structural_signals,
        has_hist_recovery_continuity=has_hist_recovery_continuity,
        has_obs_stability=has_obs_stability,
        has_adaptive_coherence=has_adaptive_coherence,
    )

    return StructuralRecoveryCapacity(
        recovery_state=state,
        structural_recoverability=_STRUCTURAL_RECOVERABILITIES[state],
        recovery_elasticity=_RECOVERY_ELASTICITIES[state],
        restructuring_requirement=_RESTRUCTURING_REQUIREMENTS[state],
        continuity_dependence=_CONTINUITY_DEPENDENCES[state],
        structural_recovery_horizon=_RECOVERY_HORIZONS[state],
        recovery_window_days=_RECOVERY_WINDOWS[state],
        structural_reversibility_index=_REVERSIBILITY_INDICES[state],
        recovery_capacity_note=_RECOVERY_NOTES[state],
        recovery_capacity_confidence=conf,
    )
