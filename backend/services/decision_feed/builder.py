"""
Daily Decision Feed builder (A3) — read-only aggregation of the six contours into
one prioritized "what needs my decision today" list.

Reads live engine signals (SEO / Advertising / Review / Growth / Legal) + Decision
Outcome proven effects, overlays the per-user decision_feed_state attention layer,
and returns FeedItems. It NEVER stores or duplicates a signal, NEVER creates a
signal, NEVER mutates a source table or the feed-state table. No score, no numeric
priority, no ranking weight, no forecast.

Item key reuses the Decision Outcome canonical policy: canonical 3-part insight_key
for the five engines (Review drops its review_id 4th segment), decision_id for the
Decision Outcome contour.

Ordering is simple and explainable (no magic priority):
  reopened > active > acknowledged > proven_worsened > not_evaluated >
  not_measured_yet > proven_improved > (rest), then created_at desc.

Honest degradation: a row missing a field keeps its item with that field None — the
builder never fakes an effect and never crashes on a bad row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision_feed_state import DecisionFeedState
from models.seo_signal import SeoSignal
from models.advertising_signal import AdvertisingSignal
from models.review_signal import ReviewSignal
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal

from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_outcome.effect_summary import build_effect_summaries
from services.learning_os.registry import get_action_learning_summary

# Learning OS v1 — feed enrichment gate. Show the observed learning line ONLY when
# this (marketplace, action) has at least this many measured outcomes. Below the
# gate: show nothing (never a thin, misleading sample).
_LEARNING_MIN_SAMPLE = 10
_MP_DISPLAY = {"wb": "Wildberries", "ozon": "Ozon", "yandex": "Yandex Market",
               "megamarket": "Megamarket"}


async def _learning_context(db, user_id: str, summary) -> Optional[str]:
    """Observed-only descriptive line for a MEASURED effect, gated by min sample.
    Counts only — no percentage, no probability, no forecast. None when ungated or
    no action/marketplace. Marketplace-isolated (per canonical marketplace)."""
    if not summary.action_key or not summary.marketplace:
        return None
    agg = await get_action_learning_summary(
        db, user_id=user_id, marketplace=summary.marketplace, action_key=summary.action_key)
    if agg is None or agg.total_count < _LEARNING_MIN_SAMPLE:
        return None
    mp = _MP_DISPLAY.get(agg.marketplace or "", agg.marketplace or "")
    return (f"По {mp} это решение ранее помогло в "
            f"{agg.improved_count} случаях из {agg.total_count}.")

# contour → (model, signal_table)
_ENGINES = (
    ("seo", SeoSignal, "seo_signal"),
    ("advertising", AdvertisingSignal, "advertising_signal"),
    ("review", ReviewSignal, "review_signal"),
    ("growth", GrowthSignal, "growth_signal"),
    ("legal", LegalSignal, "legal_signal"),
)

# promoted_to_decision stays live: the signal has a Decision and is awaiting the
# seller's manual apply — it must remain in the feed so "Применить решение" shows.
_LIVE = {"active", "reopened", "acknowledged", "promoted_to_decision"}
_RESOLVED_LIKE = {"resolved", "dismissed"}

# explainable order buckets (NOT a numeric priority exposed on the item)
_ORDER = {
    "reopened": 1, "active": 2, "promoted_to_decision": 3, "acknowledged": 4,
    "proven_worsened": 5, "not_evaluated": 6, "not_measured_yet": 7,
    "proven_improved": 8, "proven_unchanged": 9,
}
_DO_DEFAULT_VISIBLE = {"proven_improved", "proven_worsened", "not_evaluated", "not_measured_yet"}


@dataclass
class FeedItem:
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
    effect_status: Optional[str] = None
    effect_band: Optional[str] = None
    lifecycle_reason: Optional[str] = None
    # Learning OS v1 — optional, observed-only descriptive context for a MEASURED
    # effect (counts, marketplace-specific). None unless the minimum sample gate
    # passes. Never a percentage / score / forecast.
    learning_context: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    source_context: Mapping[str, object] = field(default_factory=dict)
    # internal sort bucket (NOT a numeric priority field for callers)
    _order_bucket: str = "active"


def _canonical_key(sig) -> Optional[str]:
    raw = sig.insight_key
    if not raw:
        return None
    entry = BY_SIGNAL_KEY.get(sig.signal_key)
    parts = raw.split(":")
    if entry and entry.carries_review_id and len(parts) == 4:
        return ":".join(parts[:3])   # drop review_id 4th segment
    return raw


def _engine_item(contour: str, table: str, sig) -> Optional[FeedItem]:
    key = _canonical_key(sig)
    if not key:
        return None
    ctx = {
        "signal_key": sig.signal_key,
        "problem_type": getattr(sig, "problem_type", None) or getattr(sig, "requirement_type", None),
        "listing_id": getattr(sig, "listing_id", None),
        # decision_id is set once the signal is promoted to a Decision — the apply
        # UX shows "Применить решение" only when this is present.
        "decision_id": getattr(sig, "decision_id", None),
    }
    rid = getattr(sig, "review_id", None)
    if rid:
        ctx["review_id"] = rid
    what = getattr(sig, "what", None)
    return FeedItem(
        item_key=key, contour=contour, source_table=table, source_id=sig.id,
        source_status=sig.status, attention_state="new",
        marketplace=sig.marketplace, sku=sig.sku,
        title=(what[:80] if what else sig.signal_key),
        what_happened=what, why_it_matters=getattr(sig, "why", None),
        meaning=getattr(sig, "meaning", None), recommended_action=getattr(sig, "what_to_do", None),
        expected_effect=getattr(sig, "expected_effect", None),
        lifecycle_reason=getattr(sig, "lifecycle_reason", None),
        created_at=sig.created_at, updated_at=getattr(sig, "updated_at", None),
        source_context={k: v for k, v in ctx.items() if v is not None},
        _order_bucket=sig.status or "active",
    )


def _do_item(summary) -> Optional[FeedItem]:
    if not summary.decision_id:
        return None
    return FeedItem(
        item_key=summary.decision_id, contour="decision_outcome",
        source_table="engine_effect_observation", source_id=summary.decision_id,
        source_status=summary.link_status, attention_state="new",
        marketplace=summary.marketplace, sku=summary.sku,
        title=summary.what_happened, what_happened=summary.what_happened,
        why_it_matters=summary.what_it_means, meaning=None,
        recommended_action=summary.next_action, expected_effect=None,
        effect_status=summary.effect_status, effect_band=summary.effect_band,
        created_at=summary.measured_at, updated_at=summary.measured_at,
        source_context=dict(summary.evidence) if summary.evidence else {},
        _order_bucket=summary.effect_status,
    )


def _ts(dt: Optional[datetime]) -> float:
    return dt.timestamp() if dt else 0.0


async def build_feed(
    db: AsyncSession, *, user_id: str, contour: Optional[str] = None,
    include_snoozed: bool = False, include_dismissed: bool = False,
    include_resolved: bool = False, limit: int = 50, now: Optional[datetime] = None,
) -> List[FeedItem]:
    """Read-only Daily Decision Feed. No writes, no signal duplication."""
    ts = now or datetime.utcnow()

    # attention overlay (read-only)
    states = (await db.execute(select(DecisionFeedState).where(
        DecisionFeedState.user_id == user_id))).scalars().all()
    by_key = {s.item_key: s for s in states}

    items: List[FeedItem] = []

    # ── engine sources ────────────────────────────────────────────────────────
    for name, model, table in _ENGINES:
        if contour and contour != name:
            continue
        rows = (await db.execute(select(model).where(model.user_id == user_id))).scalars().all()
        for sig in rows:
            live = sig.status in _LIVE
            if not live and not (include_resolved and sig.status in _RESOLVED_LIKE):
                continue
            it = _engine_item(name, table, sig)
            if it is not None:
                items.append(it)

    # ── Decision Outcome source (via the A8 read-service) ─────────────────────
    if not contour or contour == "decision_outcome":
        summaries = await build_effect_summaries(db, user_id=user_id)
        for s in summaries:
            if s.effect_status not in _DO_DEFAULT_VISIBLE and not include_resolved:
                continue   # proven_unchanged only when explicitly requested
            it = _do_item(s)
            if it is not None:
                # Learning OS v1 — attach observed learning context for measured
                # effects only (gated; observed counts, marketplace-isolated).
                if s.measured_at is not None:
                    it.learning_context = await _learning_context(db, user_id, s)
                items.append(it)

    # ── attention overlay + visibility ────────────────────────────────────────
    visible: List[FeedItem] = []
    for it in items:
        st = by_key.get(it.item_key)
        it.attention_state = st.state if st else "new"
        if it.attention_state == "snoozed":
            snoozed_active = st and st.snooze_until and st.snooze_until > ts
            if snoozed_active and not include_snoozed:
                continue
        if it.attention_state == "dismissed" and not include_dismissed:
            continue
        visible.append(it)

    # ── ordering (explainable, no numeric priority) ──────────────────────────
    visible.sort(key=lambda i: (_ORDER.get(i._order_bucket, 99), -_ts(i.created_at)))
    return visible[: max(1, limit)]
