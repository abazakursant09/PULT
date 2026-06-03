"""Characterization fixtures for logic.decision_confidence (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.decision_confidence import compute_decision_confidence
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_decision_confidence"] = call(compute_decision_confidence, **auto_kwargs(compute_decision_confidence))
    if has_list_param(compute_decision_confidence): c["compute_decision_confidence.empty"] = call(compute_decision_confidence, **auto_kwargs(compute_decision_confidence, empty=True))
    return c
