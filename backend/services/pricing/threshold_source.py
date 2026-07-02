"""
Pricing threshold source (READ-ONLY) — derive PricingThresholds from the seller's OWN
observed finance, so the Advisory Runtime producer can run unattended.

Mirrors growth A11 / advertising Phase 1.1. PricingThresholds default to None, and a
runtime run has no request body, so the producer needs an adapter-owned anchor.

Derived:
  * min_revenue_for_pricing_signal = median of the seller's per-sku observed revenue
    (only meaningful-revenue listings surface; enables negative_margin).

Deliberately NOT derived:
  * target_margin_pct = None — there is no canonical seller source for a target margin
    (the pricing snapshot reads only PricingRule.min_price as a floor, never a target).
    Leaving it None is honest: margin_below_target stays NOT_EVALUATED rather than
    surfacing against a fabricated target. negative_margin (losing money) and
    price_below_floor (a seller rule) still evaluate.

Observed-only, SELECT-only, no marketplace calls, no writes. Returns None when the
seller has no finance — the producer then emits nothing (honest absence).
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from services.pricing.rules import PricingThresholds


async def derive_pricing_thresholds(
    db: AsyncSession, user_id: str, *, now: Optional[datetime] = None,
) -> Optional[PricingThresholds]:
    """Per-seller PricingThresholds from observed finance. min_revenue anchored to the
    seller's own median per-sku revenue; target_margin left None (no canonical source).
    Returns None when there is no finance — honest absence, no fabricated signals."""
    rows = (await db.execute(
        select(
            ImportedFinanceRow.sku,
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0),
        )
        .where(ImportedFinanceRow.user_id == user_id, ImportedFinanceRow.sku.isnot(None))
        .group_by(ImportedFinanceRow.sku))).all()

    revenues = [float(r[1]) for r in rows if float(r[1]) > 0]
    if not revenues:
        return None   # honest: no observed revenue → nothing to anchor → no signals

    return PricingThresholds(
        min_revenue_for_pricing_signal=float(median(revenues)),
        target_margin_pct=None,   # no canonical target-margin source → margin_below_target NOT_EVALUATED
    )
