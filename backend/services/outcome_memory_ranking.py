"""
Outcome memory ranking (Learning OS Phase 2, L2).

Pure read-only ranking of available actions by their terminal outcome history in
DecisionMemory. NO ML, NO probabilities, NO writes, NO execution. Ranking only
produces ordering + a human-readable reason; it never gates or executes.

Contract:
- Source: DecisionMemory terminal outcomes only — confirmed / refuted. insufficient,
  followup_created, still_open and any non-terminal event are ignored.
- Scope: per (user_id, context_group). Stats keyed by (context_group, action_key).
  decision_memory has no user_id, so the seller scope joins via Decision.
- min_sample = 3: an action with < 3 terminal samples is NOT ranked above the
  deterministic fallback.
- confirmed_rate = confirmed / (confirmed + refuted) — a transparent ratio.
- Sort: eligible actions by confirmed_rate desc, tie-break by the original
  available_actions order; ineligible actions keep the deterministic fallback
  order after the eligible ones.
- No action reaches min_sample → the original available_actions order is returned.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision import Decision
from models.decision_memory import DecisionMemory

MIN_SAMPLE = 3
_TERMINAL = ("confirmed", "refuted")


async def rank_actions(
    db: AsyncSession,
    *,
    user_id: str,
    problem_type: Optional[str],
    context_group: str,
    available_actions: list[str],
) -> list[dict]:
    """
    Rank `available_actions` for one seller within `context_group`. Returns a list
    of dicts (in final order) — each: action_key, confirmed, refuted, sample,
    confirmed_rate, eligible, rank, reason. Read-only, deterministic.
    """
    available = list(available_actions)
    order = {a: i for i, a in enumerate(available)}
    counts = {a: {"confirmed": 0, "refuted": 0} for a in available}

    if user_id and context_group and available:
        rows = (
            await db.execute(
                select(DecisionMemory.action_type, DecisionMemory.outcome)
                .join(Decision, Decision.id == DecisionMemory.decision_id)
                .where(
                    Decision.user_id == user_id,
                    DecisionMemory.context_group == context_group,
                    DecisionMemory.action_type.in_(available),
                    DecisionMemory.outcome.in_(_TERMINAL),
                )
            )
        ).all()
        for action, outcome in rows:
            if action in counts and outcome in ("confirmed", "refuted"):
                counts[action][outcome] += 1

    recs: list[dict] = []
    for a in available:                       # built in deterministic fallback order
        c = counts[a]["confirmed"]
        r = counts[a]["refuted"]
        sample = c + r
        recs.append({
            "action_key": a,
            "confirmed": c,
            "refuted": r,
            "sample": sample,
            "confirmed_rate": round(c / sample, 4) if sample > 0 else None,
            "eligible": sample >= MIN_SAMPLE,
        })

    eligible = [x for x in recs if x["eligible"]]
    ineligible = [x for x in recs if not x["eligible"]]
    # rate desc, tie-break original order; ineligible keep fallback order
    eligible.sort(key=lambda x: (-x["confirmed_rate"], order[x["action_key"]]))

    final = eligible + ineligible
    for i, x in enumerate(final, 1):
        x["rank"] = i
        if x["eligible"]:
            x["reason"] = f'{x["confirmed"]}/{x["sample"]} confirmed in this context'
        elif x["sample"] > 0:
            x["reason"] = "not enough history, fallback order"
        else:
            x["reason"] = "no history, fallback order"
    return final
