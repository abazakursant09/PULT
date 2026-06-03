"""Characterization fixtures for logic.comparative_simulation (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.comparative_simulation import compute_path_comparison
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_path_comparison"] = call(compute_path_comparison, **auto_kwargs(compute_path_comparison))
    return c
