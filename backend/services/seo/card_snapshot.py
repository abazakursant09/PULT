"""
Canonical CardSnapshot (SEO A3) — the single, marketplace-agnostic input to the
SEO Audit Engine.

The SEO core consumes ONLY this snapshot. Adapters (WB/Ozon/Yandex) build it from
their own APIs; the engine never sees marketplace-specific structures. Every
marketplace-specific limit lives in `constraints` — the core must never hardcode
WB/Ozon/Yandex thresholds. `SeoConstraints` has NO defaults on purpose: a
snapshot cannot exist without externally-supplied limits, so the core physically
cannot fabricate them.

Pure data only — no logic, no rules, no metrics, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional


@dataclass(frozen=True)
class SeoConstraints:
    """All marketplace-specific limits — supplied by the adapter, never by core.
    No defaults: the core cannot invent a threshold."""
    title_min_len: int
    title_max_len: int
    description_min_len: int
    media_min_images: int
    attribute_fill_rate_threshold: float
    content_completeness_threshold: float


@dataclass(frozen=True)
class CategorySchema:
    required_attributes: tuple[str, ...] = ()
    filterable_attributes: tuple[str, ...] = ()
    variant_attributes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CardAttribute:
    key: str
    value: Optional[str]
    is_filled: bool
    is_valid_format: bool = True


@dataclass(frozen=True)
class CardMedia:
    image_count: int
    video_present: bool = False


@dataclass(frozen=True)
class CardSnapshot:
    """Canonical, marketplace-agnostic card state. Adapter-built, engine-consumed."""
    listing_id: str
    marketplace: str                 # provenance / dispatch only
    sku: Optional[str]
    captured_at: datetime
    source: str                      # e.g. "api" | "import"

    title: Optional[str]
    description: Optional[str]
    brand: Optional[str]

    category_path: tuple[str, ...]
    expected_category_path: Optional[tuple[str, ...]]
    category_schema: CategorySchema

    attributes: tuple[CardAttribute, ...]
    variants: tuple[str, ...]        # variant attribute keys present on the card
    media: CardMedia

    constraints: SeoConstraints      # MP-specific limits live HERE, not in core
    field_availability: Mapping[str, bool]   # honest map of which fields the adapter supplied
