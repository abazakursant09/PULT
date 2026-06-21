"""
Internal CardSnapshot source (A8) — build a snapshot from EXISTING PULT data, no
external API.

PULT stores only thin card data (ProductListing + PhysicalProduct): listing id,
marketplace, external sku, title, brand. There is no internal description /
attributes / category schema / media / variants, and no marketplace/category
constraints. So this builder supplies what exists and HONESTLY marks everything
else unavailable (field_availability=False, constraints=None) — the SEO rules
then return not_evaluated, never a false "ok". Nothing is invented.

Marketplace-agnostic: reads the generic listing/product models, never a
marketplace-specific client. Any adapter can reuse it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from models.product_listing import ProductListing
from models.physical_product import PhysicalProduct

from .card_snapshot import CardSnapshot, CategorySchema, CardMedia
from .adapter import SnapshotResult, SnapshotUnavailable

# Fields PULT cannot supply internally today → always unavailable (honest).
_UNAVAILABLE = {
    "description": False, "attributes": False, "category_schema": False,
    "category_path": False, "expected_category_path": False, "variants": False,
    "media": False, "constraints": False,
}


async def build_snapshot_from_internal(
    db, *, listing_id: str, marketplace: Optional[str] = None,
) -> SnapshotResult:
    """CardSnapshot from internal listing/product data, or honest SnapshotUnavailable."""
    if db is None:
        return SnapshotUnavailable(marketplace or "unknown", "no_db_context")
    listing = await db.get(ProductListing, listing_id)
    if listing is None:
        return SnapshotUnavailable(marketplace or "unknown", "listing_not_found")

    physical = (await db.get(PhysicalProduct, listing.physical_product_id)
                if listing.physical_product_id else None)
    title = listing.title or (physical.title if physical else None)
    brand = physical.brand if physical else None

    availability = dict(_UNAVAILABLE)
    availability["title"] = bool(title)
    availability["brand"] = bool(brand)

    return CardSnapshot(
        listing_id=listing.id, marketplace=listing.marketplace, sku=listing.external_id,
        captured_at=datetime.utcnow(), source="internal",
        title=title, description=None, brand=brand,
        category_path=(), expected_category_path=None, category_schema=CategorySchema(),
        attributes=(), variants=(), media=CardMedia(image_count=0),
        constraints=None,                       # no real limits internally — never invented
        field_availability=availability,
    )
