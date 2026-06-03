"""Characterization fixtures for logic.marketplace_behavior (Sprint 71).
Auto-generated, observe-only. Freezes current behavior on a neutral input path.
"""
from logic.marketplace_behavior import behavior_note_for_insight, match_marketplace_patterns
from characterization._engine import call, auto_kwargs, has_list_param


def build_cases():
    c = {}
    c["behavior_note_for_insight"] = call(behavior_note_for_insight, **auto_kwargs(behavior_note_for_insight))
    c["match_marketplace_patterns"] = call(match_marketplace_patterns, **auto_kwargs(match_marketplace_patterns))
    return c
