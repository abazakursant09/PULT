"""Characterization fixtures for logic.decision_weight (Sprint 71). Observe-only.

Hand-built: apply_decision_weights needs real insight objects + an operator
profile (None is a valid path -> sensitivity 0.5); compute_fatigue_score needs a
dict for ignored_categories. The other two are pure scalar functions.
"""
from logic.decision_weight import (
    apply_decision_weights, compute_fatigue_score, compute_stability_credit, compute_weight,
)
from characterization._engine import call, insight, jsonable


def build_cases():
    c = {}

    c["compute_weight.margin_structural"] = call(
        compute_weight, insight_type="margin_crisis", days_active=30, confidence=85,
        financial_impact=25000.0, pressure_source="structural", causal_depth=1,
        operator_sensitivity=0.5, recurring=True)
    c["compute_weight.seo_low_conf"] = call(
        compute_weight, insight_type="seo_opportunity", days_active=3, confidence=55,
        financial_impact=2000.0, pressure_source=None, causal_depth=0,
        operator_sensitivity=0.8, recurring=False)
    c["compute_weight.low_stock"] = call(
        compute_weight, insight_type="low_stock", days_active=10, confidence=90,
        financial_impact=12000.0, pressure_source=None, causal_depth=0,
        operator_sensitivity=0.5, recurring=False)

    c["compute_fatigue_score.none"] = call(
        compute_fatigue_score, unresolved_count=0, alerts_last_7d=0,
        ignored_categories={}, focus_churn=0)
    c["compute_fatigue_score.high"] = call(
        compute_fatigue_score, unresolved_count=8, alerts_last_7d=15,
        ignored_categories={"seo_opportunity": 3, "high_ad_spend": 3}, focus_churn=4)

    c["compute_stability_credit.new_operator"] = call(
        compute_stability_credit, resolved_count_90d=0, crisis_recurrence_count=3,
        operational_age_days=10)
    c["compute_stability_credit.stable_operator"] = call(
        compute_stability_credit, resolved_count_90d=10, crisis_recurrence_count=0,
        operational_age_days=400)

    # apply_decision_weights mutates insights in-place; freeze the resulting fields.
    ins = [
        insight(key="margin_crisis:wildberries:A", confidence=80),
        insight(key="low_stock:ozon:B", confidence=90),
    ]
    apply_decision_weights(ins, {"margin_crisis": 2}, None)
    c["apply_decision_weights.effect"] = jsonable([
        {"key": i.key, "weight": i.weight, "signal_state": i.signal_state,
         "resolution_difficulty": i.resolution_difficulty, "intervention_tier": i.intervention_tier}
        for i in ins
    ])
    return c
