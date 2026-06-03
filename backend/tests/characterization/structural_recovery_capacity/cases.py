"""Characterization fixtures for logic.structural_recovery_capacity (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.structural_recovery_capacity import compute_structural_recovery_capacity
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_structural_recovery_capacity"] = call(compute_structural_recovery_capacity, **auto_kwargs(compute_structural_recovery_capacity))
    if has_list_param(compute_structural_recovery_capacity): c["compute_structural_recovery_capacity.empty"] = call(compute_structural_recovery_capacity, **auto_kwargs(compute_structural_recovery_capacity, empty=True))
    return c
