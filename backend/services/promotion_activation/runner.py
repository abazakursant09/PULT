"""
Promotion Runner (Promotion Activation A2) — run the EXISTING promotion/bridge once
and record it. No new promotion logic, no execution, no measurement, no marketplace.

Flow:
  1. build_promotion_candidates()  → how many candidates were seen (read-only)
  2. promote_eligible_candidates() → write proposed links for eligible signals
  3. bridge_links_to_decisions()   → promote proposed links → Decision (capability-
     gated; marks the source signal promoted_to_decision + decision_id)
  4. append a promotion_run ledger row

Flush-only — the caller owns the transaction. Decision is an intent record; apply
stays manual (Decision Apply UX).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.promotion_run import PromotionRun
from services.decision_outcome.candidate_engine import build_promotion_candidates
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions


@dataclass
class PromotionRunItem:
    phase: str                 # promote | bridge
    contour: str
    outcome: str
    reason: Optional[str] = None
    decision_id: Optional[str] = None


@dataclass
class PromotionRunResult:
    candidates_seen: int
    links_created: int
    decisions_created: int
    skipped: int
    items: List[PromotionRunItem] = field(default_factory=list)
    run_id: Optional[str] = None


async def run_promotion(
    db: AsyncSession, *, user_id: str, contour: Optional[str] = None,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> PromotionRunResult:
    """Activate the existing promotion/bridge for a seller, then log the run."""
    ts = now or datetime.utcnow()

    # 1) candidates seen (read-only)
    candidates = await build_promotion_candidates(db, user_id=user_id, contour=contour)

    # 2) write proposed links for eligible candidates (idempotent — existing service)
    promo = await promote_eligible_candidates(db, user_id=user_id, contour=contour)

    # 3) promote proposed links → Decision (capability-gated — existing bridge)
    bridge = await bridge_links_to_decisions(db, user_id=user_id, now=ts)

    skipped = promo.skipped + promo.blocked + bridge.skipped

    items: List[PromotionRunItem] = []
    for it in promo.items:
        items.append(PromotionRunItem(phase="promote", contour=it.contour,
                                      outcome=it.outcome, reason=it.promotion_status))
    for it in bridge.items:
        items.append(PromotionRunItem(phase="bridge", contour=it.contour,
                                      outcome=it.outcome, reason=it.reason,
                                      decision_id=it.decision_id))

    # 4) append-only ledger
    run = PromotionRun(
        user_id=user_id, contour=contour, candidates_seen=len(candidates),
        links_created=promo.created, decisions_created=bridge.promoted, skipped=skipped,
        triggered_by=triggered_by, created_at=ts)
    db.add(run)
    await db.flush()

    return PromotionRunResult(
        candidates_seen=len(candidates), links_created=promo.created,
        decisions_created=bridge.promoted, skipped=skipped, items=items, run_id=run.id)
