"""
Imported-card CardSnapshot source (Phase C1) — build the canonical CardSnapshot from UPLOAD
Evidence (imported_card_content_rows), no external API.

Per the Evidence Source Doctrine this reads UPLOAD Evidence: the seller's uploaded card-content
report (C0 ingestion). It supplies the CONTENT fields the internal source cannot
(description / attributes / category_path / media) and marks them available — so content-presence
SEO rules can evaluate for real.

HONEST GAP (still open): category schema, required attributes, and marketplace constraints have no
downloadable-report equivalent — they are API-Snapshot Evidence, not uploaded. So this source keeps
`constraints = None` and `field_availability["category_schema"] = False` (and expected_category_path
/ variants False): constraint-dependent SEO rules stay `not_evaluated`. Nothing is invented.

Latest-by-date: the newest ImportedCardContentRow (by created_at) for a (user, marketplace, sku)
wins — successive uploads are immutable dated snapshots; diagnosis reads the latest.

Reader-only: builds an existing CardSnapshot; does NOT enable a producer, touch the registry,
scheduler, Decision Feed, or call a marketplace API.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from models.imported_card_content import ImportedCardContentRow
from models.product_listing import ProductListing

from .card_snapshot import CardSnapshot, CategorySchema, CardAttribute, CardMedia
from .adapter import SnapshotResult, SnapshotUnavailable


def _parse_attributes(characteristics_json: Optional[str]) -> tuple[CardAttribute, ...]:
    """Map the uploaded characteristics JSON into CardAttribute tuple. A dict → one attribute per
    key; is_filled reflects a non-empty value. Unparseable / empty → empty tuple (unavailable)."""
    if not characteristics_json:
        return ()
    try:
        data = json.loads(characteristics_json)
    except (ValueError, TypeError):
        return ()
    if not isinstance(data, dict) or not data:
        return ()
    out = []
    for key, value in data.items():
        val = None if value is None else str(value)
        out.append(CardAttribute(key=str(key), value=val, is_filled=bool(val and val.strip())))
    return tuple(out)


async def _latest_card_row(
    db, user_id: str, marketplace: str, sku: str
) -> Optional[ImportedCardContentRow]:
    """Newest uploaded card-content row for one (user, marketplace, sku), by created_at."""
    return (await db.execute(
        select(ImportedCardContentRow).where(
            ImportedCardContentRow.user_id == user_id,
            ImportedCardContentRow.marketplace == marketplace,
            ImportedCardContentRow.sku == sku)
        .order_by(ImportedCardContentRow.created_at.desc()).limit(1))).scalars().first()


async def _resolve_listing_id(db, user_id: str, marketplace: str, sku: str) -> Optional[str]:
    """Best-effort ProductListing.id for (user, marketplace, external_id=sku). None if unresolved."""
    return (await db.execute(
        select(ProductListing.id).where(
            ProductListing.user_id == user_id,
            ProductListing.marketplace == marketplace,
            ProductListing.external_id == str(sku)).limit(1))).scalar()


async def build_snapshot_from_import(
    db, *, user_id: str, marketplace: str, sku: str,
) -> SnapshotResult:
    """CardSnapshot from the latest uploaded card-content row, or honest SnapshotUnavailable.

    Content fields present in the upload are marked available; category schema / constraints stay
    unavailable (the API-Snapshot Evidence gap) so constraint-dependent rules remain not_evaluated.
    """
    if db is None:
        return SnapshotUnavailable(marketplace or "unknown", "no_db_context")
    row = await _latest_card_row(db, user_id, marketplace, sku)
    if row is None:
        return SnapshotUnavailable(marketplace or "unknown", "card_not_found")

    listing_id = await _resolve_listing_id(db, user_id, marketplace, sku) or str(sku)

    title = (row.title or "").strip() or None
    description = (row.description or "").strip() or None
    brand = (row.brand or "").strip() or None
    category = (row.category or "").strip() or None
    category_path = tuple(p.strip() for p in category.split(">")) if category else ()
    attributes = _parse_attributes(row.characteristics_json)
    has_media = row.image_count is not None

    availability = {
        # content fields — available iff the upload actually provided them
        "title": title is not None,
        "description": description is not None,
        "brand": brand is not None,
        "category_path": bool(category_path),
        "attributes": bool(attributes),
        "media": has_media,
        # API-Snapshot Evidence gap — never sourced from an upload, always unavailable here
        "expected_category_path": False,
        "category_schema": False,
        "variants": False,
        "constraints": False,
    }

    return CardSnapshot(
        listing_id=listing_id, marketplace=row.marketplace, sku=row.sku,
        captured_at=row.created_at or datetime.utcnow(), source="import",
        title=title, description=description, brand=brand,
        category_path=category_path, expected_category_path=None,
        category_schema=CategorySchema(),
        attributes=attributes, variants=(),
        media=CardMedia(image_count=row.image_count or 0),
        constraints=None,                        # API-Snapshot gap — never invented
        field_availability=availability,
    )
