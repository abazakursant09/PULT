"""
Today (One Morning Truth, A17) — the single product layer for "what to do today".

A THIN, READ-ONLY projection over the canonical Daily Decision Feed (build_feed). It
is the one place every surface (Telegram / Dashboard / Copilot) will later read so they
all answer the same question identically. This slice ONLY adds the service — it
repoints nothing; no existing surface uses it yet.

Hard boundaries (by construction):
  * build_feed is the ONLY data source — Today fetches nothing else, joins nothing,
    reads no table directly.
  * Today computes no recommendation of its own — it re-frames already-built FeedItems.
  * Order is PRESERVED exactly as build_feed returned it (build_feed already owns the
    explainable lifecycle/severity/recency ordering). Today never re-sorts.
  * Read-only — build_feed is read-only and Today adds no write. Never commits, never
    touches a table.
  * top_action() is ALWAYS the first build_today() item (or None when empty) — the same
    item the feed shows first.

TodayAction carries the product-facing doctrine fields verbatim from the FeedItem
(what/why/meaning/recommended_action/expected_effect) plus identity and the observed
explanation fields — never a score, never a forecast, never a fabricated number.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services.decision_feed.builder import build_feed, FeedItem


@dataclass(frozen=True)
class TodayAction:
    """One product-facing 'what to do today' item — a verbatim re-frame of a FeedItem.
    Internal feed sort keys (_order_bucket/_priority_bucket) are intentionally not
    carried; ordering is already baked into the list position."""
    item_key: str
    contour: str
    marketplace: Optional[str]
    sku: Optional[str]
    title: Optional[str]
    # PULT doctrine, 5 parts — passed through unchanged
    what_happened: Optional[str]
    why_it_matters: Optional[str]
    meaning: Optional[str]
    recommended_action: Optional[str]
    expected_effect: Optional[str]
    # lifecycle / attention / effect (observed)
    source_status: Optional[str]
    attention_state: str
    effect_status: Optional[str]
    effect_band: Optional[str]
    # alternatives grouping + lever (observed; None for plain engine items)
    group_key: Optional[str]
    action_key: Optional[str]
    action_role: Optional[str]
    # observed learning explanation (None unless the feed attached it)
    learning_context: Optional[str]
    learning_explain: Optional[Mapping[str, object]]
    ranking_explain: Optional[Mapping[str, object]]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


def _project(i: FeedItem) -> TodayAction:
    """1:1 re-frame of a FeedItem — fields verbatim, nothing computed."""
    return TodayAction(
        item_key=i.item_key, contour=i.contour, marketplace=i.marketplace, sku=i.sku,
        title=i.title, what_happened=i.what_happened, why_it_matters=i.why_it_matters,
        meaning=i.meaning, recommended_action=i.recommended_action,
        expected_effect=i.expected_effect, source_status=i.source_status,
        attention_state=i.attention_state, effect_status=i.effect_status,
        effect_band=i.effect_band, group_key=i.group_key, action_key=i.action_key,
        action_role=i.action_role, learning_context=i.learning_context,
        learning_explain=i.learning_explain, ranking_explain=i.ranking_explain,
        created_at=i.created_at, updated_at=i.updated_at,
    )


async def build_today(
    db: AsyncSession, *, user_id: str, contour: Optional[str] = None,
    include_snoozed: bool = False, include_dismissed: bool = False,
    include_resolved: bool = False, limit: int = 50, now: Optional[datetime] = None,
) -> List[TodayAction]:
    """The ordered 'what to do today' list — build_feed's items, re-framed 1:1, order
    preserved. Read-only. Same kwargs as build_feed so callers can scope identically."""
    items = await build_feed(
        db, user_id=user_id, contour=contour, include_snoozed=include_snoozed,
        include_dismissed=include_dismissed, include_resolved=include_resolved,
        limit=limit, now=now)
    return [_project(i) for i in items]


async def top_action(
    db: AsyncSession, *, user_id: str, contour: Optional[str] = None,
    include_snoozed: bool = False, include_dismissed: bool = False,
    include_resolved: bool = False, limit: int = 50, now: Optional[datetime] = None,
) -> Optional[TodayAction]:
    """The single highest-priority thing to do today — ALWAYS the first build_today()
    item, or None when there is nothing live."""
    items = await build_today(
        db, user_id=user_id, contour=contour, include_snoozed=include_snoozed,
        include_dismissed=include_dismissed, include_resolved=include_resolved,
        limit=limit, now=now)
    return items[0] if items else None
