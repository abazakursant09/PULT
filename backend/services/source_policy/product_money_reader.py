"""Product-level API money + attribution completeness (PULT-LAUNCH-1.4.5H3).

A per-product money figure may use the API, but ONLY for operations unambiguously tied to a Product
(product_id NOT NULL) and only when coverage is complete. An operation with product_id=NULL belongs
to the store total but to NO product — it is never spread across products proportionally or evenly.
So a product ranking (top/loss) is honest only when every money operation of that metric carried a
product_id; one significant unassigned operation makes the product breakdown incomplete, even while
the store total stays correct.

Same discipline as the store money reader: source='api', the ONE authoritative provider_dataset,
the right operation_type, Decimal, legacy excluded, orders never revenue, a return counted once, and
never unioned with CSV — the resolver picks one path.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.api_sync_state import ApiSyncState
from models.marketplace_operation import MarketplaceOperation
from services.source_policy import dataset_authority as da
from services.source_policy.money_reader import _period_filter


async def api_product_money(db: AsyncSession, *, store_id: str, product_id: str, marketplace: str,
                            metric_type: str, period: Optional[tuple[str, str]] = None) -> Optional[Decimal]:
    """Signed money for ONE product from the authoritative finance dataset. product_id is REQUIRED —
    an operation with no product is never attributed here. None when not an API money metric or none."""
    auth = da.authoritative(marketplace, metric_type)
    if auth is None or not auth.is_money:
        return None
    row = (await db.execute(
        select(func.sum(MarketplaceOperation.amount)).where(
            MarketplaceOperation.marketplace_store_id == store_id,
            MarketplaceOperation.product_id == product_id,
            MarketplaceOperation.source == "api",
            MarketplaceOperation.provider_dataset.in_(list(auth.datasets)),
            MarketplaceOperation.operation_type.in_(list(auth.operation_types)),
            MarketplaceOperation.amount.isnot(None),
            *_period_filter(period)))).scalar()
    return Decimal(row) if row is not None else None


async def api_product_count(db: AsyncSession, *, store_id: str, product_id: str, marketplace: str,
                            metric_type: str, period: Optional[tuple[str, str]] = None) -> Optional[int]:
    """Operational count (orders / returns-frequency) for ONE product. product_id REQUIRED."""
    auth = da.authoritative(marketplace, metric_type)
    if auth is None or auth.is_money:
        return None
    conds = [
        MarketplaceOperation.marketplace_store_id == store_id,
        MarketplaceOperation.product_id == product_id,
        MarketplaceOperation.source == "api",
        MarketplaceOperation.provider_dataset.in_(list(auth.datasets)),
        MarketplaceOperation.operation_type.in_(list(auth.operation_types)),
        *_period_filter(period),
    ]
    qty = (await db.execute(
        select(func.coalesce(func.sum(MarketplaceOperation.quantity), 0)).where(*conds))).scalar()
    if qty:
        return int(qty)
    n = (await db.execute(select(func.count()).select_from(MarketplaceOperation).where(*conds))).scalar()
    return int(n) if n else None


@dataclass
class Attribution:
    total: int
    attributed: int          # operations with a product_id
    unassigned: int          # operations with product_id IS NULL
    complete: bool           # safe to present an exact product breakdown
    reason: str


async def attribution_completeness(db: AsyncSession, *, store_id: str, marketplace: str,
                                   metric_type: str,
                                   period: Optional[tuple[str, str]] = None) -> Attribution:
    """Whether a per-product breakdown of this metric is COMPLETE for the store+period.

    Complete requires: the store's ApiSyncState for the metric's dataset is synced with
    coverage_complete and zero skipped rows, AND every authoritative money operation carries a
    product_id. A single significant unassigned operation → incomplete (the store total is still
    right; only the product split is untrustworthy)."""
    auth = da.authoritative(marketplace, metric_type)
    if auth is None:
        return Attribution(0, 0, 0, False, "api_unsupported_metric")

    base = [
        MarketplaceOperation.marketplace_store_id == store_id,
        MarketplaceOperation.source == "api",
        MarketplaceOperation.provider_dataset.in_(list(auth.datasets)),
        MarketplaceOperation.operation_type.in_(list(auth.operation_types)),
        *_period_filter(period),
    ]
    total = int((await db.execute(
        select(func.count()).select_from(MarketplaceOperation).where(*base))).scalar() or 0)
    unassigned = int((await db.execute(
        select(func.count()).select_from(MarketplaceOperation).where(
            *base, MarketplaceOperation.product_id.is_(None)))).scalar() or 0)
    attributed = total - unassigned

    # Coverage of the metric's dataset(s) at store level.
    coverage_ok = True
    for dt in auth.datasets:
        st = (await db.execute(select(ApiSyncState).where(
            ApiSyncState.marketplace_store_id == store_id,
            ApiSyncState.data_type == dt))).scalars().first()
        if st is None or st.status != "synced" or not st.coverage_complete or (st.skipped_rows_count or 0) > 0:
            coverage_ok = False
            break

    if not coverage_ok:
        return Attribution(total, attributed, unassigned, False, "coverage_incomplete")
    if unassigned > 0:
        return Attribution(total, attributed, unassigned, False, "unassigned_operations")
    return Attribution(total, attributed, unassigned, True, "complete")
