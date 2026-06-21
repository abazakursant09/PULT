"""
Manual CardSnapshot source (A9) — build a snapshot from an EXPLICIT user/internal
payload passed to POST /api/seo/audit. This is NOT fake data: it is input the
caller asserts about a real card.

Honesty rules (same as every source):
- a field present in the payload → field_availability[field] = True;
- a field omitted (None) → False (rules that need it return not_evaluated);
- constraints omitted → constraints = None → constraint-dependent rules
  not_evaluated. NO default/marketplace limits are ever injected;
- category_schema / attributes / media are never invented — absent = unavailable.
An explicit field_availability map in the payload overrides the derived one.

Pure data mapping. No marketplace-specific code.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from .card_snapshot import (
    CardSnapshot, SeoConstraints, CategorySchema, CardAttribute, CardMedia,
)


class ManualCategorySchema(BaseModel):
    required_attributes: List[str] = []
    filterable_attributes: List[str] = []
    variant_attributes: List[str] = []


class ManualAttribute(BaseModel):
    key: str
    value: Optional[str] = None
    is_filled: bool
    is_valid_format: bool = True


class ManualMedia(BaseModel):
    image_count: int
    video_present: bool = False


class ManualConstraints(BaseModel):
    # All six required — partial constraints are rejected (no defaults invented).
    title_min_len: int
    title_max_len: int
    description_min_len: int
    media_min_images: int
    attribute_fill_rate_threshold: float
    content_completeness_threshold: float


class ManualSnapshot(BaseModel):
    sku: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    brand: Optional[str] = None
    category_path: Optional[List[str]] = None
    expected_category_path: Optional[List[str]] = None
    category_schema: Optional[ManualCategorySchema] = None
    attributes: Optional[List[ManualAttribute]] = None
    variants: Optional[List[str]] = None
    media: Optional[ManualMedia] = None
    constraints: Optional[ManualConstraints] = None
    field_availability: Optional[dict] = None


def build_snapshot_from_manual(payload: ManualSnapshot, *, listing_id: str,
                               marketplace: str) -> CardSnapshot:
    """CardSnapshot from an explicit manual payload. source='manual'. Honest availability."""
    availability = {
        "title": payload.title is not None,
        "description": payload.description is not None,
        "brand": payload.brand is not None,
        "category_path": payload.category_path is not None,
        "expected_category_path": payload.expected_category_path is not None,
        "category_schema": payload.category_schema is not None,
        "attributes": payload.attributes is not None,
        "variants": payload.variants is not None,
        "media": payload.media is not None,
        "constraints": payload.constraints is not None,
    }
    if payload.field_availability:
        availability.update(payload.field_availability)

    schema = (CategorySchema(
        required_attributes=tuple(payload.category_schema.required_attributes),
        filterable_attributes=tuple(payload.category_schema.filterable_attributes),
        variant_attributes=tuple(payload.category_schema.variant_attributes),
    ) if payload.category_schema else CategorySchema())

    attributes = tuple(
        CardAttribute(a.key, a.value, a.is_filled, a.is_valid_format)
        for a in payload.attributes
    ) if payload.attributes is not None else ()

    media = (CardMedia(payload.media.image_count, payload.media.video_present)
             if payload.media else CardMedia(image_count=0))

    constraints = (SeoConstraints(
        title_min_len=payload.constraints.title_min_len,
        title_max_len=payload.constraints.title_max_len,
        description_min_len=payload.constraints.description_min_len,
        media_min_images=payload.constraints.media_min_images,
        attribute_fill_rate_threshold=payload.constraints.attribute_fill_rate_threshold,
        content_completeness_threshold=payload.constraints.content_completeness_threshold,
    ) if payload.constraints else None)   # None → constraint rules not_evaluated

    return CardSnapshot(
        listing_id=listing_id, marketplace=marketplace, sku=payload.sku,
        captured_at=datetime.utcnow(), source="manual",
        title=payload.title, description=payload.description, brand=payload.brand,
        category_path=tuple(payload.category_path or ()),
        expected_category_path=(tuple(payload.expected_category_path)
                                if payload.expected_category_path is not None else None),
        category_schema=schema, attributes=attributes, variants=tuple(payload.variants or ()),
        media=media, constraints=constraints, field_availability=availability,
    )
