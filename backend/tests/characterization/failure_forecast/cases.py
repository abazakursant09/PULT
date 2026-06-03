"""Characterization fixtures for logic.failure_forecast (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.failure_forecast import compute_failure_forecast
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["compute_failure_forecast"] = call(compute_failure_forecast, **auto_kwargs(compute_failure_forecast))
    if has_list_param(compute_failure_forecast): c["compute_failure_forecast.empty"] = call(compute_failure_forecast, **auto_kwargs(compute_failure_forecast, empty=True))
    return c
