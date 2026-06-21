"""
Internal GrowthSnapshot source (Growth A3) — aggregate EXISTING PULT data into one
opportunity snapshot. No external/MP API, no forecast, no competitors, no score.

Sources (all already stored by PULT):
  finance      ImportedFinanceRow   → revenue / net_profit / ad_spend / units → margin, drr
  seo          SeoSignal            → active / critical live signal counts (needs listing_id)
  reviews      ReviewSignal         → active / risk live signal counts (user-wide)
  operations   ImportedProductRow   → stock_units
  context      Product              → category; margin_band classified from margin

Whatever PULT does not store is HONESTLY marked unavailable (availability=False) —
never zero-filled, never invented. Finance is the anchor: no finance rows for the
sku → GrowthDataUnavailable("finance_missing"). days_to_oos needs a sales velocity
over a known window that PULT does not store → always unavailable (no forecast).

Marketplace-agnostic: reads generic models, never a marketplace client.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.product import Product
from models.seo_signal import SeoSignal
from models.review_signal import ReviewSignal

from .snapshot import GrowthSnapshot, GrowthDataUnavailable

# Canonical marketplace → stored-row aliases (imports may store raw labels).
_ALIASES = {
    "wb": ("wb", "wildberries"), "wildberries": ("wb", "wildberries"),
    "ozon": ("ozon",),
    "yandex": ("yandex", "yandex_market", "ym"), "ym": ("yandex", "yandex_market", "ym"),
}
_LIVE = ("active", "reopened")


def _aliases(mp: Optional[str]) -> tuple[str, ...]:
    key = (mp or "").strip().lower()
    return _ALIASES.get(key, (key,))


def _margin_band(margin: Optional[float]) -> Optional[str]:
    """Classify an already-known margin into a band. No forecast — pure bucketing."""
    if margin is None:
        return None
    if margin <= 0:
        return "negative"
    if margin < 10:
        return "low"
    if margin < 20:
        return "medium"
    return "high"


async def build_snapshot_from_internal(
    db: AsyncSession, *, user_id: str, marketplace: str, sku: Optional[str],
    listing_id: Optional[str] = None, now: Optional[datetime] = None,
):
    """GrowthSnapshot aggregated from internal PULT data, or honest GrowthDataUnavailable."""
    if db is None:
        return GrowthDataUnavailable(marketplace, "no_db_context")
    if not sku:
        return GrowthDataUnavailable(marketplace, "insufficient_data", "sku required")

    aliases = _aliases(marketplace)

    # ── finance (anchor) ──────────────────────────────────────────────────────
    frow = (await db.execute(
        select(
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0),
            func.coalesce(func.sum(ImportedFinanceRow.ad_spend), 0.0),
            func.coalesce(func.sum(ImportedFinanceRow.quantity), 0),
            func.count(),
        ).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace.in_(aliases),
            ImportedFinanceRow.sku == str(sku),
        )
    )).one()
    revenue, net_profit, ad_spend, units, n = (
        float(frow[0]), float(frow[1]), float(frow[2]), int(frow[3]), int(frow[4]))
    if n == 0:
        return GrowthDataUnavailable(marketplace, "finance_missing")

    margin = (net_profit / revenue * 100.0) if revenue > 0 else None
    drr = (ad_spend / revenue * 100.0) if revenue > 0 else None

    # ── operations (stock) ────────────────────────────────────────────────────
    stock_units = (await db.execute(
        select(ImportedProductRow.stock).where(
            ImportedProductRow.user_id == user_id,
            ImportedProductRow.marketplace.in_(aliases),
            ImportedProductRow.sku == str(sku),
            ImportedProductRow.stock.isnot(None),
        ).order_by(ImportedProductRow.created_at.desc()).limit(1)
    )).scalar()

    # ── context (category) ────────────────────────────────────────────────────
    category = (await db.execute(
        select(Product.category).where(
            Product.user_id == user_id, Product.sku == str(sku),
            Product.category.isnot(None),
        ).limit(1)
    )).scalar()

    # ── seo cross-contour (needs a listing_id to scope) ───────────────────────
    seo_active: Optional[int] = None
    seo_critical: Optional[int] = None
    if listing_id:
        sigs = (await db.execute(select(SeoSignal).where(
            SeoSignal.user_id == user_id, SeoSignal.listing_id == listing_id,
            SeoSignal.status.in_(_LIVE)))).scalars().all()
        seo_active = len(sigs)
        seo_critical = sum(1 for s in sigs if s.priority_level == "critical")

    # ── reviews cross-contour (review signals are user-wide, not listing-scoped) ─
    rsigs = (await db.execute(select(ReviewSignal).where(
        ReviewSignal.user_id == user_id,
        ReviewSignal.status.in_(_LIVE)))).scalars().all()
    review_active = len(rsigs)
    review_risk = sum(1 for s in rsigs if s.safety_category == "RISK")

    availability = {
        "revenue": True, "net_profit": True, "ad_spend": True, "units_sold": True,
        "margin": margin is not None, "drr": drr is not None,
        "active_seo_signals": listing_id is not None,
        "critical_seo_signals": listing_id is not None,
        "active_review_signals": True, "risk_review_signals": True,
        "stock_units": stock_units is not None,
        "days_to_oos": False,            # needs velocity over a known window — not stored, no forecast
        "category": category is not None,
        "margin_band": margin is not None,
    }

    return GrowthSnapshot(
        listing_id=listing_id, marketplace=marketplace, sku=sku,
        captured_at=now or datetime.utcnow(), source="internal",
        revenue=revenue, net_profit=net_profit, margin=margin, units_sold=units,
        ad_spend=ad_spend, drr=drr,
        active_seo_signals=seo_active, critical_seo_signals=seo_critical,
        active_review_signals=review_active, risk_review_signals=review_risk,
        stock_units=(int(stock_units) if stock_units is not None else None), days_to_oos=None,
        category=category, margin_band=_margin_band(margin),
        field_availability=availability,
    )
