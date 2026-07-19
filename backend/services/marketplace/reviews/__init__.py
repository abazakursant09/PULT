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
from .yandex import YandexReviewProvider

# marketplace label (as stored on Product.marketplace / MarketplaceConnection.marketplace) -> provider
REVIEW_PROVIDERS: dict[str, ReviewProvider] = {
    "wildberries": WildberriesReviewProvider(),
    # Ozon has a REAL fetch/publish client (R-OZ1), but its provider.supports_reviews() stays False
    # until R-OZ3 wires the publish dispatcher and flips capability_registry pult_supported — so the
    # /sync router still answers honest-unsupported for Ozon today. Registered, not yet enabled.
    "ozon": OzonReviewProvider(),
    "yandex": YandexReviewProvider(),
    # megamarket: no provider yet — get_review_provider returns None (honest unsupported).
}


def get_review_provider(marketplace: str) -> Optional[ReviewProvider]:
    """The review provider for this marketplace, or None when none exists yet."""
    return REVIEW_PROVIDERS.get(marketplace)


# Per-marketplace credential shaping — a DICT, never an if/elif. The ReviewProvider contract passes
# a single `token`, but some marketplaces need more than a bearer: Ozon's provider expects the
# composite "<client_id>:<api_key>". Both the /sync router and the publish dispatcher shape the
# credential here so the two paths can never diverge, and a new marketplace adds a key (or uses the
# identity default), never a branch.
def _yandex_credential(token: str, ctx: Optional[dict]) -> str:
    """Yandex scopes every review call to a CABINET, which the seller never types: it is discovered
    from the key itself and cached on the credential row, arriving here as `account_ref`.

    When it is absent — a publish attempted before any sync ever resolved one — the token is handed
    over ALONE, so the provider rejects it as an incomplete credential. Interpolating the missing
    value would build the literal cabinet "None" and send a seller's reply at it.
    """
    account_ref = (ctx or {}).get("account_ref")
    return f"{account_ref}:{token}" if account_ref else token


_REVIEW_CREDENTIAL = {
    "ozon": lambda token, ctx: f"{(ctx or {}).get('ozon_client_id')}:{token}",
    "yandex": _yandex_credential,
}


def review_credential(marketplace: str, token: str, ctx: Optional[dict] = None) -> str:
    """Shape the raw scoped token into what this marketplace's provider expects."""
    return _REVIEW_CREDENTIAL.get(marketplace, lambda t, _c: t)(token, ctx)


async def resolve_account_ref(provider: ReviewProvider, token: str) -> Optional[str]:
    """The provider's account/cabinet id for this key, when it needs one.

    A CAPABILITY check, not a marketplace check: a provider that scopes its calls to an account
    exposes `resolve_account_ref`, and the ingestion flow asks every provider the same question
    without learning which marketplace answers it.
    """
    resolver = getattr(provider, "resolve_account_ref", None)
    if resolver is None:
        return None
    return await resolver(token)


async def answer_status(provider: ReviewProvider, token: str, external_review_id: str,
                        comment_id: Optional[str]) -> Optional[str]:
    """The marketplace's current view of OUR reply, for providers that moderate replies.

    Capability check again, not a marketplace check. None means "this provider does not moderate" or
    "the reply could not be found" — both are unknowns, and an unknown must never become a re-send.
    """
    checker = getattr(provider, "answer_status", None)
    if checker is None:
        return None
    return await checker(token, external_review_id, comment_id)


__all__ = ["NormalizedReview", "ReviewProvider", "REVIEW_PROVIDERS",
           "get_review_provider", "review_credential", "resolve_account_ref", "answer_status"]
