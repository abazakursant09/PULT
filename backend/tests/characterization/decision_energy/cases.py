"""Characterization fixtures for logic.decision_energy (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.decision_energy import compute_decision_energy
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_decision_energy"] = call(compute_decision_energy, **auto_kwargs(compute_decision_energy))
    if has_list_param(compute_decision_energy): c["compute_decision_energy.empty"] = call(compute_decision_energy, **auto_kwargs(compute_decision_energy, empty=True))
    return c
