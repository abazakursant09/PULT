"""Characterization fixtures for logic.cascade_pressure (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.cascade_pressure import compute_cascade_pressure
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_cascade_pressure"] = call(compute_cascade_pressure, **auto_kwargs(compute_cascade_pressure))
    return c
