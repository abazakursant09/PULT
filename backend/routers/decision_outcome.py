"""
Decision Outcome read API (A8) — surface proven decision effects + an honest
feedback view for the Learning OS. Read-only. NOT a dashboard, NOT BI. No score,
no forecast, no ROI, no money promise, no fake success. not_evaluated /
not_measured_yet are reported truthfully.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User

from services.decision_outcome.effect_summary import (
    build_effect_summaries, aggregate_counts, DecisionEffectSummary,
)

router = APIRouter()


class EffectView(BaseModel):
    decision_id: Optional[str]
    insight_key: Optional[str]
    contour: str
    marketplace: Optional[str]
    sku: Optional[str]
    action_key: Optional[str]
    metric_key: Optional[str]
    link_status: str
    effect_band: Optional[str]
    effect_status: str               # proven_improved|proven_unchanged|proven_worsened|not_measured_yet|not_evaluated
    measured_at: Optional[str]
    evidence: Optional[dict]
    what_happened: str
    what_it_means: str
    next_action: str


def _view(s: DecisionEffectSummary) -> EffectView:
    return EffectView(
        decision_id=s.decision_id, insight_key=s.insight_key, contour=s.contour,
        marketplace=s.marketplace, sku=s.sku, action_key=s.action_key, metric_key=s.metric_key,
        link_status=s.link_status, effect_band=s.effect_band, effect_status=s.effect_status,
        measured_at=s.measured_at.isoformat() if s.measured_at else None,
        evidence=dict(s.evidence) if s.evidence else {},
        what_happened=s.what_happened, what_it_means=s.what_it_means, next_action=s.next_action,
    )


class EffectsResponse(BaseModel):
    items: list[EffectView]
    total: int


@router.get("/decision-outcome/effects", response_model=EffectsResponse)
async def decision_outcome_effects(
    contour: Optional[str] = None,
    effect_status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EffectsResponse:
    summaries = await build_effect_summaries(
        db, user_id=current_user.id, contour=contour, effect_status=effect_status)
    items = [_view(s) for s in summaries]
    return EffectsResponse(items=items, total=len(items))


class SummaryResponse(BaseModel):
    proven_improved: int
    proven_unchanged: int
    proven_worsened: int
    not_evaluated: int
    not_measured_yet: int
    total: int


@router.get("/decision-outcome/summary", response_model=SummaryResponse)
async def decision_outcome_summary(
    contour: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SummaryResponse:
    summaries = await build_effect_summaries(db, user_id=current_user.id, contour=contour)
    c = aggregate_counts(summaries)
    return SummaryResponse(
        proven_improved=c["proven_improved"], proven_unchanged=c["proven_unchanged"],
        proven_worsened=c["proven_worsened"], not_evaluated=c["not_evaluated"],
        not_measured_yet=c["not_measured_yet"], total=c["total"])
