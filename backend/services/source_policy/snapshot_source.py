"""Resolve the snapshot source for a producer that only knows (user, marketplace, sku)
(PULT-LAUNCH-1.4.5H2).

The diagnosis producers build a price/stock/rating series per (marketplace, sku). Policy lives on the
Store, so this maps the pair to its Store and asks the resolver which source to read. If the pair
resolves to exactly ONE store, that store's preference + coverage decide the source; if it is
ambiguous (several stores — e.g. two Yandex campaigns) or storeless, it falls back to CSV rather than
picking a store arbitrarily. The returned store_id is threaded into the reader so the series stays a
single source AND a single store — never an interleaved or cross-store trend.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.imported_product import ImportedProductRow
from services.source_policy import resolver as rz
from services.source_policy.store_totals import _canon

# marketplace label variants that mean the same canonical marketplace, for matching finance/product
# rows that may store 'wb' vs 'wildberries'.
_ALIASES = {
    "wildberries": ("wb", "wildberries"),
    "ozon": ("ozon",),
    "yandex": ("yandex", "yandex_market", "ym"),
}


async def resolved_snapshot(db: AsyncSession, user_id: str, marketplace: str, sku: str,
                            metric_type: str) -> tuple[str, Optional[str]]:
    """(source, store_id) for a (user, marketplace, sku) snapshot series. Defaults to ('csv', None)
    with the flag off or when the store is ambiguous — never picks a store arbitrarily."""
    if not settings.api_data_sync_enabled:
        return "csv", None
    canon = _canon(marketplace)
    aliases = _ALIASES.get(canon, (canon,))
    store_ids = [s for (s,) in (await db.execute(
        select(ImportedProductRow.marketplace_store_id).where(
            ImportedProductRow.user_id == user_id,
            ImportedProductRow.marketplace.in_(aliases),
            ImportedProductRow.sku == sku,
            ImportedProductRow.marketplace_store_id.isnot(None))
        .distinct())).all()]
    if len(store_ids) != 1:
        return "csv", None      # ambiguous or storeless — do not pick a store, stay CSV
    store_id = store_ids[0]
    preference = await rz.effective_preference(db, store_id, metric_type)
    if preference == "csv":
        return "csv", store_id
    if preference == "api":
        # An explicit API choice reads the API series — the seller's decision, never a hidden switch
        # back to CSV. (Freshness is surfaced elsewhere, not by silently changing the source.)
        return "api", store_id
    # auto: API only when the snapshot is synced and fresh, else CSV.
    eligible, _ = await rz.api_coverage(db, store_id, canon, metric_type, period=None)
    return ("api" if eligible else "csv"), store_id
