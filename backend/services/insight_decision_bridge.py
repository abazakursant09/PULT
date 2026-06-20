"""
Insight → Decision bridge (Slice 1: promotion only).

Fixates a promotable production Insight into a Decision (intent layer). This is
the ONLY edge implemented here: Insight → Decision. It does NOT apply, execute,
open/close measurement, schedule, attribute, or learn. No marketplace client,
no executor, no validation.

Idempotent: one Decision per (user_id, insight_key). Re-promotion returns the
existing Decision with created=False. Uniqueness is backed by the
`uq_decision_user_insight` unique index (race-safe via IntegrityError reselect).

Marketplace-agnostic: marketplace/sku normalized through product_resolver; no
WB-only logic. Product/listing resolution against the spine ProductListing is
best-effort — a miss still produces a Decision with null product/listing.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision import Decision
from models.product_listing import ProductListing
from services.product_resolver import normalize_marketplace, normalize_sku

# Deterministic insight-type → executor action_key. Explicit by design: only the
# three mapped intents get an action_key; everything else is a manual Decision
# (action_key=None). NOTE: action_engine emits "high_rating" (NOT "rating_good")
# and it is intentionally unmapped here — no naming-mismatch carry-over.
_ACTION_KEY: dict[str, Optional[str]] = {
    "margin_crisis":   "set_price",
    "high_ad_spend":   "ad_set_bid",
    "seo_opportunity": "update_card",
    "sales_growth":    None,
    "low_stock":       None,
    "high_rating":     None,
}


@dataclass
class InsightPromotionDTO:
    """Minimal, router-free view of an Insight needed to fixate a Decision."""
    insight_key: str
    itype: str
    marketplace: Optional[str]
    sku: Optional[str]
    problem: Optional[str] = None
    cause: Optional[str] = None
    effect: Optional[str] = None
    action: Optional[str] = None
    pnl_impact: Optional[float] = None
    severity: Optional[str] = None
    is_demo: bool = False


@dataclass
class PromoteResult:
    decision_id: Optional[str]
    created: bool
    promotable: bool
    reason: Optional[str] = None


def action_key_for(itype: str) -> Optional[str]:
    """Deterministic action_key for an insight type, or None (manual)."""
    return _ACTION_KEY.get(itype)


def _is_promotable(dto: InsightPromotionDTO) -> bool:
    """False when sku is missing/unknown — such a key is a display-only anchor."""
    if dto.insight_key.endswith(":unknown"):
        return False
    sku_n = normalize_sku(dto.sku)
    return sku_n is not None and sku_n != "UNKNOWN"


async def _resolve_listing(
    db: AsyncSession, user_id: str, marketplace: Optional[str], sku: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """
    Best-effort spine resolution → (physical_product_id, listing_id). Match is
    marketplace-agnostic: canon marketplace + case-insensitive external_id vs
    normalized sku. Miss → (None, None).
    """
    mp = normalize_marketplace(marketplace)
    sku_n = normalize_sku(sku)
    if not mp or sku_n is None:
        return None, None
    listing = (
        await db.execute(
            select(ProductListing).where(
                ProductListing.user_id == user_id,
                ProductListing.marketplace == mp,
                func.upper(ProductListing.external_id) == sku_n,
            )
        )
    ).scalars().first()
    if listing is None:
        return None, None
    return listing.physical_product_id, listing.id


async def _get_existing(db: AsyncSession, user_id: str, insight_key: str) -> Optional[Decision]:
    return (
        await db.execute(
            select(Decision).where(
                Decision.user_id == user_id,
                Decision.insight_key == insight_key,
            )
        )
    ).scalars().first()


async def promote_insight_to_decision(
    db: AsyncSession, *, user_id: str, insight: InsightPromotionDTO
) -> PromoteResult:
    """
    Create or get the Decision for a promotable Insight. Promotion only — no
    apply, no measurement, no execution. See module docstring.
    """
    # ── hard blocks (no DB write) ────────────────────────────────────────────
    if insight.is_demo:
        return PromoteResult(None, created=False, promotable=False, reason="demo")
    if not user_id:
        return PromoteResult(None, created=False, promotable=False, reason="no_scope")
    if not _is_promotable(insight):
        return PromoteResult(None, created=False, promotable=False, reason="non_promotable_sku")

    # ── idempotency: one Decision per (user_id, insight_key) ──────────────────
    existing = await _get_existing(db, user_id, insight.insight_key)
    if existing is not None:
        return PromoteResult(existing.id, created=False, promotable=True, reason=None)

    phys_id, listing_id = await _resolve_listing(
        db, user_id, insight.marketplace, insight.sku
    )

    decision = Decision(
        user_id=user_id,
        insight_key=insight.insight_key,
        physical_product_id=phys_id,
        listing_id=listing_id,
        problem=(insight.problem or insight.itype),
        cause=insight.cause,
        effect=insight.effect,
        action=insight.action,
        action_key=action_key_for(insight.itype),
        pnl_impact=insight.pnl_impact,
        severity=(insight.severity or "warn"),
        source="insight",
        status="open",
        # Memory OS Phase 1: a new promotion starts a new chain at step 0.
        # Existing decisions (returned above) keep their chain untouched.
        decision_chain_id=str(uuid.uuid4()),
        step_in_chain=0,
    )
    db.add(decision)
    try:
        await db.flush()
    except IntegrityError:
        # Race: another promotion won the unique index. Return the winner.
        await db.rollback()
        winner = await _get_existing(db, user_id, insight.insight_key)
        if winner is not None:
            return PromoteResult(winner.id, created=False, promotable=True, reason=None)
        raise

    return PromoteResult(decision.id, created=True, promotable=True, reason=None)
