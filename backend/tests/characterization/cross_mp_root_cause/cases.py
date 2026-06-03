"""Characterization fixtures for logic.cross_mp_root_cause (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.cross_mp_root_cause import confidence_band, infer_root_cause
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["confidence_band"] = call(confidence_band, **auto_kwargs(confidence_band))
    c["infer_root_cause"] = call(infer_root_cause, **auto_kwargs(infer_root_cause))
    if has_list_param(infer_root_cause): c["infer_root_cause.empty"] = call(infer_root_cause, **auto_kwargs(infer_root_cause, empty=True))
    return c
