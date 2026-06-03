"""
Operator Strategy Profile — Sprint 40.
Analyzes operator behavioral patterns to surface intervention style,
pacing discipline, recovery patience, and structural decision tendency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class OperatorStrategyProfile:
    intervention_style:            str   # stable | reactive | aggressive | delayed | oscillating
    pacing_discipline:             str   # strong | moderate | weak
    recovery_patience:             str   # patient | unstable | intervention_prone
    structural_decision_tendency:  str   # balanced | symptom_focused | structurally_avoidant
    operational_volatility_source: str   # market_driven | mixed | operator_driven
    strategic_stability_score:     int   # 0-100
    stability_band:                str   # unstable | elevated | generally_stable | disciplined
    coaching_note:                 Optional[str]
    profile_confidence:            str   # low | moderate | stable | high


_OPERATOR_CATS = {"high_ad_spend", "margin_crisis"}
_MARKET_CATS   = {"seo_opportunity", "sales_growth", "high_rating", "low_stock"}

_STYLE_DELTA: dict[str, int] = {
    "stable":      +10,
    "reactive":    -5,
    "aggressive":  -18,
    "delayed":     -12,
    "oscillating": -20,
}
_PACING_DELTA: dict[str, int] = {"strong": +8, "moderate": 0, "weak": -10}
_PATIENCE_DELTA: dict[str, int] = {"patient": +5, "unstable": -5, "intervention_prone": -12}
_TENDENCY_DELTA: dict[str, int] = {"balanced": +5, "symptom_focused": -5, "structurally_avoidant": -8}
_VOLATILITY_DELTA: dict[str, int] = {"market_driven": +3, "mixed": 0, "operator_driven": -5}

_COACHING: dict[str, str] = {
    "aggressive":        "Высокая частота вмешательств без завершённых стабилизационных циклов — признак операционного нетерпения.",
    "oscillating":       "Система замечает склонность к ранним вмешательствам до завершения стабилизационного окна.",
    "weak":              "Слабая дисциплина пейсинга увеличивает накопленное давление при повторяющихся сигналах.",
    "operator_driven":   "Значительная часть волатильности связана с решениями оператора, а не рыночной динамикой.",
    "unstable_recovery": "Система замечает склонность к ранним вмешательствам до завершения стабилизационного окна.",
}


def compute_operator_strategy_profile(
    insights: list,
    portfolio_patterns: list,
    fatigue_score: float = 0.0,
    stability_credit: float = 0.0,
) -> OperatorStrategyProfile:
    active = [
        i for i in insights
        if getattr(i, "status", "") not in ("resolved", "dismissed")
    ]

    # ── Signal counts ────────────────────────────────────────────────────────
    recurring_count  = sum(1 for i in active if getattr(i, "signal_lifecycle_stage", None) == "recurring")
    confirmed_count  = sum(1 for i in active if getattr(i, "signal_lifecycle_stage", None) == "confirmed")
    escalating_count = sum(1 for i in active
                           if getattr(i, "trajectory_state", None) in ("escalating", "structurally_accumulating"))
    temp_fixes       = sum(1 for i in active
                           if getattr(i, "outcome_state", None) == "temporary"
                           and (getattr(i, "signal_recurrence_count", None) or 0) >= 2)
    waiting_locks    = sum(1 for i in active if getattr(i, "recovery_signal_state", None) == "waiting")
    structural_rec   = sum(1 for i in active if getattr(i, "recovery_state", None) in ("structural", "unstable"))
    int_prone_count  = sum(1 for i in active
                           if getattr(i, "recovery_signal_state", None) == "waiting"
                           and getattr(i, "signal_lifecycle_stage", None) == "recurring")
    symptom_count    = sum(1 for i in active
                           if getattr(i, "stabilization_role", None) == "fast_stabilization"
                           and getattr(i, "signal_lifecycle_stage", None) == "recurring")
    structural_never = sum(1 for i in active
                           if getattr(i, "stabilization_role", None) == "structural_fix"
                           and getattr(i, "signal_lifecycle_stage", None) == "recurring")
    op_cat_count     = sum(1 for i in active
                           if getattr(i, "key", "").split(":")[0] in _OPERATOR_CATS
                           and getattr(i, "signal_lifecycle_stage", None) == "recurring")
    mkt_cat_count    = sum(1 for i in active
                           if getattr(i, "key", "").split(":")[0] in _MARKET_CATS
                           and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed"))
    lc_known         = sum(1 for i in active if getattr(i, "signal_lifecycle_stage", None) is not None)

    # ── Intervention style ───────────────────────────────────────────────────
    if recurring_count >= 2 and temp_fixes >= 1:
        intervention_style = "oscillating"
    elif recurring_count >= 3 and escalating_count >= 1:
        intervention_style = "aggressive"
    elif escalating_count >= 2:
        intervention_style = "delayed"
    elif recurring_count >= 1 and (confirmed_count >= 1 or escalating_count == 0):
        intervention_style = "reactive"
    else:
        intervention_style = "stable"

    # ── Pacing discipline ────────────────────────────────────────────────────
    if stability_credit >= 0.3 and waiting_locks == 0 and fatigue_score <= 0.3:
        pacing_discipline = "strong"
    elif waiting_locks >= 2 and recurring_count >= 1:
        pacing_discipline = "weak"
    else:
        pacing_discipline = "moderate"

    # ── Recovery patience ────────────────────────────────────────────────────
    if int_prone_count >= 2:
        recovery_patience = "intervention_prone"
    elif structural_rec >= 2 and recurring_count >= 2:
        recovery_patience = "unstable"
    else:
        recovery_patience = "patient"

    # ── Structural decision tendency ─────────────────────────────────────────
    if structural_never >= 2 and recurring_count >= 2:
        structural_decision_tendency = "structurally_avoidant"
    elif symptom_count >= 2 and structural_never < 1:
        structural_decision_tendency = "symptom_focused"
    else:
        structural_decision_tendency = "balanced"

    # ── Operational volatility source ─────────────────────────────────────────
    if op_cat_count >= 2:
        operational_volatility_source = "operator_driven"
    elif mkt_cat_count >= 2 and op_cat_count == 0:
        operational_volatility_source = "market_driven"
    else:
        operational_volatility_source = "mixed"

    # ── Strategic stability score ─────────────────────────────────────────────
    score = 70
    score += _STYLE_DELTA.get(intervention_style, 0)
    score += _PACING_DELTA.get(pacing_discipline, 0)
    score += _PATIENCE_DELTA.get(recovery_patience, 0)
    score += _TENDENCY_DELTA.get(structural_decision_tendency, 0)
    score += _VOLATILITY_DELTA.get(operational_volatility_source, 0)
    score += int(stability_credit * 10)
    score -= int(fatigue_score * 10)
    score  = max(0, min(100, score))

    # ── Stability band ────────────────────────────────────────────────────────
    if score >= 75:
        stability_band = "disciplined"
    elif score >= 55:
        stability_band = "generally_stable"
    elif score >= 40:
        stability_band = "elevated"
    else:
        stability_band = "unstable"

    # ── Coaching note ─────────────────────────────────────────────────────────
    coaching_note: Optional[str] = None
    if intervention_style == "aggressive":
        coaching_note = _COACHING["aggressive"]
    elif intervention_style == "oscillating":
        coaching_note = _COACHING["oscillating"]
    elif pacing_discipline == "weak":
        coaching_note = _COACHING["weak"]
    elif operational_volatility_source == "operator_driven":
        coaching_note = _COACHING["operator_driven"]
    elif recovery_patience == "unstable":
        coaching_note = _COACHING["unstable_recovery"]

    # ── Profile confidence ────────────────────────────────────────────────────
    if lc_known < 2:
        profile_confidence = "low"
    elif lc_known < 4:
        profile_confidence = "moderate"
    elif lc_known < 6:
        profile_confidence = "stable"
    else:
        profile_confidence = "high"

    return OperatorStrategyProfile(
        intervention_style=intervention_style,
        pacing_discipline=pacing_discipline,
        recovery_patience=recovery_patience,
        structural_decision_tendency=structural_decision_tendency,
        operational_volatility_source=operational_volatility_source,
        strategic_stability_score=score,
        stability_band=stability_band,
        coaching_note=coaching_note,
        profile_confidence=profile_confidence,
    )
