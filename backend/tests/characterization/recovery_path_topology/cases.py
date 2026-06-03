"""Characterization fixtures for logic.recovery_path_topology (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.recovery_path_topology import compute_recovery_path_topology
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_recovery_path_topology"] = call(compute_recovery_path_topology, **auto_kwargs(compute_recovery_path_topology))
    if has_list_param(compute_recovery_path_topology): c["compute_recovery_path_topology.empty"] = call(compute_recovery_path_topology, **auto_kwargs(compute_recovery_path_topology, empty=True))
    return c
