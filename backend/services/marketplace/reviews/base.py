"""Marketplace-neutral review ingestion contract.

A `ReviewProvider` fetches (and, later, publishes answers to) reviews for one marketplace. The
router speaks only this contract — never a marketplace client directly — so marketplace knowledge
lives inside the provider module, not in the ingestion flow. `NormalizedReview` is the shape every
provider returns; anything marketplace-specific goes in `metadata`, never in the core model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable


@dataclass
class NormalizedReview:
    """One marketplace review, in the shape ReviewResponse ingestion consumes.

    Every field is marketplace-agnostic. Raw, provider-specific payload keys (WB `productValuation`,
    an Ozon status, etc.) belong in `metadata`, so the core model never grows a marketplace column.
    """
    marketplace: str
    external_review_id: str
    product_ref: str
    rating: Optional[int] = None
    text: Optional[str] = None
    author: Optional[str] = None
    review_created_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class ReviewProvider(Protocol):
    """One marketplace's review capability. Looked up by key in the registry — never via an
    if/elif on the marketplace name."""

    def supports_reviews(self) -> bool:
        """True if this provider can actually read reviews today (real client, not a stub)."""
        ...

    async def fetch_reviews(self, token: str, product_ref: str) -> list[NormalizedReview]:
        """Read unanswered reviews for one product, normalized. Read-only on the marketplace."""
        ...

    async def publish_answer(self, token: str, external_review_id: str, text: str) -> dict:
        """Publish an answer. Interface only in AR1 — the /publish flow is unchanged this phase."""
        ...
