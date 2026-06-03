"""Characterization fixtures for logic.operational_regime (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.operational_regime import compute_operational_regime
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_operational_regime"] = call(compute_operational_regime, **auto_kwargs(compute_operational_regime))
    if has_list_param(compute_operational_regime): c["compute_operational_regime.empty"] = call(compute_operational_regime, **auto_kwargs(compute_operational_regime, empty=True))
    return c
