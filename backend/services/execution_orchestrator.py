"""
Execution orchestrator (Slice 9: PLAN ONLY — NO EXECUTION).

Builds an inert execution-plan artifact from policy-allowed candidates. It does
NOT execute, NOT call the executor, NOT touch Decision tables, NOT schedule, NOT
write. The plan is data: a sorted list of would-be actions. Producing it has no
side effects.

Flow: candidates (Slice 7) → policy (Slice 8) → convert allowed → plan.

Only policy-ALLOWED, action_based candidates become plan entries (those are the
executable ones). insight_based allowed candidates are advisory/diagnostic and
are intentionally excluded from the execution plan. confidence is copied
verbatim, never recomputed.
"""
from __future__ import annotations

from services import decision_candidate_engine as _cand
from services import decision_policy_engine as _policy

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


async def build_execution_plan(db, user_id: str) -> dict:
    """
    {execution_plan, blocked_actions, metadata}. Read-only, deterministic.
    No action is taken — the plan is a proposal artifact only.
    """
    candidates = await _cand.generate_decision_candidates(db, user_id)
    pol = await _policy.apply_decision_policy(db, user_id, candidates)

    allowed = pol.get("allowed_actions", [])
    blocked = pol.get("blocked_actions", [])

    # Only executable (action_based) allowed candidates enter the plan.
    executable = [c for c in allowed if c.get("type") == "action_based"]
    # Stable sort high → low; preserves policy order within a priority tier.
    executable = sorted(executable, key=lambda c: _PRIORITY_RANK.get(c.get("priority"), 3))

    plan = [{
        "action_type": c["target"],
        "target": c["target"],
        "priority": c.get("priority"),
        "confidence": c.get("confidence"),   # copied, not recalculated
        "reason": c.get("reason"),
    } for c in executable]

    return {
        "execution_plan": plan,
        "blocked_actions": blocked,
        "metadata": {
            "total_candidates": len(candidates),
            "allowed_count": len(plan),
            "blocked_count": len(blocked),
        },
    }
