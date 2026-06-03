"""Characterization fixtures for logic.outcome_feedback (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.outcome_feedback import evaluate_operator_action
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["evaluate_operator_action"] = call(evaluate_operator_action, **auto_kwargs(evaluate_operator_action))
    return c
