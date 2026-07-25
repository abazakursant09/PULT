"""
Advertising threshold source (READ-ONLY) — derive AdvertisingThresholds from the
seller's OWN observed finance, so the Advisory Runtime producer can run unattended.

Mirrors growth A11 (services/growth/threshold_source): AdvertisingThresholds have NO
defaults and, until now, came only from the POST /advertising/audit request body. A
scheduler-run producer has no request body, so it needs adapter-owned thresholds
anchored to the seller's own observed data.

Derived floors are medians of the seller's own per-sku finance (only above-typical
listings surface — no fabricated numbers, no competitor data, no forecast). The two
non-finance thresholds are fixed DEFINITIONS, not derived numbers, and are inert under
a finance-only snapshot (stock_units / days_to_oos are unavailable there, so their
rules stay NOT_EVALUATED regardless of value).

Observed-only: the seller's own data. SELECT only — never writes. Knows nothing about
the Advisory Runtime / scheduler / ProducerSpec — it only returns AdvertisingThresholds
(or None when there is nothing to anchor to).
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from services.advertising.snapshot import AdvertisingThresholds

# Fixed DEFINITIONS (not finance-derived, not tuning):
#   low margin ≡ negative margin; low stock ≡ 5 units (project-wide); oos risk ≡ 7 days.
# The last two are inert under a finance-only snapshot (their fields are unavailable →
# ad_on_low_stock / ad_on_oos_risk stay NOT_EVALUATED regardless of the value).
_LOW_MARGIN_THRESHOLD = 0.0
_LOW_STOCK_UNITS = 5
_OOS_RISK_DAYS = 7.0


async def derive_advertising_thresholds(
    db: AsyncSession, user_id: str, *, now: Optional[datetime] = None,
) -> Optional[AdvertisingThresholds]:
    """Per-seller AdvertisingThresholds from observed finance. Anchored to the seller's
    OWN ad-spending listings (median revenue / ad_spend / DRR). Returns None when the
    seller has no ad-spending finance — honest absence, so the producer emits nothing
    (never a fabricated signal). Deterministic (medians over per-sku totals)."""
    rows = (await db.execute(
        select(
            ImportedFinanceRow.sku,
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0),
            func.coalesce(func.sum(ImportedFinanceRow.ad_spend), 0.0),
        )
        .where(ImportedFinanceRow.user_id == user_id, ImportedFinanceRow.sku.isnot(None),
               ImportedFinanceRow.source == "csv")   # single-source (1.4.5H2)
        .group_by(ImportedFinanceRow.sku))).all()

    # advertising is about AD spend — only ad-spending listings anchor the baseline
    spending = [(float(r[1]), float(r[2])) for r in rows if float(r[2]) > 0]
    if not spending:
        return None   # honest: no ad spend → nothing to anchor → no signals

    revenues = [rev for rev, _ in spending]
    ad_spends = [ad for _, ad in spending]
    drrs = [ad / rev * 100.0 for rev, ad in spending if rev > 0]
    if not drrs:                      # ad spend but no revenue anywhere → no DRR anchor
        return None

    return AdvertisingThresholds(
        max_drr=float(median(drrs)),                       # seller's own typical DRR ceiling
        min_revenue_for_signal=float(median(revenues)),    # floor: meaningful revenue
        min_ad_spend_for_signal=float(median(ad_spends)),  # floor: meaningful ad spend
        low_margin_threshold=_LOW_MARGIN_THRESHOLD,        # definition: unprofitable = margin < 0
        low_stock_units=_LOW_STOCK_UNITS,                  # definition (inert under finance snapshot)
        oos_risk_days=_OOS_RISK_DAYS,                      # definition (inert under finance snapshot)
    )
