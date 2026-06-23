"""
Promotion Activation API (A3) — safe manual trigger for run_promotion.

Thin owner-scoped delegation: POST runs the EXISTING promotion/bridge for the caller
(eligible actionable signals → Decisions) and returns a summary. It applies nothing,
opens no measurement, calls no marketplace, creates no signal — a Decision is an
intent record. No scheduler, no auto-loop, no executor.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User

from services.promotion_activation.runner import run_promotion, PromotionRunResult

router = APIRouter()


class RunRequest(BaseModel):
    contour: Optional[str] = None


class RunItemView(BaseModel):
    phase: str
    contour: str
    outcome: str
    reason: Optional[str] = None
    decision_id: Optional[str] = None


class RunResponse(BaseModel):
    ok: bool
    run_id: Optional[str]
    candidates_seen: int
    links_created: int
    decisions_created: int
    skipped: int
    items: List[RunItemView]


def _view(r: PromotionRunResult) -> RunResponse:
    return RunResponse(
        ok=True, run_id=r.run_id, candidates_seen=r.candidates_seen,
        links_created=r.links_created, decisions_created=r.decisions_created,
        skipped=r.skipped,
        items=[RunItemView(phase=i.phase, contour=i.contour, outcome=i.outcome,
                           reason=i.reason, decision_id=i.decision_id) for i in r.items])


@router.post("/promotion-activation/run", response_model=RunResponse)
async def promotion_activation_run(
    body: RunRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    res = await run_promotion(db, user_id=current_user.id, contour=body.contour,
                              triggered_by="manual")
    await db.commit()
    return _view(res)
