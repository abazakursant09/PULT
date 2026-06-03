"""Characterization fixtures for logic.operational_trajectory (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.operational_trajectory import compute_operational_trajectory, trajectory_weight_delta
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_operational_trajectory"] = call(compute_operational_trajectory, **auto_kwargs(compute_operational_trajectory))
    if has_list_param(compute_operational_trajectory): c["compute_operational_trajectory.empty"] = call(compute_operational_trajectory, **auto_kwargs(compute_operational_trajectory, empty=True))
    c["trajectory_weight_delta"] = call(trajectory_weight_delta, **auto_kwargs(trajectory_weight_delta))
    return c
