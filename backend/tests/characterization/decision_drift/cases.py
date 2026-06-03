"""Characterization fixtures for logic.decision_drift (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.decision_drift import compute_decision_drift
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_decision_drift"] = call(compute_decision_drift, **auto_kwargs(compute_decision_drift))
    if has_list_param(compute_decision_drift): c["compute_decision_drift.empty"] = call(compute_decision_drift, **auto_kwargs(compute_decision_drift, empty=True))
    return c
