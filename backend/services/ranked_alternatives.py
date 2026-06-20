"""
Ranked alternatives read model (Learning OS L2.4).

One read-only entry point for a future Evidence / Alternatives surface: returns
the margin alternatives in outcome-memory-ranked order, each with a structured
reason. Composes existing pieces — emit_ranked_candidates (order) + rank_actions
(stats) + explain_ranking (reasons) — into one list. Never drops a candidate.

Read-only: no Decision promotion, no execution, no DecisionMemory writes, no
measurement, no ML, no probabilities, no migration.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from services.insight_decision_bridge import emit_ranked_candidates
from services.outcome_memory_ranking import rank_actions
from services.decision_reasoning import explain_ranking

_NON_MARGIN_REASON = "No ranking available for this problem type. Using default action order."


async def get_ranked_alternatives(
    db: AsyncSession, *, user_id: str, insight_key: str, context_group: str
) -> list[dict]:
    """
    Ordered, explained alternatives for an insight. For margin_crisis: ranked by
    outcome memory with historical reasons. For other problems: deterministic
    candidates with a fallback reason. Empty list for a malformed/empty insight.
    Read-only.
    """
    candidates = await emit_ranked_candidates(
        db, user_id=user_id, insight_key=insight_key, context_group=context_group)
    if not candidates:
        return []

    itype = candidates[0].itype
    if itype == "margin_crisis":
        ranked = await rank_actions(
            db, user_id=user_id, problem_type=itype, context_group=context_group,
            available_actions=[c.action_key for c in candidates])
        reasons = {r["action_key"]: r for r in explain_ranking(ranked)}
        out: list[dict] = []
        for i, c in enumerate(candidates, 1):
            r = reasons.get(c.action_key, {})
            out.append({
                "action_key": c.action_key,
                "rank": i,
                "reason": r.get("reason", _NON_MARGIN_REASON),
                "fallback": bool(r.get("fallback", True)),
                "confirmed": int(r.get("confirmed", 0) or 0),
                "refuted": int(r.get("refuted", 0) or 0),
                "sample": int(r.get("sample", 0) or 0),
                "confirmed_rate": r.get("confirmed_rate"),
                "weighted_rate": r.get("weighted_rate"),
            })
        return out

    # non-margin: deterministic candidates, no ranking
    return [{
        "action_key": c.action_key,
        "rank": i,
        "reason": _NON_MARGIN_REASON,
        "fallback": True,
        "confirmed": 0,
        "refuted": 0,
        "sample": 0,
        "confirmed_rate": None,
        "weighted_rate": None,
    } for i, c in enumerate(candidates, 1)]
