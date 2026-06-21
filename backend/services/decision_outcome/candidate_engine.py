"""
Promotion Candidate Engine (Decision Outcome A4) — classify which normalized engine
signals COULD become a Decision, without creating anything.

Read-only. Takes the A3 canonical snapshot, checks each item against the existing
engine_signal_decision_link ledger, and emits EnginePromotionCandidate objects with
a promotion_status. It NEVER creates a Decision, NEVER writes a link, NEVER runs
measurement/execution, NEVER does capability gating, and NEVER invents an action.

promotion_status (first failing gate wins):
  blocked_invalid_signal      — A3 returned an InvalidSignalItem (bad/unknown key)
  blocked_non_active_status   — signal is not active/reopened (acknowledged /
                                resolved / dismissed / promoted)
  blocked_no_action           — registry action_key is None (no executor binding
                                yet — honest, A6 capability gating will fill it)
  blocked_already_linked      — a link already exists for (user, canonical_insight_key,
                                action_key)
  eligible                    — passes every gate

Today action_key is None for all types, so most candidates are honestly
blocked_no_action — A4 surfaces that, it does not fabricate an action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.engine_signal_decision_link import EngineSignalDecisionLink

from .snapshot import build_signal_snapshot, EngineSignalSnapshot, InvalidSignalItem

ELIGIBLE = "eligible"
BLOCKED_NO_ACTION = "blocked_no_action"
BLOCKED_INVALID = "blocked_invalid_signal"
BLOCKED_NON_ACTIVE = "blocked_non_active_status"
BLOCKED_ALREADY_LINKED = "blocked_already_linked"

_PROMOTABLE_STATUS = {"active", "reopened"}


@dataclass(frozen=True)
class EnginePromotionCandidate:
    contour: str
    signal_table: str
    signal_id: str
    raw_insight_key: Optional[str]
    canonical_insight_key: Optional[str]
    marketplace: Optional[str]
    sku: Optional[str]
    status: Optional[str]
    action_key: Optional[str]
    metric_key: Optional[str]
    promotion_status: str
    reason: Optional[str]
    source_context: Mapping[str, object] = field(default_factory=dict)


def _from_invalid(item: InvalidSignalItem) -> EnginePromotionCandidate:
    return EnginePromotionCandidate(
        contour=item.contour, signal_table=item.signal_table, signal_id=item.signal_id,
        raw_insight_key=item.raw_insight_key, canonical_insight_key=None,
        marketplace=None, sku=None, status=None, action_key=None, metric_key=None,
        promotion_status=BLOCKED_INVALID, reason=item.reason, source_context=item.source_context,
    )


def _classify(snap: EngineSignalSnapshot, linked: set) -> EnginePromotionCandidate:
    status, reason = ELIGIBLE, None
    if snap.status not in _PROMOTABLE_STATUS:
        status, reason = BLOCKED_NON_ACTIVE, f"status={snap.status}"
    elif snap.action_key is None:
        status, reason = BLOCKED_NO_ACTION, "no executor action bound for this signal type"
    elif (snap.canonical_insight_key, snap.action_key) in linked:
        status, reason = BLOCKED_ALREADY_LINKED, "link already exists for this insight/action"
    return EnginePromotionCandidate(
        contour=snap.contour, signal_table=snap.signal_table, signal_id=snap.signal_id,
        raw_insight_key=snap.raw_insight_key, canonical_insight_key=snap.canonical_insight_key,
        marketplace=snap.marketplace, sku=snap.sku, status=snap.status,
        action_key=snap.action_key, metric_key=snap.metric_key,
        promotion_status=status, reason=reason, source_context=snap.source_context,
    )


async def build_promotion_candidates(
    db: AsyncSession, *, user_id: str, contour: Optional[str] = None,
    listing_id: Optional[str] = None,
) -> List[EnginePromotionCandidate]:
    """Classify the seller's engine signals into promotion candidates. Read-only.

    No status filter is applied to the fetch — non-active signals are returned as
    blocked_non_active_status so the picture is honest and complete."""
    items = await build_signal_snapshot(db, user_id=user_id, contour=contour, listing_id=listing_id)

    # existing links → dedup set keyed on the CANONICAL key (Review-normalized)
    links = (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.user_id == user_id))).scalars().all()
    linked = {(l.insight_key, l.action_key) for l in links}

    out: List[EnginePromotionCandidate] = []
    for item in items:
        if isinstance(item, InvalidSignalItem):
            out.append(_from_invalid(item))
        else:
            out.append(_classify(item, linked))
    return out
