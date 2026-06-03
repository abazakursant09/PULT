"""
Operational Regime Engine — Sprint 55.
Determines the systemic operating mode of the portfolio.
The same signal carries different operational risk inside different regimes.
NOT strategic consulting. NOT executive coaching. Operational systems intelligence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Regime → operational_posture
_POSTURES: dict[str, str] = {
    "expansion":           "expansion_tolerant",
    "stabilization":       "equilibrium_focused",
    "defensive":           "preservation_oriented",
    "constrained":         "flexibility_constrained",
    "containment":         "deterioration_containment",
    "recovery_transition": "recovery_rebuilding",
}

# Regime → intervention_tolerance
_TOLERANCES: dict[str, str] = {
    "expansion":           "high",
    "stabilization":       "moderate",
    "defensive":           "selective",
    "constrained":         "narrow",
    "containment":         "minimal",
    "recovery_transition": "moderate",
}

# Regime → regime_note
_REGIME_NOTES: dict[str, str] = {
    "expansion":
        "Система сохраняет пространство для управляемого масштабирования без выраженного операционного давления.",
    "stabilization":
        "Текущий operational regime ориентирован на локальную стабилизацию отдельных зон давления.",
    "defensive":
        "Система постепенно смещается в режим selective stabilization и защиты операционной устойчивости.",
    "constrained":
        "Часть операционных решений сейчас ограничена сниженной наблюдаемостью и сужением пространства стабилизации.",
    "containment":
        "Текущий operational regime всё больше ориентирован на удержание структурного давления, а не на восстановление роста.",
    "recovery_transition":
        "Система постепенно выходит из режима защитной стабилизации и восстанавливает операционную гибкость.",
}

# Regime → resilience_context
_RESILIENCE_CONTEXTS: dict[str, str] = {
    "expansion":
        "Операционная устойчивость обеспечивает пространство для управляемого роста.",
    "stabilization":
        "Локальная нестабильность управляема в рамках текущего операционного ресурса.",
    "defensive":
        "Операционная устойчивость сохраняется, но пространство для манёвра сужается.",
    "constrained":
        "Наблюдаемость снижает способность системы к точной стабилизации.",
    "containment":
        "Структурная деградация фиксируется в нескольких операционных слоях.",
    "recovery_transition":
        "Система постепенно восстанавливает операционную гибкость после периода давления.",
}

# Regime → weight multiplier (applied to recurring non-stale non-isolated insights)
REGIME_WEIGHT_MULTIPLIER: dict[str, float] = {
    "expansion":           0.92,
    "stabilization":       1.00,
    "defensive":           1.08,
    "constrained":         1.14,
    "containment":         1.22,
    "recovery_transition": 0.96,
}


@dataclass
class OperationalRegime:
    regime:                 str
    regime_direction:       str
    operational_posture:    str
    resilience_context:     str
    intervention_tolerance: str
    observability_quality:  str
    regime_note:            str
    regime_confidence:      int


class _Signals:
    """Aggregated portfolio signals used for regime classification."""
    def __init__(
        self,
        n_struct_degrading:    int,
        n_struct_cascading:    int,
        n_compounding_rep:     int,
        n_brittle_plus:        int,
        n_failed_det:          int,
        n_fragmented_obs:      int,
        n_struct_locked:       int,
        n_timing_degraded:     int,
        n_narrowing_res:       int,
        n_rigid_adapt:         int,
        n_coupled_cascade:     int,
        n_recovering_res:      int,
        n_strengthening:       int,
        n_resilient:           int,
        n_improving:           int,
        n_total_active:        int,
        n_total_recurring:     int,
        n_aligned_drift:       int,
        commitment_state:      Optional[str],
        capacity_state:        Optional[str],
    ) -> None:
        self.n_struct_degrading  = n_struct_degrading
        self.n_struct_cascading  = n_struct_cascading
        self.n_compounding_rep   = n_compounding_rep
        self.n_brittle_plus      = n_brittle_plus
        self.n_failed_det        = n_failed_det
        self.n_fragmented_obs    = n_fragmented_obs
        self.n_struct_locked     = n_struct_locked
        self.n_timing_degraded   = n_timing_degraded
        self.n_narrowing_res     = n_narrowing_res
        self.n_rigid_adapt       = n_rigid_adapt
        self.n_coupled_cascade   = n_coupled_cascade
        self.n_recovering_res    = n_recovering_res
        self.n_strengthening     = n_strengthening
        self.n_resilient         = n_resilient
        self.n_improving         = n_improving
        self.n_total_active      = n_total_active
        self.n_total_recurring   = n_total_recurring
        self.n_aligned_drift     = n_aligned_drift
        self.commitment_state    = commitment_state
        self.capacity_state      = capacity_state


def _classify_regime(s: _Signals) -> tuple[str, str]:
    """Returns (regime, regime_direction)."""
    # ── containment (highest priority) ────────────────────────────────────────
    if (
        s.n_struct_degrading >= 1
        or (s.n_struct_cascading >= 1 and s.n_compounding_rep >= 1)
        or s.n_brittle_plus >= 3
        or (s.n_failed_det >= 1 and s.n_compounding_rep >= 1)
        or (s.n_brittle_plus >= 2 and s.n_struct_cascading >= 1)
    ):
        direction = "structurally_accumulating" if s.n_struct_degrading >= 1 else "deteriorating"
        return "containment", direction

    # ── constrained ───────────────────────────────────────────────────────────
    if (
        s.n_fragmented_obs >= 2
        or (s.n_struct_locked >= 2)
        or (s.n_timing_degraded >= 2 and s.n_fragmented_obs >= 1)
        or s.capacity_state in ("overloaded",) and s.n_fragmented_obs >= 1
    ):
        return "constrained", "constrained"

    # ── defensive ─────────────────────────────────────────────────────────────
    if (
        (s.n_narrowing_res >= 2 and s.n_rigid_adapt >= 1)
        or (s.n_coupled_cascade >= 1 and s.n_timing_degraded >= 1)
        or s.n_narrowing_res >= 3
        or s.n_brittle_plus >= 2
        or (s.n_rigid_adapt >= 2 and s.n_coupled_cascade >= 1)
        or s.commitment_state in ("fragmented", "abandoned")
    ):
        return "defensive", "deteriorating"

    # ── recovery_transition ───────────────────────────────────────────────────
    if (
        s.n_recovering_res >= 1
        and s.n_strengthening >= 1
        and s.n_struct_degrading == 0
        and s.n_brittle_plus <= 1
    ):
        return "recovery_transition", "recovering"

    # ── expansion ─────────────────────────────────────────────────────────────
    majority = max(1, s.n_total_active // 2)
    if (
        s.n_resilient >= majority
        and s.n_improving >= 1
        and s.n_coupled_cascade == 0
        and s.n_narrowing_res == 0
        and s.n_fragmented_obs == 0
    ):
        return "expansion", "stabilizing"

    # ── stabilization (default) ───────────────────────────────────────────────
    return "stabilization", "stabilizing"


def _obs_quality(n_fragmented: int, n_total: int) -> str:
    if n_fragmented >= 3 or (n_total > 0 and n_fragmented / n_total >= 0.5):
        return "fragmented"
    if n_fragmented >= 2:
        return "degraded"
    if n_fragmented == 1:
        return "moderate"
    return "strong"


def _regime_confidence(s: _Signals, regime: str) -> int:
    score = 58

    # Aligned systemic signals
    if regime == "containment":
        n_aligned = s.n_struct_degrading + s.n_brittle_plus + s.n_compounding_rep
    elif regime == "defensive":
        n_aligned = s.n_narrowing_res + s.n_rigid_adapt + s.n_coupled_cascade
    elif regime == "recovery_transition":
        n_aligned = s.n_recovering_res + s.n_strengthening + s.n_improving
    elif regime == "expansion":
        n_aligned = s.n_resilient + s.n_improving
    else:
        n_aligned = 1
    if n_aligned >= 3:
        score += 10

    if s.n_total_recurring >= 2:
        score += 8
    if s.n_aligned_drift >= 2:
        score += 8
    if s.commitment_state in ("active", "stabilizing"):
        score += 6
    if s.n_fragmented_obs >= 2:
        score -= 12
    if s.n_total_recurring < 2:
        score -= 10
    # Conflicting signals
    if s.n_recovering_res >= 1 and s.n_struct_degrading >= 1:
        score -= 8

    return max(42, min(92, score))


def compute_operational_regime(
    insights:         list,       # list[InsightItem] — enriched through Sprint 54
    commitment_state: Optional[str] = None,
    capacity_state:   Optional[str] = None,
) -> OperationalRegime:
    """
    Aggregate portfolio signals to determine the systemic operating mode.
    Priority: containment → constrained → defensive → recovery_transition → stabilization → expansion.
    """
    active    = [i for i in insights if getattr(i, "status", "") not in ("resolved", "dismissed")]
    recurring = [
        i for i in active
        if getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
    ]

    def _c(lst, attr, vals):
        return sum(1 for i in lst if getattr(i, attr, None) in (vals if isinstance(vals, (list, tuple)) else (vals,)))

    s = _Signals(
        n_struct_degrading = _c(recurring, "resilience_trajectory",  "structurally_degrading"),
        n_struct_cascading  = _c(recurring, "cascade_state",          "structurally_cascading"),
        n_compounding_rep   = _c(active,    "strategic_drift_state",  "compounding_repetition"),
        n_brittle_plus      = _c(active,    "resilience_state",       ("brittle", "collapsing", "exhausted")),
        n_failed_det        = sum(
            1 for i in active
            if getattr(i, "outcome_state", None) in ("failed", "repeated")
            and getattr(i, "adaptive_capacity_state", None) == "deteriorating"
        ),
        n_fragmented_obs    = _c(active,    "obs_recovery_state",     ("fragmented", "reset_required")),
        n_struct_locked     = sum(
            1 for i in active
            if getattr(i, "reversibility_state", None) == "structurally_locked"
            or getattr(i, "reversal_state", None) == "structurally_locked"
        ),
        n_timing_degraded   = _c(recurring, "timing_state",           "structurally_late"),
        n_narrowing_res     = _c(recurring, "resilience_state",       ("narrowing", "brittle")),
        n_rigid_adapt       = _c(recurring, "adaptive_capacity_state", ("rigid", "deteriorating")),
        n_coupled_cascade   = _c(active,    "cascade_state",          ("coupled_instability", "structurally_cascading")),
        n_recovering_res    = _c(recurring, "resilience_trajectory",  "recovering"),
        n_strengthening     = _c(active,    "adaptive_capacity_state", "strengthening"),
        n_resilient         = _c(active,    "resilience_state",       ("adaptive", "resilient")),
        n_improving         = _c(active,    "trajectory_direction",   "improving"),
        n_total_active      = len(active),
        n_total_recurring   = len(recurring),
        n_aligned_drift     = _c(active,    "strategic_drift_state",  "aligned"),
        commitment_state    = commitment_state,
        capacity_state      = capacity_state,
    )

    regime, direction = _classify_regime(s)
    obs_q = _obs_quality(s.n_fragmented_obs, len(active) or 1)
    conf  = _regime_confidence(s, regime)

    return OperationalRegime(
        regime=regime,
        regime_direction=direction,
        operational_posture=_POSTURES[regime],
        resilience_context=_RESILIENCE_CONTEXTS[regime],
        intervention_tolerance=_TOLERANCES[regime],
        observability_quality=obs_q,
        regime_note=_REGIME_NOTES[regime],
        regime_confidence=conf,
    )
