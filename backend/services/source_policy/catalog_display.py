"""Catalog / card-content display resolution (PULT-LAUNCH-1.4.5H).

A Product is ONE identity (marketSku/nmID/product_id), never duplicated by source. But a display
field (title/description/brand/category) can differ between the CSV a seller uploaded and what the
API returned, and the last writer must NOT silently win. This resolves one display field from the
source-specific ImportedCardContentRow rows already stored (no new provenance table): the policy
picks the source, CSV fills what the API lacks, a genuine difference is a surfaced conflict, and
Product.name is only a last-resort fallback when no source row exists — never an identity.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_card_content import ImportedCardContentRow
from models.product import Product
from services.source_policy.resolver import Resolution, decide

_FIELDS = ("title", "description", "brand", "category")


async def _latest_value(db: AsyncSession, product_id: str, source: str, field: str) -> Optional[str]:
    col = getattr(ImportedCardContentRow, field)
    return (await db.execute(
        select(col).where(
            ImportedCardContentRow.product_id == product_id,
            ImportedCardContentRow.source == source,
            col.isnot(None))
        .order_by(ImportedCardContentRow.fetched_at.desc()).limit(1))).scalar()


async def resolve_card_field(db: AsyncSession, *, product: Product, field: str, preference: str,
                             api_eligible: bool) -> Resolution:
    """Resolve one card display field for a Product. Falls back to Product.name (title only) when no
    source row carries the field — a safe label, never used as identity."""
    if field not in _FIELDS:
        raise ValueError(f"unknown card field: {field}")
    csv_val = await _latest_value(db, product.id, "csv", field)
    api_val = await _latest_value(db, product.id, "api", field)
    res = decide(marketplace=product.marketplace, metric_type="card_content",
                 preference=preference, api_eligible=api_eligible,
                 api_value=api_val, csv_value=csv_val)
    if res.source is None and field == "title":
        # No source row for the title — show the Product's name as a plain label (not identity).
        res.value = product.name
        res.completeness = "complete"
        res.reason = "product_name_fallback"
    return res
