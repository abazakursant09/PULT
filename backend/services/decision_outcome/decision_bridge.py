"""
Decision Bridge + Action Binding (Decision Outcome A6) — turn proposed engine
links into real Decisions on the Decision Spine.

For each proposed engine_signal_decision_link with no decision_id yet:
  1. require an action_key (links only exist for bound actions, but guard anyway);
  2. CAPABILITY-GATE it against services.marketplace.action_catalog — the action
     must be a real catalog action AND supported for the signal's marketplace
     (stop_auto_promotion → WB/Ozon; Yandex is gated impossible). No capability →
     SKIP, leave the link proposed, never invent an action;
  3. create (or idempotently reuse) the Decision via the existing Spine promoter
     `promote_insight_to_decision` — same uq_decision_user_insight_action dedup;
  4. write decision_id + link_status=promoted onto the link;
  5. mark the SOURCE engine signal status=promoted_to_decision and decision_id —
     and ONLY now (a signal is never "promoted" before its Decision exists).

Does NOT execute the action, NOT open measurement, NOT write an execution log, NOT
create an effect observation. Uses the canonical 3-part insight_key on the Decision;
for Review the review_id is preserved (signal row keeps it + it is carried into the
Decision cause and the bridge result). Flush-only — caller owns the transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.seo_signal import SeoSignal
from models.advertising_signal import AdvertisingSignal
from models.review_signal import ReviewSignal
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal
from models.pricing_signal import PricingSignal
from models.operations_signal import OperationsSignal

from services.marketplace import action_catalog
from services.product_resolver import normalize_marketplace
from services.insight_decision_bridge import promote_insight_to_decision, InsightPromotionDTO

_MODELS = {
    "seo": SeoSignal, "advertising": AdvertisingSignal, "review": ReviewSignal,
    "growth": GrowthSignal, "legal": LegalSignal, "pricing": PricingSignal,
    "operations": OperationsSignal,
}
# spec.marketplace=None → WB/Ozon only (compared in canon slugs from
# normalize_marketplace, where "wildberries"→"wb").
_NO_MP_ACTION_SUPPORTS = {"wb", "ozon"}

PROMOTED = "promoted"
SKIPPED_NO_ACTION = "skipped_no_action"
SKIPPED_NO_CAPABILITY = "skipped_unsupported_capability"
SKIPPED_SIGNAL_MISSING = "skipped_signal_missing"


@dataclass
class BridgeItem:
    link_id: str
    contour: str
    signal_id: str
    canonical_insight_key: Optional[str]
    action_key: Optional[str]
    decision_id: Optional[str]
    outcome: str
    reason: Optional[str] = None
    source_context: Mapping[str, object] = field(default_factory=dict)


@dataclass
class BridgeResult:
    promoted: int = 0
    skipped: int = 0
    items: List[BridgeItem] = field(default_factory=list)


def capability_supported(action_key: Optional[str], marketplace: Optional[str]) -> bool:
    """Deterministic capability gate from the action catalog (no credentials)."""
    if not action_key or action_key not in action_catalog.known_actions():
        return False
    spec = action_catalog.get(action_key)
    mp = normalize_marketplace(marketplace)
    if spec.marketplace is None:
        return mp in _NO_MP_ACTION_SUPPORTS
    return normalize_marketplace(spec.marketplace) == mp


def _dto(link: EngineSignalDecisionLink, sig, review_id: Optional[str]) -> InsightPromotionDTO:
    cause = getattr(sig, "why", None)
    if review_id:
        cause = f"review_id={review_id}" + (f"; {cause}" if cause else "")
    return InsightPromotionDTO(
        insight_key=link.insight_key, itype=link.contour, marketplace=link.marketplace,
        sku=link.sku, problem=getattr(sig, "what", None) or link.insight_key,
        cause=cause, effect=getattr(sig, "expected_effect", None),
        action=getattr(sig, "what_to_do", None),
        severity=getattr(sig, "priority_level", None),
    )


async def bridge_links_to_decisions(
    db: AsyncSession, *, user_id: str, now: Optional[datetime] = None,
) -> BridgeResult:
    """Promote proposed engine links into Decisions. Flush-only. No execution, no
    measurement."""
    ts = now or datetime.utcnow()
    links = (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.user_id == user_id,
        EngineSignalDecisionLink.decision_id.is_(None),
        EngineSignalDecisionLink.link_status == "proposed"))).scalars().all()

    res = BridgeResult()
    for link in links:
        ctx: dict = {}
        if not link.action_key:
            res.skipped += 1
            res.items.append(BridgeItem(link.id, link.contour, link.signal_id, link.insight_key,
                                        link.action_key, None, SKIPPED_NO_ACTION,
                                        "link has no action_key"))
            continue
        if not capability_supported(link.action_key, link.marketplace):
            res.skipped += 1
            res.items.append(BridgeItem(link.id, link.contour, link.signal_id, link.insight_key,
                                        link.action_key, None, SKIPPED_NO_CAPABILITY,
                                        f"{link.action_key} not supported for {link.marketplace}"))
            continue

        model = _MODELS.get(link.contour)
        sig = await db.get(model, link.signal_id) if model is not None else None
        if sig is None:
            res.skipped += 1
            res.items.append(BridgeItem(link.id, link.contour, link.signal_id, link.insight_key,
                                        link.action_key, None, SKIPPED_SIGNAL_MISSING,
                                        "source signal not found"))
            continue

        review_id = getattr(sig, "review_id", None)
        if review_id:
            ctx["review_id"] = review_id

        promote = await promote_insight_to_decision(
            db, user_id=user_id, insight=_dto(link, sig, review_id), action_key=link.action_key)
        if not promote.decision_id:
            res.skipped += 1
            res.items.append(BridgeItem(link.id, link.contour, link.signal_id, link.insight_key,
                                        link.action_key, None, SKIPPED_NO_CAPABILITY,
                                        promote.reason or "not_promotable", source_context=ctx))
            continue

        # bind the link and mark the source signal — only now that a Decision exists
        link.decision_id = promote.decision_id
        link.link_status = PROMOTED
        sig.status = "promoted_to_decision"
        if hasattr(sig, "decision_id"):
            sig.decision_id = promote.decision_id
        if hasattr(sig, "updated_at"):
            sig.updated_at = ts

        res.promoted += 1
        res.items.append(BridgeItem(link.id, link.contour, link.signal_id, link.insight_key,
                                    link.action_key, promote.decision_id, PROMOTED, None,
                                    source_context=ctx))

    await db.flush()
    return res
