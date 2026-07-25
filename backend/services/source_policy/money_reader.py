"""Gated API money/operation reader (PULT-LAUNCH-1.4.5H).

Reads realized money and operational counts from MarketplaceOperation — the API side only. It is the
counterpart of the CSV path (ImportedFinanceRow); the resolver picks exactly one, and the two are
NEVER unioned. Every read:
  * is source='api';
  * reads ONLY the authoritative provider_dataset for the metric (so a WB sale counted from finance
    is not also counted from the sales feed; 'legacy' rows are excluded automatically);
  * filters by operation_type, store and period;
  * uses Decimal for money; an order is never revenue; commission/logistics never revenue;
  * counts a return once.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.marketplace_operation import MarketplaceOperation
from services.source_policy import dataset_authority as da


def _period_filter(period: Optional[tuple[str, str]]):
    if period is None:
        return []
    p_from, p_to = period
    # occurred_at is a datetime; compare against day bounds (inclusive).
    return [MarketplaceOperation.occurred_at.isnot(None),
            func.substr(func.strftime("%Y-%m-%d", MarketplaceOperation.occurred_at), 1, 10) >= p_from,
            func.substr(func.strftime("%Y-%m-%d", MarketplaceOperation.occurred_at), 1, 10) <= p_to]


async def api_money(db: AsyncSession, *, store_id: str, marketplace: str, metric_type: str,
                    period: Optional[tuple[str, str]] = None) -> Optional[Decimal]:
    """Signed money total for a MONEY metric (revenue/fees/logistics/penalties/deductions) from the
    one authoritative finance dataset. None when the metric is not an API money metric here (e.g.
    Yandex money) or when no rows exist — never 0 for 'unknown'."""
    auth = da.authoritative(marketplace, metric_type)
    if auth is None or not auth.is_money:
        return None
    row = (await db.execute(
        select(func.sum(MarketplaceOperation.amount)).where(
            MarketplaceOperation.marketplace_store_id == store_id,
            MarketplaceOperation.source == "api",
            MarketplaceOperation.provider_dataset.in_(list(auth.datasets)),
            MarketplaceOperation.operation_type.in_(list(auth.operation_types)),
            MarketplaceOperation.amount.isnot(None),
            *_period_filter(period)))).scalar()
    return Decimal(row) if row is not None else None


async def api_count(db: AsyncSession, *, store_id: str, marketplace: str, metric_type: str,
                    period: Optional[tuple[str, str]] = None) -> Optional[int]:
    """Operational COUNT for a frequency metric (orders / returns-frequency) from its authoritative
    dataset. Sums quantity when present, else counts rows. None when the metric has no API count here.
    A return is counted once (one dataset, one operation_type)."""
    auth = da.authoritative(marketplace, metric_type)
    if auth is None or auth.is_money:
        return None
    conds = [
        MarketplaceOperation.marketplace_store_id == store_id,
        MarketplaceOperation.source == "api",
        MarketplaceOperation.provider_dataset.in_(list(auth.datasets)),
        MarketplaceOperation.operation_type.in_(list(auth.operation_types)),
        *_period_filter(period),
    ]
    qty = (await db.execute(
        select(func.coalesce(func.sum(MarketplaceOperation.quantity), 0)).where(*conds))).scalar()
    if qty:
        return int(qty)
    # No quantities recorded — fall back to a row count so an order/return is still counted once.
    n = (await db.execute(
        select(func.count()).select_from(MarketplaceOperation).where(*conds))).scalar()
    return int(n) if n else None
