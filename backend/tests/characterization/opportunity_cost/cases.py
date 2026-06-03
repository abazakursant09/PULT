"""Characterization fixtures for logic.opportunity_cost (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.opportunity_cost import compute_opportunity_cost
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_opportunity_cost"] = call(compute_opportunity_cost, **auto_kwargs(compute_opportunity_cost))
    return c
