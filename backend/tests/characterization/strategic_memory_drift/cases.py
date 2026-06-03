"""Characterization fixtures for logic.strategic_memory_drift (Sprint 72 — branch depth).
Observe-only. Reaches every drift_state branch:
aligned (non-recurring + recurring-no-flags), compounding_repetition,
historically_disconnected, fragmented, drifting.
"""
from logic.strategic_memory_drift import compute_strategic_memory_drift
from characterization._engine import call

_PARAMS = [
    "insight_category", "signal_lifecycle_stage", "signal_recurrence_count", "outcome_state",
    "recovery_state", "recovery_probability", "adaptive_capacity_state", "resilience_trajectory",
    "reversal_state", "timing_state", "obs_recovery_state", "counterfactual_pressure_state",
    "pressure_accumulation", "trajectory_direction",
]


def _d(**over):
    kw = {p: None for p in _PARAMS}
    kw["insight_category"] = over.pop("insight_category", "margin_crisis")
    kw.update(over)
    return call(compute_strategic_memory_drift, **kw)


def build_cases():
    c = {}
    c["aligned_not_recurring"] = _d(signal_lifecycle_stage=None)
    c["aligned_recurring_no_flags"] = _d(signal_lifecycle_stage="recurring", signal_recurrence_count=2)
    c["compounding_repetition"] = _d(signal_lifecycle_stage="recurring", signal_recurrence_count=3,
                                    outcome_state="failed", adaptive_capacity_state="deteriorating")
    c["historically_disconnected"] = _d(signal_lifecycle_stage="recurring", signal_recurrence_count=2,
                                        outcome_state="failed", adaptive_capacity_state="rigid",
                                        recovery_state="unstable")
    c["fragmented"] = _d(signal_lifecycle_stage="recurring", signal_recurrence_count=2,
                        reversal_state="overextended")
    c["drifting"] = _d(signal_lifecycle_stage="recurring", signal_recurrence_count=2,
                      outcome_state="temporary")
    # category-specific repetition pattern (fragmented path, different category)
    c["fragmented_low_stock"] = _d(insight_category="low_stock", signal_lifecycle_stage="recurring",
                                  signal_recurrence_count=2, reversal_state="structurally_locked")
    return c
