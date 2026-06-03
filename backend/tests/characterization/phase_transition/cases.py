"""Characterization fixtures for logic.phase_transition (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.phase_transition import compute_phase_transition
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_phase_transition"] = call(compute_phase_transition, **auto_kwargs(compute_phase_transition))
    if has_list_param(compute_phase_transition): c["compute_phase_transition.empty"] = call(compute_phase_transition, **auto_kwargs(compute_phase_transition, empty=True))
    return c
