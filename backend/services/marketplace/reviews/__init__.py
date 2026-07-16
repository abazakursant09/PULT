"""Review provider registry. A dict, deliberately — never an if/elif on the marketplace.

A conditional would put marketplace knowledge back into the ingestion flow, one branch at a time,
which is how a shared framework quietly becomes a Wildberries framework with a slot for everyone
else. Looking the provider up by key keeps that knowledge inside the provider module.

A marketplace with no provider is not an error: `get_review_provider` returns None and the router
answers an honest "unsupported". That is how Ozon, Yandex and Megamarket behave today — no special
case, only an absent entry. No fake providers are registered for them.
"""
from __future__ import annotations

from typing import Optional

from .base import NormalizedReview, ReviewProvider
from .wildberries import WildberriesReviewProvider
from .ozon import OzonReviewProvider

# marketplace label (as stored on Product.marketplace / MarketplaceConnection.marketplace) -> provider
REVIEW_PROVIDERS: dict[str, ReviewProvider] = {
    "wildberries": WildberriesReviewProvider(),
    # Ozon has a REAL fetch/publish client (R-OZ1), but its provider.supports_reviews() stays False
    # until R-OZ3 wires the publish dispatcher and flips capability_registry pult_supported — so the
    # /sync router still answers honest-unsupported for Ozon today. Registered, not yet enabled.
    "ozon": OzonReviewProvider(),
    # yandex / megamarket: no provider yet — get_review_provider returns None (honest unsupported).
}


def get_review_provider(marketplace: str) -> Optional[ReviewProvider]:
    """The review provider for this marketplace, or None when none exists yet."""
    return REVIEW_PROVIDERS.get(marketplace)


__all__ = ["NormalizedReview", "ReviewProvider", "REVIEW_PROVIDERS", "get_review_provider"]
