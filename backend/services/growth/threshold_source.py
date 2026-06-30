"""
Growth threshold source (READ-ONLY) — derive per-seller GrowthThresholds from the
seller's OWN observed finance.

Replaces the permissive default (0,0,0) that fired a growth signal on almost every
listing. Anchors the numeric thresholds to the seller's own ImportedFinanceRow
(median per-sku revenue / net_profit), so only above-typical listings surface as
growth candidates. `low_stock_units` is a fixed DEFINITION of "low stock", not a
finance-derived number.

Observed-only: the seller's own data — never a competitor figure, never a forecast,
never a fabricated number. SELECT only — never writes. Knows nothing about the
Advisory Runtime / scheduler / ProducerSpec — it only returns GrowthThresholds.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from services.growth.rules import GrowthThresholds

# A definition of "low stock" (units), NOT a finance-derived threshold.
_LOW_STOCK_UNITS = 5


async def derive_growth_thresholds(
    db: AsyncSession, user_id: str, *, now: Optional[datetime] = None,
) -> GrowthThresholds:
    """Per-seller GrowthThresholds from observed finance. No finance rows → all-None
    thresholds (rules resolve NOT_EVALUATED → no signals; honest absence, never a
    permissive 0). Deterministic (median over the seller's per-sku totals)."""
    rows = (await db.execute(
        select(
            ImportedFinanceRow.sku,
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0),
        )
        .where(ImportedFinanceRow.user_id == user_id, ImportedFinanceRow.sku.isnot(None))
        .group_by(ImportedFinanceRow.sku))).all()

    if not rows:
        return GrowthThresholds()   # honest: no data → no thresholds → no signals

    revenues = [float(r[1]) for r in rows]
    profits = [float(r[2]) for r in rows if float(r[2]) > 0]

    return GrowthThresholds(
        low_stock_units=_LOW_STOCK_UNITS,
        min_revenue_for_growth_signal=float(median(revenues)),
        # median of the seller's POSITIVE per-sku net profit; None when none positive
        min_net_profit_for_growth_signal=(float(median(profits)) if profits else None),
    )
