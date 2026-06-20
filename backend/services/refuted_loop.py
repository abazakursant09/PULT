"""
Refuted loop foundation (Learning OS Phase 1, L1).

When a Decision is measured REFUTED, propose the NEXT alternative action and
create a follow-up Decision in the SAME chain. Deterministic next-in-action-space
selection only — NO ranking, NO probabilities, NO historical learning. The
previous Decision is never mutated; a new Decision is appended.

Selection sequence (margin_crisis): set_price → reduce_discount →
stop_auto_promotion → None. Exhausting the action space is the stop condition.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision import Decision
from services.marketplace import action_metric_binding


def select_next_candidate(
    problem_type: Optional[str],
    failed_action_key: Optional[str],
    available_actions: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Pure, deterministic: the action immediately AFTER `failed_action_key` in the
    problem's declared action space, or None when it was the last (or unknown).
    No learning, no ranking, no probabilities.
    """
    space = (list(available_actions) if available_actions is not None
             else list(action_metric_binding.problem_action_space(problem_type)))
    if failed_action_key not in space:
        return None
    i = space.index(failed_action_key)
    return space[i + 1] if i + 1 < len(space) else None


async def create_followup_for_refuted(
    db: AsyncSession, decision: Decision, *, now: Optional[datetime] = None
) -> Optional[Decision]:
    """
    Create the follow-up Decision for a refuted one: same insight_key / marketplace
    / sku / product / chain, next action_key, step_in_chain + 1. Idempotent: if a
    Decision for (user, insight_key, next_action) already exists (e.g. a
    pre-promoted alternative) no duplicate is created. Returns the new Decision or
    None (no next action / already exists).
    """
    insight_key = getattr(decision, "insight_key", None)
    problem_type = insight_key.split(":", 1)[0] if insight_key and ":" in insight_key else ""
    nxt = select_next_candidate(problem_type, getattr(decision, "action_key", None))
    if nxt is None:
        return None

    existing = (
        await db.execute(
            select(Decision).where(
                Decision.user_id == decision.user_id,
                Decision.insight_key == insight_key,
                Decision.action_key == nxt,
            )
        )
    ).scalars().first()
    if existing is not None:
        return None  # alternative already exists → no chain duplicate

    followup = Decision(
        user_id=decision.user_id,
        insight_key=insight_key,
        physical_product_id=decision.physical_product_id,
        listing_id=decision.listing_id,
        problem=decision.problem,
        cause=decision.cause,
        effect=decision.effect,
        action=decision.action,
        action_key=nxt,
        pnl_impact=decision.pnl_impact,
        pnl_level=decision.pnl_level,
        severity=decision.severity,
        source="followup",
        status="open",
        decision_chain_id=decision.decision_chain_id,          # SAME chain
        step_in_chain=(decision.step_in_chain or 0) + 1,       # next attempt
    )
    db.add(followup)
    await db.flush()
    return followup
