"""
Promotion Link Creation (Decision Outcome A5) — write engine_signal_decision_link
rows for eligible promotion candidates.

This is the ONLY write edge in A5. It creates the binding (link_status=proposed,
decision_id=None) — it does NOT create a Decision, does NOT run the executor, does
NOT change the engine signal's status, does NOT set promoted_to_decision, does NOT
measure effect. The link is just "this signal is a candidate for a decision on this
action".

Idempotent: only `eligible` candidates are written; `blocked_already_linked` is
skipped; all other blocked_* are reported, not written. A concurrent racing insert
is caught by the unique (user_id, insight_key, action_key) constraint via a
SAVEPOINT and counted as skipped — never a duplicate, never a crash.

Flush-only — the caller owns the transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.engine_signal_decision_link import EngineSignalDecisionLink

from .candidate_engine import (
    build_promotion_candidates, EnginePromotionCandidate,
    ELIGIBLE, BLOCKED_ALREADY_LINKED,
)

CREATED = "created"
SKIPPED = "skipped"     # already linked (idempotent re-run / race)
BLOCKED = "blocked"     # not eligible (no_action / invalid / non_active)


@dataclass
class PromotionItem:
    contour: str
    signal_id: str
    canonical_insight_key: Optional[str]
    action_key: Optional[str]
    outcome: str                 # created | skipped | blocked
    promotion_status: str        # the candidate's gate result
    link_id: Optional[str] = None


@dataclass
class PromotionWriteResult:
    created: int = 0
    skipped: int = 0
    blocked: int = 0
    items: List[PromotionItem] = field(default_factory=list)


async def _create_link(db, cand: EnginePromotionCandidate, user_id: str, now: datetime):
    """Insert one link inside a SAVEPOINT; return link or None on unique-race."""
    link = EngineSignalDecisionLink(
        user_id=user_id, contour=cand.contour, signal_table=cand.signal_table,
        signal_id=cand.signal_id, insight_key=cand.canonical_insight_key,
        action_key=cand.action_key, decision_id=None, link_status="proposed",
        marketplace=cand.marketplace, sku=cand.sku, created_at=now,
    )
    try:
        async with db.begin_nested():
            db.add(link)
            await db.flush()
        return link
    except IntegrityError:
        return None   # another run already created it (race) → idempotent skip


async def promote_eligible_candidates(
    db: AsyncSession, *, user_id: str, contour: Optional[str] = None,
    listing_id: Optional[str] = None, now: Optional[datetime] = None,
) -> PromotionWriteResult:
    """Create links for eligible candidates. Idempotent, flush-only. No Decision,
    no executor, no measurement, no engine-signal mutation."""
    ts = now or datetime.utcnow()
    candidates = await build_promotion_candidates(
        db, user_id=user_id, contour=contour, listing_id=listing_id)

    res = PromotionWriteResult()
    for cand in candidates:
        if cand.promotion_status == ELIGIBLE:
            link = await _create_link(db, cand, user_id, ts)
            if link is not None:
                res.created += 1
                res.items.append(PromotionItem(cand.contour, cand.signal_id,
                                               cand.canonical_insight_key, cand.action_key,
                                               CREATED, cand.promotion_status, link.id))
            else:
                res.skipped += 1
                res.items.append(PromotionItem(cand.contour, cand.signal_id,
                                               cand.canonical_insight_key, cand.action_key,
                                               SKIPPED, cand.promotion_status))
        elif cand.promotion_status == BLOCKED_ALREADY_LINKED:
            res.skipped += 1
            res.items.append(PromotionItem(cand.contour, cand.signal_id,
                                           cand.canonical_insight_key, cand.action_key,
                                           SKIPPED, cand.promotion_status))
        else:
            res.blocked += 1
            res.items.append(PromotionItem(cand.contour, cand.signal_id,
                                           cand.canonical_insight_key, cand.action_key,
                                           BLOCKED, cand.promotion_status))

    await db.flush()
    return res
