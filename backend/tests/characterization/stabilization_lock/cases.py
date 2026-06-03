"""Characterization fixtures for logic.stabilization_lock (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.stabilization_lock import compute_stabilization_lock
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_stabilization_lock"] = call(compute_stabilization_lock, **auto_kwargs(compute_stabilization_lock))
    return c
