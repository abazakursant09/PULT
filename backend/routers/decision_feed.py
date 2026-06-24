"""
Daily Decision Feed API (A4) — read the unified feed + manage per-user attention
state. Read + attention-only writes. NO change to source signals, NO change to
Decision Outcome, NO decision/action creation, NO executor, NO ranking, NO UI.

The four mutations touch ONLY decision_feed_state. An item_key that is not in the
caller's current feed returns 404 (no junk state is created). item_key must be
canonical — a raw 4-part Review key (rev_…:mp:sku:review_id) is rejected.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.decision_feed_state import DecisionFeedState

from services.decision_feed.builder import build_feed, FeedItem

router = APIRouter()


# ── views ────────────────────────────────────────────────────────────────────

class FeedItemView(BaseModel):
    item_key: str
    contour: str
    source_table: str
    source_id: str
    source_status: Optional[str]
    attention_state: str
    marketplace: Optional[str]
    sku: Optional[str]
    title: Optional[str]
    what_happened: Optional[str]
    why_it_matters: Optional[str]
    meaning: Optional[str]
    recommended_action: Optional[str]
    expected_effect: Optional[str]
    effect_status: Optional[str]
    effect_band: Optional[str]
    learning_context: Optional[str]
    learning_explain: Optional[dict]
    lifecycle_reason: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    source_context: Optional[dict]


def _view(i: FeedItem) -> FeedItemView:
    return FeedItemView(
        item_key=i.item_key, contour=i.contour, source_table=i.source_table, source_id=i.source_id,
        source_status=i.source_status, attention_state=i.attention_state,
        marketplace=i.marketplace, sku=i.sku, title=i.title, what_happened=i.what_happened,
        why_it_matters=i.why_it_matters, meaning=i.meaning, recommended_action=i.recommended_action,
        expected_effect=i.expected_effect, effect_status=i.effect_status, effect_band=i.effect_band,
        learning_context=i.learning_context,
        learning_explain=dict(i.learning_explain) if i.learning_explain else None,
        lifecycle_reason=i.lifecycle_reason,
        created_at=i.created_at.isoformat() if i.created_at else None,
        updated_at=i.updated_at.isoformat() if i.updated_at else None,
        source_context=dict(i.source_context) if i.source_context else {},
    )


class FeedResponse(BaseModel):
    items: list[FeedItemView]
    total: int


# ── GET /decision-feed ───────────────────────────────────────────────────────

@router.get("/decision-feed", response_model=FeedResponse)
async def get_decision_feed(
    contour: Optional[str] = None,
    include_snoozed: bool = False,
    include_dismissed: bool = False,
    include_resolved: bool = False,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedResponse:
    items = await build_feed(
        db, user_id=current_user.id, contour=contour, include_snoozed=include_snoozed,
        include_dismissed=include_dismissed, include_resolved=include_resolved, limit=limit)
    return FeedResponse(items=[_view(i) for i in items], total=len(items))


# ── attention mutations (decision_feed_state ONLY) ───────────────────────────

class SnoozeBody(BaseModel):
    until: datetime


class FeedStateResponse(BaseModel):
    ok: bool
    item_key: str
    state: str
    snooze_until: Optional[str] = None


def _reject_non_canonical(item_key: str) -> None:
    # canonical keys: engine insight_key (2 colons) or decision_id (0 colons).
    # a raw 4-part Review key has 3 colons → reject.
    if item_key.count(":") > 2:
        raise HTTPException(status_code=400, detail="non-canonical item_key (raw key not allowed)")


async def _find_item(db, user_id: str, item_key: str) -> FeedItem:
    """Locate the item in the caller's full feed (all states) or 404."""
    items = await build_feed(db, user_id=user_id, include_snoozed=True,
                             include_dismissed=True, include_resolved=True, limit=100000)
    for i in items:
        if i.item_key == item_key:
            return i
    raise HTTPException(status_code=404, detail="feed item not found")


async def _upsert_state(db, user_id: str, item_key: str, contour: str, *,
                        state: str, snooze_until=None, last_seen_at=None) -> DecisionFeedState:
    now = datetime.utcnow()
    row = (await db.execute(select(DecisionFeedState).where(
        DecisionFeedState.user_id == user_id,
        DecisionFeedState.item_key == item_key))).scalars().first()
    if row is None:
        row = DecisionFeedState(user_id=user_id, item_key=item_key, contour=contour,
                                state=state, snooze_until=snooze_until, last_seen_at=last_seen_at,
                                created_at=now, updated_at=now)
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            row = (await db.execute(select(DecisionFeedState).where(
                DecisionFeedState.user_id == user_id,
                DecisionFeedState.item_key == item_key))).scalars().one()
            row.state = state
            if snooze_until is not None:
                row.snooze_until = snooze_until
            if last_seen_at is not None:
                row.last_seen_at = last_seen_at
            row.updated_at = now
    else:
        row.state = state
        if snooze_until is not None:
            row.snooze_until = snooze_until
        if last_seen_at is not None:
            row.last_seen_at = last_seen_at
        row.updated_at = now
    await db.commit()
    return row


async def _mutate(db, user, item_key, *, state, snooze_until=None, last_seen_at=None) -> FeedStateResponse:
    _reject_non_canonical(item_key)
    item = await _find_item(db, user.id, item_key)
    row = await _upsert_state(db, user.id, item_key, item.contour,
                              state=state, snooze_until=snooze_until, last_seen_at=last_seen_at)
    return FeedStateResponse(ok=True, item_key=item_key, state=row.state,
                             snooze_until=row.snooze_until.isoformat() if row.snooze_until else None)


@router.post("/decision-feed/{item_key}/seen", response_model=FeedStateResponse)
async def feed_seen(item_key: str, current_user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)) -> FeedStateResponse:
    return await _mutate(db, current_user, item_key, state="seen", last_seen_at=datetime.utcnow())


@router.post("/decision-feed/{item_key}/snooze", response_model=FeedStateResponse)
async def feed_snooze(item_key: str, body: SnoozeBody,
                      current_user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)) -> FeedStateResponse:
    until = body.until
    if until.tzinfo is not None:
        until = until.replace(tzinfo=None)
    if until <= datetime.utcnow():
        raise HTTPException(status_code=422, detail="snooze_until must be in the future")
    return await _mutate(db, current_user, item_key, state="snoozed", snooze_until=until)


@router.post("/decision-feed/{item_key}/dismiss", response_model=FeedStateResponse)
async def feed_dismiss(item_key: str, current_user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)) -> FeedStateResponse:
    return await _mutate(db, current_user, item_key, state="dismissed")


@router.post("/decision-feed/{item_key}/act", response_model=FeedStateResponse)
async def feed_act(item_key: str, current_user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)) -> FeedStateResponse:
    return await _mutate(db, current_user, item_key, state="acted")
