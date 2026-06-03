"""Characterization fixtures for logic.intervention_timing (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.intervention_timing import compute_intervention_timing
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_intervention_timing"] = call(compute_intervention_timing, **auto_kwargs(compute_intervention_timing))
    return c
