"""
Operational Doctrine Formation — Sprint 59.
Identifies when recurring operational behaviors stop being temporary adaptations
and start institutionalizing as persistent execution doctrine.
NOT regime. NOT trajectory. NOT topology.
Behavioral institutionalization: operational systems intelligence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DOCTRINE_WEIGHT_MULTIPLIER: dict[str, float] = {
    "adaptive_execution":           0.94,
    "recurring_operational_bias":   1.03,
    "defensive_patterning":         1.10,
    "stabilization_dependency":     1.18,
    "structurally_embedded_doctrine": 1.28,
    "rigid_operational_doctrine":   1.38,
}

_DOCTRINE_NOTES: dict[str, str] = {
    "adaptive_execution":
        "Operational behavior сохраняет достаточную гибкость и не демонстрирует признаков rigid execution repetition.",
    "recurring_operational_bias":
        "Некоторые stabilization approaches начинают постепенно повторяться как preferred operational response.",
    "defensive_patterning":
        "Система постепенно закрепляет более defensive operational responses как recurring execution pattern.",
    "stabilization_dependency":
        "Operational system increasingly зависит от повторяющихся stabilization cycles для поддержания continuity.",
    "structurally_embedded_doctrine":
        "Часть operational responses начинает закрепляться как устойчивый structural execution doctrine.",
    "rigid_operational_doctrine":
        "Operational behavior increasingly повторяется независимо от исходного signal source, снижая adaptive flexibility.",
}

_DOCTRINE_PATTERNS: dict[str, str] = {
    "adaptive_execution":             "diversified execution",
    "recurring_operational_bias":     "preferred response repetition",
    "defensive_patterning":           "defensive execution",
    "stabilization_dependency":       "stabilization cycle dependency",
    "structurally_embedded_doctrine": "structural execution doctrine",
    "rigid_operational_doctrine":     "source-independent repetition",
}

_ADAPTATION_MODES: dict[str, str] = {
    "adaptive_execution":             "adaptive",
    "recurring_operational_bias":     "biased",
    "defensive_patterning":           "protective",
    "stabilization_dependency":       "dependency_driven",
    "structurally_embedded_doctrine": "structurally_constrained",
    "rigid_operational_doctrine":     "rigidly_fixed",
}

_INSTITUTIONALIZATION_LEVELS: dict[str, str] = {
    "adaptive_execution":             "none",
    "recurring_operational_bias":     "emerging",
    "defensive_patterning":           "moderate",
    "stabilization_dependency":       "established",
    "structurally_embedded_doctrine": "embedded",
    "rigid_operational_doctrine":     "rigid",
}

_DOCTRINE_FLEXIBILITIES: dict[str, str] = {
    "adaptive_execution":             "high",
    "recurring_operational_bias":     "moderate",
    "defensive_patterning":           "narrowing",
    "stabilization_dependency":       "limited",
    "structurally_embedded_doctrine": "low",
    "rigid_operational_doctrine":     "minimal",
}


@dataclass
class OperationalDoctrine:
    doctrine_state:            str   # adaptive_execution | recurring_operational_bias | defensive_patterning | stabilization_dependency | structurally_embedded_doctrine | rigid_operational_doctrine
    doctrine_pattern:          str
    adaptation_mode:           str
    institutionalization_level: str
    doctrine_flexibility:      str
    doctrine_note:             str
    doctrine_confidence:       int


def _classify(
    n_recurring:          int,
    n_compounding:        int,
    n_fragmented_obs:     int,
    n_failed_outcomes:    int,
    n_cf_locked:          int,
    n_rigid_adapt:        int,
    n_structurally_acc:   int,
    n_narrowing_res:      int,
    n_timing_compressed:  int,
    n_lock_waiting:       int,
    n_reversal_recurring: int,
    n_obs_reset:          int,
    n_drift_moderate:     int,
    n_resilient:          int,
    n_strengthening:      int,
    n_total_active:       int,
    regime:               Optional[str],
    phase:                Optional[str],
    topology_state:       Optional[str],
    energy_state:         Optional[str],
) -> str:
    # ── rigid_operational_doctrine (highest severity) ──────────────────────────
    if (
        n_rigid_adapt >= 1
        and n_failed_outcomes >= 2
        and n_compounding >= 1
        and n_cf_locked >= 1
    ) or (
        n_rigid_adapt >= 2
        and n_compounding >= 1
        and (phase == "constrained_operation" or regime == "containment")
        and n_failed_outcomes >= 1
    ):
        return "rigid_operational_doctrine"

    # ── structurally_embedded_doctrine ────────────────────────────────────────
    if (
        n_structurally_acc >= 1
        and n_compounding >= 1
        and n_rigid_adapt >= 1
        and n_recurring >= 2
    ) or (
        n_compounding >= 1
        and topology_state in ("structurally_unbalanced", "collapsing_compensation")
        and n_recurring >= 2
    ) or (
        n_compounding >= 2
        and n_recurring >= 3
    ):
        return "structurally_embedded_doctrine"

    # ── stabilization_dependency ──────────────────────────────────────────────
    if (
        n_recurring >= 2
        and n_lock_waiting >= 2
    ) or (
        n_recurring >= 2
        and n_reversal_recurring >= 1
        and n_obs_reset >= 1
    ) or (
        n_fragmented_obs >= 1
        and n_recurring >= 2
        and n_lock_waiting >= 1
    ) or (
        n_recurring >= 3
        and n_timing_compressed >= 2
    ):
        return "stabilization_dependency"

    # ── defensive_patterning ──────────────────────────────────────────────────
    if (
        regime in ("defensive", "constrained", "containment")
        and n_recurring >= 2
        and n_narrowing_res >= 1
    ) or (
        n_timing_compressed >= 2
        and n_recurring >= 2
        and n_narrowing_res >= 1
    ) or (
        regime in ("defensive", "constrained")
        and n_rigid_adapt >= 1
        and n_recurring >= 1
    ):
        return "defensive_patterning"

    # ── recurring_operational_bias ─────────────────────────────────────────────
    if (
        n_drift_moderate >= 1
        and n_recurring >= 2
    ) or (
        n_recurring >= 2
        and n_fragmented_obs == 0
        and n_compounding == 0
    ) or (
        n_recurring >= 1
        and n_structurally_acc >= 1
    ):
        return "recurring_operational_bias"

    # ── adaptive_execution (default) ──────────────────────────────────────────
    return "adaptive_execution"


def _compute_confidence(
    n_recurring:          int,
    n_compounding:        int,
    n_regime_mem_align:   int,
    n_cross_category:     int,
    has_hist_persistence: bool,
    n_fragmented_obs:     int,
    n_isolated:           int,
    n_conflict_recovery:  int,
) -> int:
    score = 66
    if n_compounding >= 2 or n_recurring >= 3:
        score += 10
    if n_regime_mem_align >= 2:
        score += 8
    if n_cross_category >= 2:
        score += 8
    if has_hist_persistence:
        score += 6
    if n_fragmented_obs >= 2:
        score -= 10
    if n_isolated >= 2 and n_recurring < 1:
        score -= 8
    if n_conflict_recovery >= 1:
        score -= 6
    return max(52, min(96, score))


def compute_operational_doctrine(
    insights:       list,           # list[InsightItem] enriched through Sprint 58
    regime:         Optional[str]  = None,
    phase:          Optional[str]  = None,
    topology_state: Optional[str]  = None,
    energy_state:   Optional[str]  = None,
) -> OperationalDoctrine:
    """
    Detect whether recurring operational behaviors are institutionalizing as doctrine.
    Priority: rigid_operational_doctrine → structurally_embedded_doctrine → stabilization_dependency
              → defensive_patterning → recurring_operational_bias → adaptive_execution.
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
    n_compounding      = _c(active,    "strategic_drift_state",       "compounding_repetition")
    n_fragmented_obs   = _c(active,    "obs_recovery_state",          ("fragmented", "reset_required", "distorted"))
    n_failed_outcomes  = _c(recurring, "outcome_state",               ("failed", "repeated"))
    n_cf_locked        = sum(
        1 for i in active
        if getattr(i, "counterfactual_pressure_state", None) == "structurally_locked"
        or getattr(i, "reversibility_state", None) == "structurally_locked"
    )
    n_rigid_adapt      = _c(recurring, "adaptive_capacity_state",     ("rigid", "deteriorating"))
    n_structurally_acc = _c(recurring, "trajectory_state",            "structurally_accumulating")
    n_narrowing_res    = _c(recurring, "resilience_state",            ("narrowing", "brittle"))
    n_timing_compressed = _c(recurring, "timing_state",               ("narrowing_window", "structurally_late"))
    n_lock_waiting     = _c(active,    "stabilization_lock_state",    ("waiting", "locked"))
    n_reversal_recurring = _c(recurring, "reversal_state",            ("diminishing_return", "overextended"))
    n_obs_reset        = _c(active,    "obs_recovery_state",          "reset_required")
    n_drift_moderate   = _c(active,    "strategic_drift_state",       ("drifting", "fragmented"))
    n_resilient        = _c(active,    "resilience_state",            ("adaptive", "resilient"))
    n_strengthening    = _c(active,    "adaptive_capacity_state",     "strengthening")

    state = _classify(
        n_recurring=n_recurring,
        n_compounding=n_compounding,
        n_fragmented_obs=n_fragmented_obs,
        n_failed_outcomes=n_failed_outcomes,
        n_cf_locked=n_cf_locked,
        n_rigid_adapt=n_rigid_adapt,
        n_structurally_acc=n_structurally_acc,
        n_narrowing_res=n_narrowing_res,
        n_timing_compressed=n_timing_compressed,
        n_lock_waiting=n_lock_waiting,
        n_reversal_recurring=n_reversal_recurring,
        n_obs_reset=n_obs_reset,
        n_drift_moderate=n_drift_moderate,
        n_resilient=n_resilient,
        n_strengthening=n_strengthening,
        n_total_active=len(active),
        regime=regime,
        phase=phase,
        topology_state=topology_state,
        energy_state=energy_state,
    )

    # ── Confidence ─────────────────────────────────────────────────────────────
    n_regime_mem_align = 0
    if regime in ("containment", "constrained") and state in ("rigid_operational_doctrine", "structurally_embedded_doctrine"):
        n_regime_mem_align = 2
    elif _c(active, "strategic_drift_state", "compounding_repetition") >= 1 and state in ("structurally_embedded_doctrine", "stabilization_dependency"):
        n_regime_mem_align = 1

    # cross-category: recurring signals from different insight categories
    categories = set()
    for i in recurring:
        cat = getattr(i, "category", None) or (getattr(i, "key", "") or "").split(":")[0]
        if cat:
            categories.add(cat)
    n_cross_category = len(categories)

    has_hist_persistence = (
        n_compounding >= 1
        or _c(active, "historical_cycles", None) == 0  # fallback: compounding signals present
        or n_failed_outcomes >= 2
    )

    n_isolated       = _c(active, "cascade_state", "isolated")
    n_conflict_recovery = (
        _c(active, "reversal_state", "reversal_window")  # recovery opening conflicts with doctrine
        + _c(active, "adaptive_capacity_state", "strengthening")
    )

    conf = _compute_confidence(
        n_recurring=n_recurring,
        n_compounding=n_compounding,
        n_regime_mem_align=n_regime_mem_align,
        n_cross_category=n_cross_category,
        has_hist_persistence=has_hist_persistence,
        n_fragmented_obs=n_fragmented_obs,
        n_isolated=n_isolated,
        n_conflict_recovery=n_conflict_recovery,
    )

    return OperationalDoctrine(
        doctrine_state=state,
        doctrine_pattern=_DOCTRINE_PATTERNS[state],
        adaptation_mode=_ADAPTATION_MODES[state],
        institutionalization_level=_INSTITUTIONALIZATION_LEVELS[state],
        doctrine_flexibility=_DOCTRINE_FLEXIBILITIES[state],
        doctrine_note=_DOCTRINE_NOTES[state],
        doctrine_confidence=conf,
    )
