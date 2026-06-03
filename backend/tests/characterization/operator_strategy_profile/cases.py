"""Characterization fixtures for logic.operator_strategy_profile (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.operator_strategy_profile import compute_operator_strategy_profile
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_operator_strategy_profile"] = call(compute_operator_strategy_profile, **auto_kwargs(compute_operator_strategy_profile))
    if has_list_param(compute_operator_strategy_profile): c["compute_operator_strategy_profile.empty"] = call(compute_operator_strategy_profile, **auto_kwargs(compute_operator_strategy_profile, empty=True))
    return c
