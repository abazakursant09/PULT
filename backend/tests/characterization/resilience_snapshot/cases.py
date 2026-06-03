"""Characterization fixtures for logic.resilience_snapshot (Sprint 72 — branch depth).
Observe-only. Drives the composite score into every resilience_state band
(adaptive..exhausted), every absorption_capacity band, and the category->layer map.
"""
from logic.resilience_snapshot import compute_resilience_snapshot
from characterization._engine import call

_PARAMS = [
    "insight_category", "trajectory_state", "trajectory_direction", "recovery_state",
    "recovery_probability", "outcome_state", "reversibility_state", "pressure_accumulation",
    "counterfactual_pressure_state", "signal_lifecycle_stage", "signal_recurrence_count",
    "signal_decay_state", "cascade_state", "obs_recovery_state", "reversal_state",
    "timing_state", "tradeoff_severity",
]


def _rs(**over):
    kw = {p: None for p in _PARAMS}
    kw["insight_category"] = over.pop("insight_category", "margin_crisis")
    kw.update(over)
    return call(compute_resilience_snapshot, **kw)


def build_cases():
    c = {}
    # adaptive (score capped 100) + absorption high
    c["adaptive"] = _rs(trajectory_direction="improving", trajectory_state="stabilizing",
                        outcome_state="improved", recovery_state="quick", recovery_probability=80,
                        counterfactual_pressure_state="stable", reversibility_state="easily_reversible",
                        signal_lifecycle_stage="stabilized", signal_decay_state="fresh",
                        pressure_accumulation="dissipating")
    # resilient band (60-74)
    c["resilient"] = _rs(trajectory_direction="improving")
    # moderate band (42-59) — neutral base 55
    c["moderate_neutral"] = _rs()
    # narrowing band (27-41)
    c["narrowing"] = _rs(trajectory_direction="worsening", pressure_accumulation="accumulating")
    # brittle band (12-26)
    c["brittle"] = _rs(trajectory_direction="critical", pressure_accumulation="compounding")
    # collapsing band (4-11)
    c["collapsing"] = _rs(trajectory_direction="critical", pressure_accumulation="compounding",
                         cascade_state="structurally_cascading")
    # exhausted band (<4)
    c["exhausted"] = _rs(trajectory_direction="critical", pressure_accumulation="compounding",
                        cascade_state="structurally_cascading", outcome_state="repeated",
                        reversibility_state="structurally_locked", recovery_state="unstable")
    # recurrence cumulative penalties + low recovery_pct
    c["recurrence_penalties"] = _rs(signal_recurrence_count=5, recovery_probability=20,
                                   obs_recovery_state="reset_required", reversal_state="structurally_locked",
                                   timing_state="structurally_late", tradeoff_severity="significant")
    # category -> weakest_layer variants + unknown category (None)
    c["category_low_stock"] = _rs(insight_category="low_stock")
    c["category_unknown"] = _rs(insight_category="unknown_cat")
    # individual score-modifier coverage
    c["mods_positive"] = _rs(outcome_state="stabilized", recovery_state="gradual")
    c["mods_struct_accum"] = _rs(trajectory_state="structurally_accumulating")
    c["mods_negative"] = _rs(trajectory_state="escalating", recovery_state="structural",
                            outcome_state="failed", reversibility_state="narrowing_window",
                            cascade_state="coupled_instability", obs_recovery_state="fragmented",
                            reversal_state="overextended")
    return c
