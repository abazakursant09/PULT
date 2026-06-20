"""
Execution approval engine (Slice 10: HUMAN-IN-LOOP GATE, no execution).

Turns a Slice 9 execution plan into a queue of pending approval requests. It
executes nothing, filters nothing, calls no executor, writes nothing, schedules
nothing. Every plan item becomes one pending request, annotated with a risk
level derived purely from confidence.

Risk model (from confidence):
    confidence > 0.8        → low
    0.5 ≤ confidence ≤ 0.8  → medium
    confidence < 0.5        → high

Future extension (NOT implemented here): approve / reject a request, and execute
only AFTER explicit human approval. This slice produces the queue only; nothing
ever leaves the 'pending' state automatically.
"""
from __future__ import annotations


def _risk_level(confidence: float) -> str:
    if confidence > 0.8:
        return "low"
    if confidence >= 0.5:
        return "medium"
    return "high"


async def create_approval_queue(db, user_id: str, execution_plan) -> list[dict]:
    """
    Map an execution plan to pending approval requests. ALL items map (no
    filtering, no execution). `execution_plan` may be the Slice 9 dict
    ({"execution_plan": [...]}) or a bare list of plan items. Read-only.
    """
    if isinstance(execution_plan, dict):
        items = execution_plan.get("execution_plan", [])
    else:
        items = execution_plan or []

    return [{
        "action_type": it.get("action_type"),
        "target": it.get("target"),
        "priority": it.get("priority"),
        "confidence": it.get("confidence"),
        "risk_level": _risk_level(it.get("confidence") or 0.0),
        "status": "pending",
    } for it in items]
