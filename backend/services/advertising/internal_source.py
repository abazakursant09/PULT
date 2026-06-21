"""
Internal AdvertisingSnapshot source (A3) — build a money snapshot from EXISTING
PULT data (ImportedFinanceRow), no external/MP API.

ImportedFinanceRow already carries revenue / net_profit / ad_spend / quantity per
sku, so DRR and ad-impact on margin are computed from finance the seller already
imported. Whatever PULT does not store (orders count, stock, context) is HONESTLY
marked unavailable; thresholds are supplied from outside (never invented). SEO
cross-contour counts come from SeoSignal when a listing_id is given.

Marketplace-agnostic: reads generic finance/signal models, never a marketplace
client.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from models.seo_signal import SeoSignal

from .snapshot import AdvertisingSnapshot, AdvertisingThresholds, AdvertisingDataUnavailable

# Canonical marketplace → finance-row aliases (imports may store raw labels).
_ALIASES = {
    "wb": ("wb", "wildberries"), "wildberries": ("wb", "wildberries"),
    "ozon": ("ozon",),
    "yandex": ("yandex", "yandex_market", "ym"), "ym": ("yandex", "yandex_market", "ym"),
}

_SnapshotResult = object  # AdvertisingSnapshot | AdvertisingDataUnavailable


def _aliases(mp: Optional[str]) -> tuple[str, ...]:
    key = (mp or "").strip().lower()
    return _ALIASES.get(key, (key,))


async def build_snapshot_from_finance(
    db: AsyncSession, *, user_id: str, marketplace: str, sku: Optional[str],
    listing_id: Optional[str] = None,
    thresholds: Optional[AdvertisingThresholds] = None,
    now: Optional[datetime] = None,
):
    """AdvertisingSnapshot from imported finance, or honest AdvertisingDataUnavailable."""
    if db is None:
        return AdvertisingDataUnavailable(marketplace, "no_db_context")
    if not sku:
        return AdvertisingDataUnavailable(marketplace, "insufficient_data", "sku required")

    row = (await db.execute(
        select(
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0),
            func.coalesce(func.sum(ImportedFinanceRow.ad_spend), 0.0),
            func.coalesce(func.sum(ImportedFinanceRow.quantity), 0),
            func.count(),
        ).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace.in_(_aliases(marketplace)),
            ImportedFinanceRow.sku == str(sku),
        )
    )).one()
    revenue, net_profit, ad_spend, units, n = (
        float(row[0]), float(row[1]), float(row[2]), int(row[3]), int(row[4]))
    if n == 0:
        return AdvertisingDataUnavailable(marketplace, "finance_missing")

    margin = (net_profit / revenue * 100.0) if revenue > 0 else None
    drr = (ad_spend / revenue * 100.0) if revenue > 0 else None

    availability = {
        "revenue": True, "net_profit": True, "ad_spend": True, "units_sold": True,
        "margin": margin is not None, "drr": drr is not None,
        "orders": False,            # finance has quantity, not order count
        "stock_units": False, "days_to_oos": False,
        "category": False, "price_band": False, "margin_band": False,
        "active_seo_problems": False, "critical_seo_problems": False,
        "thresholds": thresholds is not None,
    }

    seo_active: Optional[int] = None
    seo_critical: Optional[int] = None
    if listing_id:
        sigs = (await db.execute(select(SeoSignal).where(
            SeoSignal.user_id == user_id, SeoSignal.listing_id == listing_id,
            SeoSignal.status.in_(("active", "reopened"))))).scalars().all()
        seo_active = len(sigs)
        seo_critical = sum(1 for s in sigs if s.priority_level == "critical")
        availability["active_seo_problems"] = True
        availability["critical_seo_problems"] = True

    return AdvertisingSnapshot(
        listing_id=listing_id, marketplace=marketplace, sku=sku,
        captured_at=now or datetime.utcnow(), source="finance",
        revenue=revenue, net_profit=net_profit, ad_spend=ad_spend,
        orders=None, units_sold=units, margin=margin, drr=drr,
        stock_units=None, days_to_oos=None,
        active_seo_problems=seo_active, critical_seo_problems=seo_critical,
        category=None, price_band=None, margin_band=None,
        thresholds=thresholds, field_availability=availability,
    )
