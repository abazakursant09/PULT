"""Characterization fixtures for logic.tradeoff_intelligence (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.tradeoff_intelligence import build_tradeoff_note, get_tradeoff
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["build_tradeoff_note"] = call(build_tradeoff_note, **auto_kwargs(build_tradeoff_note))
    c["get_tradeoff"] = call(get_tradeoff, **auto_kwargs(get_tradeoff))
    return c
