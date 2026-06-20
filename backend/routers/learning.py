"""
Learning read API (L6) — ranked, explainable alternatives for an insight.

Read-only exposure of services.ranked_alternatives.get_ranked_alternatives_for_insight.
The handler resolves the read-side context_group with the SAME logic Memory
writes use (L5), ranks margin alternatives by outcome memory (L2/L4), and returns
them with structured reasons. NO promotion, NO execution, NO measurement, NO
writes — this surface only reads and explains; it never changes lifecycle.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from services.ranked_alternatives import get_ranked_alternatives_for_insight
from services.decision_memory import resolve_context_group_for_insight

router = APIRouter()

_SOURCE = "decision_memory"


class Alternative(BaseModel):
    action_key: str
    rank: int
    reason: str
    fallback: bool
    confirmed: int
    refuted: int
    sample: int
    confirmed_rate: float | None = None
    weighted_rate: float | None = None


class AlternativesResponse(BaseModel):
    insight_key: str
    alternatives: list[Alternative]
    source: str
    degraded: bool


@router.get("/learning/alternatives", response_model=AlternativesResponse)
async def ranked_alternatives_endpoint(
    insight_key: str,
    listing_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AlternativesResponse:
    """
    Ranked, explained alternatives for an insight. Read-only. `degraded` is true
    when the resolved context_group still carries an 'unknown' segment (i.e. the
    ranking ran on a degraded business context).
    """
    alternatives = await get_ranked_alternatives_for_insight(
        db, user_id=current_user.id, insight_key=insight_key, listing_id=listing_id)
    context_group = await resolve_context_group_for_insight(
        db, user_id=current_user.id, insight_key=insight_key, listing_id=listing_id)
    return AlternativesResponse(
        insight_key=insight_key,
        alternatives=alternatives,
        source=_SOURCE,
        degraded="unknown" in context_group,
    )
