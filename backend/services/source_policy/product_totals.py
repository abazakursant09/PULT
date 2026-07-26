"""Per-product resolved money (PULT-LAUNCH-1.4.5H3).

A product ranking (top / loss / summary) built the correct way: resolve each product's money per
Store through the policy, then aggregate the product across stores. API is used for a product only
when the store's policy selects it AND the product attribution is complete (every money operation of
that metric carried a product_id); otherwise the product is incomplete and its exact figure is not
presented as trustworthy. Unassigned money stays in the store total and is never split across
products.

Flag off ⇒ pure CSV fast path: group ImportedFinanceRow(source='csv') by product exactly as before,
so existing per-product numbers are byte-identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.imported_finance import ImportedFinanceRow
from models.marketplace_store import MarketplaceStore
from models.product import Product
from services.source_policy import product_money_reader as pmr
from services.source_policy import resolver as rz
from services.source_policy.store_totals import _canon


@dataclass
class ProductMoney:
    product_id: str
    name: Optional[str] = None
    revenue: float = 0.0
    net_profit: Optional[float] = 0.0     # None when suppressed (API store, no cost of goods)
    orders: int = 0
    source: str = "csv"
    completeness: str = "complete"        # complete | incomplete
    missing_fields: list = field(default_factory=list)
    conflict: bool = False


async def _csv_by_product(db, user_id, date_from, date_to):
    """CSV per (store, product) sums; product_id NOT NULL only (unassigned money is not a product).
    A None date bound means "all time" for that side."""
    conds = [ImportedFinanceRow.user_id == user_id, ImportedFinanceRow.source == "csv",
             ImportedFinanceRow.product_id.isnot(None)]
    if date_from is not None:
        conds.append(ImportedFinanceRow.date >= date_from)
    if date_to is not None:
        conds.append(ImportedFinanceRow.date <= date_to)
    return (await db.execute(
        select(
            ImportedFinanceRow.marketplace_store_id.label("store_id"),
            ImportedFinanceRow.product_id.label("product_id"),
            func.min(ImportedFinanceRow.marketplace).label("mp"),
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0).label("revenue"),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0).label("net"),
            func.coalesce(func.sum(ImportedFinanceRow.quantity), 0).label("orders"),
        ).where(*conds)
        .group_by(ImportedFinanceRow.marketplace_store_id, ImportedFinanceRow.product_id))).all()


async def resolved_products(db: AsyncSession, user_id: str, date_from,
                            date_to) -> dict[str, ProductMoney]:
    """product_id -> ProductMoney, resolved per store then aggregated across stores. A None date
    bound means all-time (period-less coverage: coverage_complete without a range check)."""
    csv_rows = await _csv_by_product(db, user_id, date_from, date_to)
    out: dict[str, ProductMoney] = {}

    # Fast path: flag off ⇒ everything CSV. Aggregate per product directly.
    if not settings.api_data_sync_enabled:
        for r in csv_rows:
            pm = out.setdefault(r.product_id, ProductMoney(product_id=r.product_id, net_profit=0.0))
            pm.revenue += float(r.revenue)
            pm.net_profit = (pm.net_profit or 0.0) + float(r.net)
            pm.orders += int(r.orders)
        await _fill_names(db, out)
        return out

    period = (date_from, date_to) if (date_from is not None and date_to is not None) else None
    for r in csv_rows:
        pid, store_id = r.product_id, r.store_id
        pm = out.setdefault(pid, ProductMoney(product_id=pid, net_profit=0.0))
        if store_id is None:
            # A product's CSV rows with no store stay CSV (no API to attribute to).
            pm.revenue += float(r.revenue)
            if pm.net_profit is not None:
                pm.net_profit += float(r.net)
            pm.orders += int(r.orders)
            continue

        store = await db.get(MarketplaceStore, store_id)
        marketplace = _canon(store.marketplace if store else r.mp)
        api_rev = await pmr.api_product_money(db, store_id=store_id, product_id=pid,
                                              marketplace=marketplace, metric_type="revenue", period=period)
        res = await rz.resolve_source(
            db, store_id=store_id, marketplace=marketplace, metric_type="revenue", period=period,
            api_value=(Decimal(str(api_rev)) if api_rev is not None else None),
            csv_value=Decimal(str(r.revenue)))
        if res.conflict:
            pm.conflict = True
        if res.source == "api":
            # Attribution must be complete to trust a per-product API figure.
            attr = await pmr.attribution_completeness(
                db, store_id=store_id, marketplace=marketplace, metric_type="revenue", period=period)
            pm.revenue += float(res.value) if res.value is not None else 0.0
            pm.source = "api"
            # API store: no cost of goods → the exact profit cannot be formed, so the product's money
            # is incomplete (revenue is real, profit is suppressed — never 0).
            pm.net_profit = None
            pm.completeness = "incomplete"
            if "cogs" not in pm.missing_fields:
                pm.missing_fields.append("cogs")
            if not attr.complete and "product_attribution" not in pm.missing_fields:
                pm.missing_fields.append("product_attribution")
            orders_api = await pmr.api_product_count(
                db, store_id=store_id, product_id=pid, marketplace=marketplace,
                metric_type="orders", period=period)
            pm.orders += int(orders_api) if orders_api is not None else 0
        else:
            pm.revenue += float(r.revenue)
            if pm.net_profit is not None:
                pm.net_profit += float(r.net)
            pm.orders += int(r.orders)

    await _fill_names(db, out)
    return out


async def _fill_names(db, out: dict[str, ProductMoney]) -> None:
    if not out:
        return
    rows = (await db.execute(
        select(Product.id, Product.name).where(Product.id.in_(list(out))))).all()
    for pid, name in rows:
        if pid in out:
            out[pid].name = name
