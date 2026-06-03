"""Characterization fixtures for logic.institutional_inertia (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.institutional_inertia import compute_institutional_inertia
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_institutional_inertia"] = call(compute_institutional_inertia, **auto_kwargs(compute_institutional_inertia))
    if has_list_param(compute_institutional_inertia): c["compute_institutional_inertia.empty"] = call(compute_institutional_inertia, **auto_kwargs(compute_institutional_inertia, empty=True))
    return c
