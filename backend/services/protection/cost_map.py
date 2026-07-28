"""
PULT-LAUNCH-2.5B — mapping + attribution helpers for the runtime evaluation service.

ONLY mapping and source/attribution resolution — no business orchestration (that is evaluation.py).
Maps a source-policy money metric onto a canonical 2.4 cost_key, chooses ONE source per metric via
the existing resolver (never sums API + CSV, preserves conflict), and proves product+store
attribution before a value may be used. A fee/logistics amount is a COST magnitude: the signed
reader value is taken as |amount| (marketplace fees are always a reduction).

Store isolation: a CSV money sum is scoped to the ONE marketplace_store_id — never merged across two
stores of the same seller. user_id is NOT a sufficient key (a seller may own several stores, and the
same SKU may exist in more than one), so it is not used here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from services.source_policy.resolver import resolve_source
from services.source_policy.product_money_reader import (
    api_product_money, attribution_completeness,
)

# money metric_type → (canonical cost_key, ImportedFinanceRow CSV column or None)
MONEY_METRICS = {
    "marketplace_fees": ("commission", "commission"),
    "logistics":        ("logistics", "logistics"),
    "penalties":        ("penalties", None),
    "deductions":       ("deductions", None),
}
_FIN_ALIASES = {"wildberries": ("wildberries", "wb"), "wb": ("wildberries", "wb"),
                "ozon": ("ozon",), "yandex": ("yandex", "yandex_market", "ym")}


@dataclass
class MetricMoney:
    """One resolved money metric for a product: chosen source, cost magnitude, attribution, conflict."""
    metric_type: str
    cost_key: str
    source: Optional[str]                 # api | csv | None(no data)
    amount: Optional[Decimal]             # cost magnitude (>=0) per period, or None
    conflict: bool = False
    attributed: bool = True
    reason: Optional[str] = None


async def _csv_sum(db: AsyncSession, *, store_id: str, marketplace: str, product_id: str,
                   column: str, period) -> Optional[Decimal]:
    """Sum ONE CSV money column for a product, scoped to a single store (never cross-store).
    Filters: marketplace_store_id + product_id + source='csv' + marketplace + period."""
    aliases = _FIN_ALIASES.get((marketplace or "").lower(), ((marketplace or "").lower(),))
    conds = [ImportedFinanceRow.marketplace_store_id == store_id,
             ImportedFinanceRow.marketplace.in_(aliases),
             ImportedFinanceRow.product_id == product_id,
             ImportedFinanceRow.source == "csv"]
    if period:
        conds += [ImportedFinanceRow.date >= period[0], ImportedFinanceRow.date <= period[1]]
    total = (await db.execute(
        select(func.coalesce(func.sum(getattr(ImportedFinanceRow, column)), None)).where(*conds))).scalar()
    return None if total is None else Decimal(str(total))


async def resolve_metric_money(db: AsyncSession, *, store_id: str, product_id: str,
                               marketplace: str, metric_type: str, period,
                               now: Optional[datetime] = None) -> MetricMoney:
    """Resolve ONE money metric (period magnitude) for a product through the source policy.
    The CSV candidate is store-scoped; API + CSV are never summed; conflict is preserved."""
    cost_key, csv_col = MONEY_METRICS[metric_type]
    api_val = await api_product_money(db, store_id=store_id, product_id=product_id,
                                      marketplace=marketplace, metric_type=metric_type,
                                      period=period)
    csv_val = (await _csv_sum(db, store_id=store_id, marketplace=marketplace, product_id=product_id,
                              column=csv_col, period=period)) if csv_col else None
    res = await resolve_source(db, store_id=store_id, marketplace=marketplace, metric_type=metric_type,
                               period=period, api_value=api_val, csv_value=csv_val, now=now)
    if res.conflict:
        return MetricMoney(metric_type, cost_key, None, None, conflict=True, reason="source_conflict")
    if res.source is None:
        return MetricMoney(metric_type, cost_key, None, None, reason=res.reason or "no_data")
    # a value chosen from the API must be provably attributed to this product+store
    if res.source == "api":
        att = await attribution_completeness(db, store_id=store_id, marketplace=marketplace,
                                             metric_type=metric_type, period=period)
        if not att.complete:
            return MetricMoney(metric_type, cost_key, None, None, attributed=False, reason=att.reason)
    amount = abs(Decimal(str(res.value))) if res.value is not None else None   # cost magnitude
    return MetricMoney(metric_type, cost_key, res.source, amount, reason=res.reason)
