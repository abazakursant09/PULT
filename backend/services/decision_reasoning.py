"""
Decision reason engine (Learning OS L2.3).

Turns outcome-memory ranking output (rank_actions) into structured, human-readable
explanations. Pure: it consumes ranking stat dicts and returns DecisionReason
dicts — no DB, no writes, no execution/measurement/promotion dependency, no ML,
no probabilities. The ranking service is unchanged; this only explains it.

confirmed_rate is a transparent ratio carried through from ranking, never a
probability. `fallback` is true whenever the action did not reach min_sample (it
keeps the deterministic default order).
"""
from __future__ import annotations

from typing import Optional


def explain(stat: dict) -> dict:
    """
    Structured explanation for one ranked action stat (as produced by
    outcome_memory_ranking.rank_actions). Deterministic.
    """
    eligible = bool(stat.get("eligible"))
    confirmed = int(stat.get("confirmed", 0) or 0)
    refuted = int(stat.get("refuted", 0) or 0)
    sample = int(stat.get("sample", 0) or 0)

    if eligible:
        reason = f"{confirmed} of {sample} similar cases confirmed profit improvement"
        fallback = False
    elif sample > 0:
        reason = f"Not enough history ({sample} cases). Using default action order."
        fallback = True
    else:
        reason = "Not enough history. Using default action order."
        fallback = True

    return {
        "action_key": stat.get("action_key"),
        "rank": stat.get("rank"),
        "confirmed": confirmed,
        "refuted": refuted,
        "sample": sample,
        "confirmed_rate": stat.get("confirmed_rate"),
        "reason": reason,
        "fallback": fallback,
    }


def explain_ranking(ranked: list[dict]) -> list[dict]:
    """Explain a full rank_actions result, preserving its order."""
    return [explain(s) for s in ranked]
