"""Store-aware resolved finance totals (PULT-LAUNCH-1.4.5H2).

The one place a user-level money total is built the correct way: split rows by Store, resolve ONE
source per (store, metric, period), sum each store, THEN add the stores together. Never sum CSV and
API first; never pick the first store; never blend two Yandex campaigns; never apply one store's
policy to another. A legacy CSV row with no store (marketplace_store_id IS NULL) is its own CSV
bucket — API is never added to it and it never disappears.

When API_DATA_SYNC_ENABLED is off there are no API rows and the resolver would pick CSV for every
store anyway, so this takes a pure-CSV fast path: the total equals today's whole-user CSV sum exactly
(the store partition is exhaustive), and zero API/coverage queries run. That keeps existing numbers
byte-identical until an operator turns the flag on AND a seller opts a store into API.
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
from services.source_policy import derived, money_reader
from services.source_policy import resolver as rz

# ImportedFinanceRow.marketplace may hold a raw label ('wb'); the Store holds the canonical name.
_CANON = {"wb": "wildberries", "wildberries": "wildberries", "ozon": "ozon",
          "yandex": "yandex", "yandex_market": "yandex", "ym": "yandex"}

# The money metrics of a finance period and the ImportedFinanceRow column each sums for its CSV side.
_MONEY_CSV_COL = {
    "revenue": ImportedFinanceRow.revenue,
    "marketplace_fees": ImportedFinanceRow.commission,
    "logistics": ImportedFinanceRow.logistics,
}


@dataclass
class ResolvedTotals:
    revenue: float = 0.0
    commission: float = 0.0
    logistics: float = 0.0
    ad_spend: float = 0.0
    net_profit: Optional[float] = 0.0     # None when suppressed (an API store lacks cost of goods)
    orders: int = 0
    # meta
    revenue_source: Optional[str] = None
    completeness: str = "complete"
    missing_fields: list = field(default_factory=list)
    conflict: bool = False
    conflict_fields: list = field(default_factory=list)


def _canon(mp: Optional[str]) -> str:
    return _CANON.get((mp or "").lower(), (mp or "").lower())


async def _csv_by_store(db, user_id, date_from, date_to):
    """Per-store CSV sums in ONE grouped query. Includes the NULL-store legacy bucket."""
    rows = (await db.execute(
        select(
            ImportedFinanceRow.marketplace_store_id.label("store_id"),
            func.min(ImportedFinanceRow.marketplace).label("mp"),
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0).label("revenue"),
            func.coalesce(func.sum(ImportedFinanceRow.commission), 0.0).label("commission"),
            func.coalesce(func.sum(ImportedFinanceRow.logistics), 0.0).label("logistics"),
            func.coalesce(func.sum(ImportedFinanceRow.ad_spend), 0.0).label("ad_spend"),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0).label("net_profit"),
            func.coalesce(func.sum(ImportedFinanceRow.quantity), 0).label("orders"),
        ).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.source == "csv",
            ImportedFinanceRow.date >= date_from,
            ImportedFinanceRow.date <= date_to,
        ).group_by(ImportedFinanceRow.marketplace_store_id))).all()
    return rows


async def resolved_finance_period(db: AsyncSession, user_id: str, date_from: str,
                                  date_to: str) -> ResolvedTotals:
    """A finance period built store-by-store through the resolver, then summed."""
    csv_rows = await _csv_by_store(db, user_id, date_from, date_to)

    # Fast path: flag off ⇒ no API rows possible ⇒ every store resolves to CSV. Sum the CSV buckets;
    # this is exactly today's whole-user total (store partition is exhaustive).
    if not settings.api_data_sync_enabled:
        t = ResolvedTotals(revenue_source="csv")
        for r in csv_rows:
            t.revenue += float(r.revenue); t.commission += float(r.commission)
            t.logistics += float(r.logistics); t.ad_spend += float(r.ad_spend)
            t.net_profit = (t.net_profit or 0.0) + float(r.net_profit); t.orders += int(r.orders)
        return t

    # Flag on: resolve each store independently.
    period = (date_from, date_to)
    t = ResolvedTotals(net_profit=0.0)
    csv_sources_only = True
    for r in csv_rows:
        store_id = r.store_id
        if store_id is None:
            # Legacy no-store CSV bucket — always CSV, never joined to API.
            t.revenue += float(r.revenue); t.commission += float(r.commission)
            t.logistics += float(r.logistics); t.ad_spend += float(r.ad_spend)
            if t.net_profit is not None:
                t.net_profit += float(r.net_profit)
            t.orders += int(r.orders)
            continue

        store = await db.get(MarketplaceStore, store_id)
        marketplace = _canon(store.marketplace if store else r.mp)
        csv_vals = {"revenue": float(r.revenue), "marketplace_fees": float(r.commission),
                    "logistics": float(r.logistics)}

        store_is_api = False
        for metric, csv_val in csv_vals.items():
            api_val = await money_reader.api_money(
                db, store_id=store_id, marketplace=marketplace, metric_type=metric, period=period)
            res = await rz.resolve_source(
                db, store_id=store_id, marketplace=marketplace, metric_type=metric, period=period,
                api_value=(Decimal(str(api_val)) if api_val is not None else None),
                csv_value=Decimal(str(csv_val)))
            chosen = float(res.value) if res.value is not None else 0.0
            if metric == "revenue":
                t.revenue += chosen
                t.revenue_source = res.source
                if res.conflict:
                    t.conflict = True
                    if "revenue" not in t.conflict_fields:
                        t.conflict_fields.append("revenue")
            elif metric == "marketplace_fees":
                t.commission += chosen
            else:
                t.logistics += chosen
            if res.source == "api":
                store_is_api = True

        # ad_spend and orders.
        t.ad_spend += float(r.ad_spend)   # external ad spend is CSV/manual only
        orders_api = await money_reader.api_count(
            db, store_id=store_id, marketplace=marketplace, metric_type="orders", period=period)
        ores = await rz.resolve_source(
            db, store_id=store_id, marketplace=marketplace, metric_type="orders", period=period,
            api_value=orders_api, csv_value=int(r.orders))
        t.orders += int(ores.value) if ores.value is not None else 0

        # Profit follows the revenue source. A CSV store contributes its imported net_profit; an API
        # store cannot — the API has no cost of goods, and CSV net_profit must NOT be mixed with API
        # revenue — so the exact profit is suppressed and the period is incomplete.
        if store_is_api:
            csv_sources_only = False
        else:
            if t.net_profit is not None:
                t.net_profit += float(r.net_profit)

    if not csv_sources_only:
        p = derived.profit(revenue=Decimal(str(t.revenue)),
                           marketplace_fees=Decimal(str(t.commission)),
                           logistics=Decimal(str(t.logistics)), cogs=None, external_ad_spend=None)
        t.net_profit = None
        t.completeness = "incomplete"
        t.missing_fields = p.missing_fields
    return t
