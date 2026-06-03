"""Characterization fixtures for logic.intervention_reversal (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.intervention_reversal import compute_intervention_reversal
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_intervention_reversal"] = call(compute_intervention_reversal, **auto_kwargs(compute_intervention_reversal))
    return c
