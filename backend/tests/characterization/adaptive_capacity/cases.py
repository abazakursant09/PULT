"""Characterization fixtures for logic.adaptive_capacity (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.adaptive_capacity import compute_adaptive_capacity
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_adaptive_capacity"] = call(compute_adaptive_capacity, **auto_kwargs(compute_adaptive_capacity))
    return c
