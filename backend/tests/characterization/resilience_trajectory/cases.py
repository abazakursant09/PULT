"""Characterization fixtures for logic.resilience_trajectory (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.resilience_trajectory import compute_resilience_trajectory
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_resilience_trajectory"] = call(compute_resilience_trajectory, **auto_kwargs(compute_resilience_trajectory))
    return c
