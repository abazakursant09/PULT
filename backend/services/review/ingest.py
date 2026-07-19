"""Shared review ingestion (AR-AUTO-FILL).

The manual `POST /reviews/{pid}/sync` route and the scheduled auto-sync both call
`sync_product_reviews`, so fetch → classify → insert can never diverge between them. Dedup is the
DB partial-unique index (product_id, external_review_id, marketplace) enforced per row via a
savepoint — the guarantee is the database, not an app-level check.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.product import Product  # noqa: F401  (kept for callers importing product typing)
from models.review_response import ReviewResponse
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from services.marketplace import credential_vault
from services.marketplace.reviews import get_review_provider, review_credential, resolve_account_ref
from services.review.draft import classify_review

FEEDBACKS_SCOPE = "feedbacks"


class ReviewSyncUnsupported(Exception):
    """The marketplace has no PULT review provider (honest-unsupported)."""


class ReviewSyncNoConnection(Exception):
    """No connected marketplace connection for this seller + marketplace."""


class ReviewSyncNoScope(Exception):
    """The connection lacks the feedbacks credential."""


async def resolve_feedbacks_connection(db: AsyncSession, user_id: str, marketplace: str):
    """The seller's connected connection for a marketplace, or None."""
    return (
        await db.execute(
            select(MarketplaceConnection).where(
                MarketplaceConnection.user_id == user_id,
                MarketplaceConnection.marketplace == marketplace,
                MarketplaceConnection.status == "connected",
            )
        )
    ).scalars().first()


ACCOUNT_REF_KEY = "account_ref"


async def ensure_account_ref(db: AsyncSession, cred, provider, token: str) -> str | None:
    """The provider's account id for this credential, resolved once and cached on the row.

    Some marketplaces scope every call to an account rather than to the key — Yandex addresses
    reviews by cabinet. The seller does not type that id: it is discovered from the key and stored in
    `ApiCredential.meta`, so a restart does not re-ask and a rotated key re-resolves.

    Capability-driven: a provider that needs no account ref simply does not expose the resolver, and
    this returns None without the flow ever asking which marketplace it is talking to.
    """
    meta = dict(cred.meta or {})
    cached = meta.get(ACCOUNT_REF_KEY)
    if cached:
        return str(cached)

    ref = await resolve_account_ref(provider, token)
    if not ref:
        return None

    meta[ACCOUNT_REF_KEY] = str(ref)
    cred.meta = meta            # reassigned, not mutated — a JSON column needs a new object to dirty
    await db.commit()
    return str(ref)


async def sync_product_reviews(db: AsyncSession, product) -> dict:
    """Fetch REAL reviews for one product and insert new ones. Idempotent via the DB unique index.

    Raises ReviewSyncUnsupported / ReviewSyncNoConnection / ReviewSyncNoScope so callers map them
    (the router to HTTP codes, the scheduler to a skipped connection).
    """
    provider = get_review_provider(product.marketplace)
    if provider is None or not provider.supports_reviews():
        raise ReviewSyncUnsupported(product.marketplace)

    conn = await resolve_feedbacks_connection(db, product.user_id, product.marketplace)
    if conn is None:
        raise ReviewSyncNoConnection()

    cred = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.connection_id == conn.id, ApiCredential.scope == FEEDBACKS_SCOPE
            )
        )
    ).scalars().first()
    if cred is None:
        raise ReviewSyncNoScope()

    token = credential_vault.decrypt(cred.secret_enc)
    account_ref = await ensure_account_ref(db, cred, provider, token)
    # Shape the token into what this marketplace's provider expects (e.g. Ozon's composite
    # "<client_id>:<api_key>") — the same helper the publish dispatcher uses, so sync and publish agree.
    cred_token = review_credential(
        product.marketplace, token,
        {"ozon_client_id": conn.ozon_client_id, "account_ref": account_ref},
    )
    reviews = await provider.fetch_reviews(cred_token, product.sku)

    imported = 0
    skipped = 0
    for r in reviews:
        # Classify at ingestion (AR2): pure, marketplace-neutral safety policy.
        safety_category, manual_reason = classify_review(r.rating, r.text)
        try:
            async with db.begin_nested():
                db.add(ReviewResponse(
                    id=str(uuid.uuid4()), product_id=product.id,
                    review_text=r.text, author=r.author, rating=r.rating,
                    status="pending", external_review_id=r.external_review_id,
                    marketplace=r.marketplace, review_created_at=r.review_created_at,
                    safety_category=safety_category,
                    manual_required_reason=(manual_reason or None),
                ))
            imported += 1
        except IntegrityError:
            skipped += 1
    await db.commit()
    return {"synced": len(reviews), "imported": imported, "skipped": skipped}
