"""Characterization fixtures for logic.operator_capacity (Sprint 72 — branch depth).
Observe-only. Reaches every capacity_state (stable/loaded/saturated/overloaded),
the systemic-pattern penalty, recovery bonuses, fatigue penalty, and the defer-list
construction (deferrable categories + monitor/fading/unstable low-impact).
"""
from logic.operator_capacity import compute_operator_capacity
from characterization._engine import call, insight, Duck


def _hard(key, **kw):
    return insight(key=key, resolution_difficulty="hard", **kw)


def _pattern(complexity):
    return Duck(stabilization_complexity=complexity)


def build_cases():
    c = {}
    # stable — healthy single insight, no penalties
    c["stable"] = call(compute_operator_capacity,
                      [insight(key="seo_opportunity:wildberries:A", resolution_difficulty="easy")], [])
    # loaded — ~3 hard insights
    c["loaded"] = call(compute_operator_capacity,
                      [_hard("margin_crisis:wildberries:A"), _hard("margin_crisis:wildberries:B"),
                       _hard("high_ad_spend:wildberries:C")], [])
    # saturated — more penalties + deferrable seo present + recurring + sequence_stage
    c["saturated"] = call(compute_operator_capacity, [
        _hard("margin_crisis:wildberries:A", signal_lifecycle_stage="recurring"),
        _hard("margin_crisis:wildberries:B", recovery_state="structural"),
        insight(key="seo_opportunity:wildberries:C", intervention_tier="monitor", impact_score=20),
        insight(key="sales_growth:wildberries:D", signal_decay_state="fading", impact_score=10),
    ], [])
    # overloaded — systemic patterns + heavy penalties + fatigue
    c["overloaded"] = call(compute_operator_capacity, [
        _hard("margin_crisis:wildberries:A", signal_lifecycle_stage="recurring", recovery_state="structural"),
        _hard("margin_crisis:wildberries:B", recovery_state="structural", sequence_stage=2),
        insight(key="seo_opportunity:wildberries:C", recovery_state="unstable", impact_score=10),
    ], [_pattern("systemic"), _pattern("systemic")], fatigue_score=0.8)
    # recovery bonuses: high stability_credit + resolved-positive outcome
    c["recovery_bonus"] = call(compute_operator_capacity,
                              [insight(key="margin_crisis:wildberries:A", outcome_state="improved")],
                              [], stability_credit=0.8)
    # heavy tradeoff cluster (2+ moderate/significant)
    c["tradeoff_cluster"] = call(compute_operator_capacity, [
        _hard("margin_crisis:wildberries:A", tradeoff_severity="significant"),
        _hard("high_ad_spend:wildberries:B", tradeoff_severity="moderate"),
    ], [])
    # defer second loop: a non-deferrable, non-never-defer monitor-tier insight
    c["defer_nondeferrable"] = call(compute_operator_capacity, [
        _hard("margin_crisis:wildberries:A", recovery_state="structural"),
        _hard("margin_crisis:wildberries:B", recovery_state="structural"),
        insight(key="high_ad_spend:wildberries:C", intervention_tier="monitor", impact_score=10),
    ], [_pattern("systemic")], fatigue_score=0.5)
    c["empty"] = call(compute_operator_capacity, [], [])
    return c
