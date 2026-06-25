"""
Pricing/Margin Snapshot (A3-pre) — observed-only facts for one (user, marketplace, sku).

Sources (observed, never forecast / competitor / AI):
  finance   ImportedFinanceRow  → revenue / net_profit → margin (%)
  price     Product.price       → current selling price (optional)
  floor     PricingRule.min_price → seller-configured price floor (optional, rule-only)

Marketplace-isolated: finance is read per the canonical marketplace's aliases; WB /
Ozon / Yandex / Megamarket are never blended. If there is NO finance row for the
(user, marketplace, sku), build returns None — no snapshot, hence no fabricated signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from models.product import Product
from models.pricing_rule import PricingRule

# canonical marketplace → finance-row aliases (imports may store raw labels).
_ALIASES = {
    "wb": ("wb", "wildberries"), "wildberries": ("wb", "wildberries"),
    "ozon": ("ozon",),
    "yandex": ("yandex", "yandex_market", "ym"), "ym": ("yandex", "yandex_market", "ym"),
}


def _aliases(mp: Optional[str]) -> tuple[str, ...]:
    key = (mp or "").strip().lower()
    return _ALIASES.get(key, (key,))


@dataclass(frozen=True)
class PricingSnapshot:
    listing_id: Optional[str]
    marketplace: Optional[str]
    sku: Optional[str]
    captured_at: datetime
    source: str
    revenue: float
    net_profit: float
    margin: Optional[float]            # net_profit / revenue * 100, None when revenue <= 0
    current_price: Optional[float]     # observed selling price (Product.price)
    floor_price: Optional[float]       # seller PricingRule.min_price (rule/floor only)
    field_availability: Mapping[str, bool] = field(default_factory=dict)


async def build_pricing_snapshot(
    db: AsyncSession, *, user_id: str, marketplace: str, sku: Optional[str],
    listing_id: Optional[str] = None, now: Optional[datetime] = None,
) -> Optional[PricingSnapshot]:
    """Observed pricing/margin snapshot, or None when no finance exists for the
    (user, marketplace, sku) — never a fabricated snapshot."""
    aliases = _aliases(marketplace)
    row = (await db.execute(
        select(
            func.count().label("n"),
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0),
        ).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace.in_(aliases),
            ImportedFinanceRow.sku == str(sku),
        )
    )).one()
    n, revenue, net_profit = int(row[0]), float(row[1]), float(row[2])
    if n == 0:
        return None   # no observed finance → no snapshot → no signal

    margin = (net_profit / revenue * 100.0) if revenue > 0 else None

    # current price (observed) — Product.price for this user/sku, if stored
    current_price = (await db.execute(
        select(Product.price).where(
            Product.user_id == user_id, Product.sku == str(sku),
            Product.price.isnot(None)).limit(1))).scalar()

    # floor (seller rule only — never a recommendation): PricingRule.min_price via Product
    floor_price = (await db.execute(
        select(PricingRule.min_price).join(Product, Product.id == PricingRule.product_id).where(
            Product.user_id == user_id, Product.sku == str(sku)).limit(1))).scalar()

    availability = {
        "revenue": True, "net_profit": True,
        "margin": margin is not None,
        "current_price": current_price is not None,
        "floor_price": floor_price is not None,
    }
    return PricingSnapshot(
        listing_id=listing_id, marketplace=marketplace, sku=sku,
        captured_at=now or datetime.utcnow(), source="internal",
        revenue=revenue, net_profit=net_profit, margin=margin,
        current_price=(float(current_price) if current_price is not None else None),
        floor_price=(float(floor_price) if floor_price is not None else None),
        field_availability=availability,
    )
