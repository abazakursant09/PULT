"""
Finance-backed compute reader for net_profit (Action Space A0.1).

net_profit is NOT a marketplace API metric — it is computed from already-imported
finance rows (ImportedFinanceRow). This reader sums net_profit over a
(user, marketplace, sku, window), mirroring finance_aggregator.effective_profit
EXACTLY (imported net_profit; fallback revenue − commission − logistics −
ad_spend). It introduces no new formula and calls no WB/Ozon/Yandex API.

Honesty: returns MetricUnavailable (never 0, never an estimate) when db/user_id
is missing or there are no finance rows in the window. A real 0 (rows exist,
profit nets to zero) is an honest MetricSample.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Union

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from services.marketplace.metric_reader import MetricSample, MetricUnavailable

_METRIC = "net_profit"

# Canonical marketplace → finance-row aliases (imports may store raw labels).
_MP_ALIASES = {
    "wb": ("wb", "wildberries"),
    "ozon": ("ozon",),
    "yandex": ("yandex", "yandex_market", "ym"),
}


async def read_net_profit(
    *,
    db: Optional[AsyncSession],
    user_id: Optional[str],
    marketplace: Optional[str],
    entity_id,
    window_days: int = 7,
    now: Optional[datetime] = None,
) -> Union[MetricSample, MetricUnavailable]:
    now = now or datetime.utcnow()

    if db is None:
        return MetricUnavailable(_METRIC, "no_db", "compute reader requires a db session")
    if not user_id:
        return MetricUnavailable(_METRIC, "no_scope", "compute reader requires user_id")
    if not entity_id:
        return MetricUnavailable(_METRIC, "no_entity", "compute reader requires entity_id (sku)")

    date_from = (now - timedelta(days=window_days)).date().isoformat()
    date_to = now.date().isoformat()
    aliases = _MP_ALIASES.get((marketplace or "").lower(), ((marketplace or "").lower(),))

    res = await db.execute(
        select(
            func.count().label("n"),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0).label("profit"),
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0).label("revenue"),
            func.coalesce(func.sum(ImportedFinanceRow.commission), 0.0).label("commission"),
            func.coalesce(func.sum(ImportedFinanceRow.logistics), 0.0).label("logistics"),
            func.coalesce(func.sum(ImportedFinanceRow.ad_spend), 0.0).label("ad_spend"),
        ).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace.in_(aliases),
            ImportedFinanceRow.sku == str(entity_id),
            ImportedFinanceRow.date.isnot(None),
            ImportedFinanceRow.date >= date_from,
            ImportedFinanceRow.date <= date_to,
        )
    )
    row = res.one()

    if not row.n:  # no finance rows in the window → honest absence, never a fabricated 0
        return MetricUnavailable(_METRIC, "no_finance_rows",
                                 f"no finance rows for {marketplace}/{entity_id} in {window_days}d window")

    # Mirror finance_aggregator.effective_profit: imported net_profit, else
    # revenue − commission − logistics − ad_spend. No new formula.
    profit = float(row.profit)
    effective = profit if profit else float(row.revenue) - float(row.commission) \
        - float(row.logistics) - float(row.ad_spend)

    return MetricSample(
        value=round(effective, 2),
        unit="rub",
        observed_at=now,
        source="compute",
        quality=f"rows={int(row.n)}",
    )
