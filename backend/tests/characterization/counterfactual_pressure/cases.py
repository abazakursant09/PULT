"""Characterization fixtures for logic.counterfactual_pressure (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.counterfactual_pressure import compute_counterfactual_pressure
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_counterfactual_pressure"] = call(compute_counterfactual_pressure, **auto_kwargs(compute_counterfactual_pressure))
    if has_list_param(compute_counterfactual_pressure): c["compute_counterfactual_pressure.empty"] = call(compute_counterfactual_pressure, **auto_kwargs(compute_counterfactual_pressure, empty=True))
    return c
