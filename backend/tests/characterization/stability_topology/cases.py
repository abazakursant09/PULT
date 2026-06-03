"""Characterization fixtures for logic.stability_topology (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.stability_topology import compute_stability_topology
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_stability_topology"] = call(compute_stability_topology, **auto_kwargs(compute_stability_topology))
    if has_list_param(compute_stability_topology): c["compute_stability_topology.empty"] = call(compute_stability_topology, **auto_kwargs(compute_stability_topology, empty=True))
    return c
