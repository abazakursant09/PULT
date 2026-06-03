"""Characterization fixtures for logic.recovery_paths (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.recovery_paths import compute_recovery_path
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_recovery_path"] = call(compute_recovery_path, **auto_kwargs(compute_recovery_path))
    if has_list_param(compute_recovery_path): c["compute_recovery_path.empty"] = call(compute_recovery_path, **auto_kwargs(compute_recovery_path, empty=True))
    return c
