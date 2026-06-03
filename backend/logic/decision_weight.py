"""
Trust Calibration + Decision Weighting — Sprint 19.

Rule-based signal weighting. No ML. No fake probabilities.
Distinguishes: actionable instability / acceptable fluctuation / noise / structural drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


# ── Base weights per insight type ─────────────────────────────────────────────
_BASE: dict[str, float] = {
    "low_stock":       85.0,   # irreversible, direct revenue loss
    "high_ad_spend":   62.0,   # cascading, financial leakage
    "margin_crisis":   68.0,   # persistent pressure; varies by source
    "seo_opportunity": 42.0,   # reversible, experimentation safe
    "sales_growth":    38.0,   # opportunity, not threat
}

_RESOLUTION: dict[str, Literal["easy", "moderate", "hard"]] = {
    "low_stock":       "moderate",
    "high_ad_spend":   "moderate",
    "margin_crisis":   "hard",
    "seo_opportunity": "easy",
    "sales_growth":    "easy",
}

TierType = Literal["monitor", "background", "attention", "immediate"]
StateType = Literal["temporary", "persistent", "structural"]
DiffType  = Literal["easy", "moderate", "hard"]


@dataclass
class DecisionWeight:
    weight:                float
    signal_state:          StateType
    resolution_difficulty: DiffType
    intervention_tier:     TierType


# ── Helpers ───────────────────────────────────────────────────────────────────

def _state(days: int, pressure_source: str | None) -> StateType:
    if pressure_source == "structural" or days > 21:
        return "structural"
    if days >= 7:
        return "persistent"
    return "temporary"


def _tier(w: float) -> TierType:
    if w >= 76: return "immediate"
    if w >= 56: return "attention"
    if w >= 36: return "background"
    return "monitor"


def _cat(key: str) -> str:
    base = key.split(":")[0]
    return base[5:] if base.startswith("demo_") else base


# ── Core computation ──────────────────────────────────────────────────────────

def compute_weight(
    insight_type:       str,
    days_active:        int,
    confidence:         int,
    financial_impact:   float,
    pressure_source:    str | None,
    causal_depth:       int,          # 0=standalone, 1=part of chain
    operator_sensitivity: float,      # 0–1; 0.8=fast resolver, 0.2=habitual ignorer
    recurring:          bool,
) -> DecisionWeight:
    base = _BASE.get(insight_type, 50.0)
    w    = base

    # Days active
    if days_active > 28:  w += 12
    elif days_active > 14: w += 8
    elif days_active > 7:  w += 4

    # Confidence
    if confidence >= 85:   w += 5
    elif confidence < 60:  w -= 10
    elif confidence < 70:  w -= 5

    # Financial impact
    if financial_impact >= 20_000:   w += 15
    elif financial_impact >= 10_000: w += 8
    elif financial_impact >= 5_000:  w += 3

    # Recurrence (seen before)
    if recurring: w += 10

    # Part of a causal chain
    if causal_depth > 0: w += 8

    # Margin crisis: pressure source modifier
    if insight_type == "margin_crisis":
        if pressure_source == "structural":
            w += 12
        elif pressure_source == "logistics":
            w += 5

    # Operator sensitivity: fast-acting operator → slightly less urgency push needed
    if operator_sensitivity >= 0.8:
        w -= 4

    w = max(0.0, min(100.0, w))
    state = _state(days_active, pressure_source)

    diff: DiffType = _RESOLUTION.get(insight_type, "moderate")
    # margin_crisis driven by ads → moderate (fixable by ad audit)
    if insight_type == "margin_crisis" and pressure_source in ("ad_driven", None):
        diff = "moderate"

    return DecisionWeight(
        weight=round(w, 1),
        signal_state=state,
        resolution_difficulty=diff,
        intervention_tier=_tier(w),
    )


# ── Bulk application ──────────────────────────────────────────────────────────

def apply_decision_weights(
    insights:           list[Any],
    notif_counts:       dict[str, int],
    operator_profile:   Any,           # OperatorProfile duck-typed
) -> None:
    """
    Mutate each InsightItem in-place: set weight, signal_state,
    resolution_difficulty, intervention_tier.
    """
    for ins in insights:
        itype = _cat(getattr(ins, "key", "") or "")
        if not itype:
            continue

        meta             = getattr(ins, "sim_meta", None) or {}
        days_active      = meta.get("days_active", 0) if meta else 0
        financial        = float(getattr(ins, "estimated_monthly_loss_rub", None) or 0)
        causal_depth     = 1 if getattr(ins, "chain_id", None) else 0
        recurring        = (notif_counts or {}).get(itype, 0) >= 1
        pressure_source  = meta.get("pressure_source") if meta else None

        # Operator sensitivity from profile
        fast_cnt   = operator_profile.fast(itype)   if operator_profile else 0
        ignore_cnt = operator_profile.ignores(itype) if operator_profile else 0
        if fast_cnt >= 2:
            sensitivity = 0.8
        elif ignore_cnt >= 3:
            sensitivity = 0.2
        else:
            sensitivity = 0.5

        dw = compute_weight(
            insight_type=itype,
            days_active=days_active,
            confidence=getattr(ins, "confidence", 70) or 70,
            financial_impact=financial,
            pressure_source=pressure_source,
            causal_depth=causal_depth,
            operator_sensitivity=sensitivity,
            recurring=recurring,
        )

        ins.weight                = dw.weight
        ins.signal_state          = dw.signal_state
        ins.resolution_difficulty = dw.resolution_difficulty
        ins.intervention_tier     = dw.intervention_tier


# ── Fatigue ────────────────────────────────────────────────────────────────────

def compute_fatigue_score(
    unresolved_count:   int,
    alerts_last_7d:     int,
    ignored_categories: dict[str, int],
    focus_churn:        int,
) -> float:
    """
    0 = no fatigue, 1 = maximum operator fatigue.
    High fatigue → PULT becomes more selective, suppresses background noise.
    """
    score = 0.0

    if unresolved_count >= 8:   score += 0.30
    elif unresolved_count >= 5: score += 0.15

    if alerts_last_7d >= 15:    score += 0.30
    elif alerts_last_7d >= 8:   score += 0.15
    elif alerts_last_7d >= 4:   score += 0.05

    repeated = sum(1 for v in ignored_categories.values() if v >= 3)
    if repeated >= 2:   score += 0.25
    elif repeated >= 1: score += 0.10

    if focus_churn >= 4: score += 0.15

    return min(1.0, round(score, 3))


# ── Stability credit ──────────────────────────────────────────────────────────

def compute_stability_credit(
    resolved_count_90d:      int,
    crisis_recurrence_count: int,
    operational_age_days:    int,
) -> float:
    """
    0 = unstable or new operator, 1 = highly stable long-running operator.
    Stable operators require stronger evidence before escalation.
    """
    score = 0.0

    if resolved_count_90d >= 10:   score += 0.35
    elif resolved_count_90d >= 5:  score += 0.20
    elif resolved_count_90d >= 2:  score += 0.10

    if crisis_recurrence_count == 0:   score += 0.30
    elif crisis_recurrence_count == 1: score += 0.10

    if operational_age_days >= 365:    score += 0.35
    elif operational_age_days >= 180:  score += 0.20
    elif operational_age_days >= 90:   score += 0.10

    return min(1.0, round(score, 3))
