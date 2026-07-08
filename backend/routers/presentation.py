"""
Presentation Intelligence read API (P4) — expose the existing Presentation cards.

GET /api/presentation/cards re-frames the caller's live Decision Feed into seller-facing
(marketplace, sku) PresentationCards: the SAME FeedItems build_feed already returned, plus
the P1 recommendation groups, P2 severity ordering and P3 root-cause narrative.

Additive + read-only: reads FeedItems via build_feed (same tenant scoping as /decision-feed),
runs the pure services.presentation synthesis, serializes. NO writes, NO attention mutations,
NO apply, NO change to /decision-feed or /today, NO diagnosis / producer / signal / migration.
Internal FeedItem sort keys (_priority_bucket / _order_bucket) are never serialized — item
serialization reuses the Decision Feed's FeedItemView, which lists only public fields.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User

from services.decision_feed.builder import build_feed
from services.presentation import build_presentation_cards
from services.presentation.cards import PresentationCard, RecommendationGroup
# Reuse the Decision Feed's public item view so a PresentationCard's raw FeedItems serialize
# EXACTLY as /decision-feed does — same fields, no internal sort keys.
from routers.decision_feed import FeedItemView, _view

router = APIRouter()


# ── views ────────────────────────────────────────────────────────────────────

class RecommendationGroupView(BaseModel):
    group_key: str                                 # normalized merge key of the recommendation
    recommendation: str                            # seller-facing text (first occurrence)
    contributing_contours: list[str]
    contributing_problem_types: list[str]
    group_severity: Optional[str] = None           # highest observed severity (P2)
    root_cause_narrative: Optional[str] = None      # convergence narrative (P3), None if <2 items
    items: list[FeedItemView]


class PresentationCardView(BaseModel):
    marketplace: Optional[str]
    sku: Optional[str]
    group_key: str
    highest_severity: Optional[str]
    contributing_contours: list[str]
    items: list[FeedItemView]
    recommendations: list[str]
    evidence: list[dict]
    recommendation_groups: list[RecommendationGroupView]
    root_cause_narrative: Optional[str] = None


class PresentationResponse(BaseModel):
    cards: list[PresentationCardView]


def _normalize_key(text: str) -> str:
    # deterministic merge key for a group, mirrors services.presentation._normalize_recommendation
    return " ".join(text.split()).casefold()


def _group_view(g: RecommendationGroup) -> RecommendationGroupView:
    return RecommendationGroupView(
        group_key=_normalize_key(g.recommendation),
        recommendation=g.recommendation,
        contributing_contours=list(g.contributing_contours),
        contributing_problem_types=list(g.contributing_problem_types),
        group_severity=g.group_severity,
        root_cause_narrative=g.root_cause_narrative,
        items=[_view(i) for i in g.items],
    )


def _card_view(c: PresentationCard) -> PresentationCardView:
    return PresentationCardView(
        marketplace=c.marketplace,
        sku=c.sku,
        group_key=c.group_key,
        highest_severity=c.highest_severity,
        contributing_contours=list(c.contributing_contours),
        items=[_view(i) for i in c.items],
        recommendations=list(c.recommendations),
        evidence=list(c.evidence),
        recommendation_groups=[_group_view(g) for g in c.recommendation_groups],
        root_cause_narrative=c.root_cause_narrative,
    )


# ── GET /presentation/cards ──────────────────────────────────────────────────

@router.get("/presentation/cards", response_model=PresentationResponse)
async def get_presentation_cards(
    contour: Optional[str] = None,
    include_snoozed: bool = False,
    include_dismissed: bool = False,
    include_resolved: bool = False,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PresentationResponse:
    items = await build_feed(
        db, user_id=current_user.id, contour=contour, include_snoozed=include_snoozed,
        include_dismissed=include_dismissed, include_resolved=include_resolved, limit=limit)
    cards = build_presentation_cards(items)
    return PresentationResponse(cards=[_card_view(c) for c in cards])
