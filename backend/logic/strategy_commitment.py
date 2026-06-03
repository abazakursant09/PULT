"""
Strategy Commitment Tracking — Sprint 43.
Infers the operator's stabilization strategy from operational signals and tracks
commitment consistency. Never tracks individual actions — tracks strategic coherence.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


@dataclass
class StrategyShift:
    previous_strategy: str
    current_strategy:  str
    shift_type:        str   # escalation | fragmentation | structural_shift | tactical_switch
    shift_note:        Optional[str]


@dataclass
class StrategyCommitment:
    strategy_type:                   str   # structural_margin_recovery | advertising_stabilization | seo_recovery | volatility_reduction | growth_scaling | inventory_stabilization | mixed_fragmented_strategy
    commitment_state:                str   # emerging | active | stabilizing | fragmented | abandoned
    interruption_risk:               str   # low | moderate | high
    observability_quality:           str   # clear | sufficient | degraded | unclear
    commitment_score:                Optional[int]   # 0-100
    commitment_note:                 Optional[str]
    estimated_observation_window_days: Optional[int]
    strategy_shift:                  Optional[StrategyShift] = None


# ── Category sets ─────────────────────────────────────────────────────────────

_PRESSURE_CATS  = {"margin_crisis", "high_ad_spend", "seo_opportunity", "low_stock"}
_MARGIN_CATS    = {"margin_crisis", "high_ad_spend"}
_STRUCTURAL_REC = {"structural"}


# ── State score deltas ────────────────────────────────────────────────────────

_STATE_DELTA = {
    "stabilizing": +15,
    "active":      +8,
    "emerging":    0,
    "fragmented":  -25,
    "abandoned":   -30,
}

_TYPE_DELTA = {
    "structural_margin_recovery": +5,
    "inventory_stabilization":    +5,
    "mixed_fragmented_strategy":  -10,
}


# ── Narratives ────────────────────────────────────────────────────────────────

_STRATEGY_NOTES: dict[str, str] = {
    "structural_margin_recovery": "Текущий stabilization cycle ориентирован на структурное восстановление unit-экономики.",
    "advertising_stabilization":  "Текущий stabilization cycle сфокусирован на стабилизации рекламной нагрузки.",
    "seo_recovery":               "Текущий stabilization cycle направлен на восстановление SEO-позиций.",
    "volatility_reduction":       "Текущий stabilization cycle ориентирован на снижение операционной волатильности.",
    "growth_scaling":             "Текущий stabilization cycle поддерживает масштабирование роста.",
    "inventory_stabilization":    "Текущий stabilization cycle направлен на стабилизацию складских показателей.",
    "mixed_fragmented_strategy":  "Частая смена stabilization path может временно снижать прозрачность recovery cycle.",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cat(insight) -> str:
    key = getattr(insight, "key", "") or ""
    return key.split(":")[0].replace("demo_", "")


def _detect_strategy(active: list) -> str:
    cats: set[str] = {_cat(i) for i in active}
    recurring_cats: set[str] = {
        _cat(i) for i in active
        if getattr(i, "signal_lifecycle_stage", None) == "recurring"
    }
    structural_count = sum(
        1 for i in active
        if getattr(i, "recovery_state", None) in _STRUCTURAL_REC
    )

    pressure_active = cats & _PRESSURE_CATS
    if len(pressure_active) >= 3:
        return "mixed_fragmented_strategy"

    has_margin  = "margin_crisis"    in cats
    has_ad      = "high_ad_spend"    in cats
    has_seo     = "seo_opportunity"  in cats
    has_growth  = "sales_growth"     in cats
    has_stock   = "low_stock"        in cats

    if has_margin and (structural_count >= 1 or "margin_crisis" in recurring_cats):
        return "structural_margin_recovery"

    if has_margin and has_ad:
        return "volatility_reduction"

    if has_ad:
        return "advertising_stabilization"

    if has_seo:
        return "seo_recovery"

    if has_growth:
        return "growth_scaling"

    if has_stock:
        return "inventory_stabilization"

    return "mixed_fragmented_strategy"


def _detect_commitment_state(active: list, strategy_type: str) -> str:
    if strategy_type == "mixed_fragmented_strategy":
        return "fragmented"

    total       = len(active)
    stale_count = sum(1 for i in active if getattr(i, "signal_decay_state", None) in ("stale", "fading"))
    if total > 0 and stale_count > total / 2:
        return "abandoned"

    recurring_count  = sum(1 for i in active if getattr(i, "signal_lifecycle_stage", None) == "recurring")
    improving_count  = sum(1 for i in active if getattr(i, "trajectory_direction", None) in ("improving", "stable"))
    worsening_count  = sum(1 for i in active if getattr(i, "trajectory_direction", None) in ("worsening", "critical"))
    locked_count     = sum(1 for i in active if getattr(i, "recovery_signal_state", None) in ("waiting", "stabilizing"))
    escalating_count = sum(1 for i in active if getattr(i, "trajectory_state", None) in ("escalating", "structurally_accumulating"))

    if improving_count >= 2 and locked_count <= 1 and worsening_count == 0:
        return "stabilizing"

    if recurring_count >= 1 and escalating_count <= 1 and improving_count >= 1:
        return "active"

    return "emerging"


def _detect_interruption_risk(active: list, commitment_state: str) -> str:
    if commitment_state in ("fragmented", "abandoned"):
        return "high"

    waiting_locks    = sum(1 for i in active if getattr(i, "recovery_signal_state", None) == "waiting")
    escalating_count = sum(1 for i in active if getattr(i, "trajectory_state", None) in ("escalating", "structurally_accumulating"))

    if waiting_locks >= 2 or escalating_count >= 2:
        return "moderate"

    if commitment_state in ("stabilizing", "active"):
        return "low"

    return "moderate"


def _detect_observability(active: list, commitment_state: str) -> str:
    if commitment_state == "fragmented":
        return "degraded"
    if commitment_state == "abandoned":
        return "unclear"

    obs_reduced = sum(
        1 for i in active
        if getattr(i, "path_comparison", None) is not None
        and getattr(getattr(i, "path_comparison", None), "comparison_dimension", "") == "observability"
    )
    waiting = sum(1 for i in active if getattr(i, "recovery_signal_state", None) == "waiting")

    if obs_reduced >= 1 and waiting >= 1:
        return "degraded"
    if commitment_state == "stabilizing":
        return "clear"
    return "sufficient"


def _compute_score(commitment_state: str, strategy_type: str, active: list) -> int:
    score = 70
    score += _STATE_DELTA.get(commitment_state, 0)
    score += _TYPE_DELTA.get(strategy_type, 0)

    recurring = sum(1 for i in active if getattr(i, "signal_lifecycle_stage", None) == "recurring")
    score -= recurring * 5

    improving = sum(1 for i in active if getattr(i, "trajectory_direction", None) == "improving")
    score += improving * 5

    return max(0, min(100, score))


def _estimate_window(active: list) -> Optional[int]:
    windows = [
        getattr(i, "lock_estimated_recovery_window_days", None)
        for i in active
        if getattr(i, "recovery_signal_state", None) in ("waiting", "stabilizing")
    ]
    valid = [w for w in windows if w is not None]
    return max(valid) if valid else None


# ── Public API ────────────────────────────────────────────────────────────────

def compute_strategy_commitment(
    insights: list,
    portfolio_patterns: list,
) -> StrategyCommitment:
    active = [
        i for i in insights
        if getattr(i, "status", "") not in ("resolved", "dismissed")
    ]

    strategy_type      = _detect_strategy(active)
    commitment_state   = _detect_commitment_state(active, strategy_type)
    interruption_risk  = _detect_interruption_risk(active, commitment_state)
    observability      = _detect_observability(active, commitment_state)
    score              = _compute_score(commitment_state, strategy_type, active)
    note               = _STRATEGY_NOTES.get(strategy_type)
    window             = _estimate_window(active)

    return StrategyCommitment(
        strategy_type=strategy_type,
        commitment_state=commitment_state,
        interruption_risk=interruption_risk,
        observability_quality=observability,
        commitment_score=score,
        commitment_note=note,
        estimated_observation_window_days=window,
        strategy_shift=None,  # requires historical tracking; reserved for future persistence
    )
