"""
Internal ReviewSnapshot source (A3) — build a snapshot from EXISTING PULT review
data (ReviewResponse), no external/MP API.

ReviewResponse already carries rating / review_text / response_text / status /
marketplace, so the snapshot is built from reviews the seller already has. Product
context (name/category/sku) comes from the linked legacy Product; brand is not
stored there → unavailable (honest). Safety category/modes come from the
deterministic ReviewSafetyPolicy. Never invents a review, never calls an MP client.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.review_response import ReviewResponse
from models.product import Product

from .snapshot import ReviewSnapshot, ReviewDataUnavailable
from .safety_policy import classify_safety


async def build_snapshot_from_reviews(
    db: AsyncSession, *, review_id: str, marketplace: Optional[str] = None,
    owner_user_id: Optional[str] = None, now: Optional[datetime] = None,
):
    """ReviewSnapshot from internal review data, or honest ReviewDataUnavailable.

    Ownership: a ReviewResponse has no direct user_id — ownership is proven via the
    linked Product (Product.user_id). When ``owner_user_id`` is given, a review the
    caller cannot be shown to own (missing product, product without that owner) is
    reported as ``review_missing`` — the SAME reason as a non-existent review, so we
    never leak the existence of another seller's review_id.
    """
    if db is None:
        return ReviewDataUnavailable(marketplace or "unknown", "no_db_context")
    if not review_id:
        return ReviewDataUnavailable(marketplace or "unknown", "insufficient_data", "review_id required")

    rr = await db.get(ReviewResponse, review_id)
    if rr is None:
        return ReviewDataUnavailable(marketplace or "unknown", "review_missing")

    prod = await db.get(Product, rr.product_id) if rr.product_id else None

    if owner_user_id is not None and (prod is None or prod.user_id != owner_user_id):
        # cannot prove ownership → indistinguishable from not-found (no data leak)
        return ReviewDataUnavailable(marketplace or "unknown", "review_missing")
    text = rr.review_text
    has_text = bool(text and text.strip())
    answered = bool(rr.response_text) or rr.status == "published"
    safety = classify_safety(rr.rating, has_text, text)

    availability = {
        "rating": rr.rating is not None,
        "text": text is not None,
        "has_text": True,
        "answered": True,
        "answer_text": rr.response_text is not None,
        "answer_created_at": rr.published_at is not None,
        "product_name": prod is not None,
        "brand": False,                       # not stored on legacy Product
        "category": bool(prod and prod.category),
        "safety_category": True,
    }

    return ReviewSnapshot(
        listing_id=None, marketplace=rr.marketplace or marketplace or "unknown",
        sku=(prod.sku if prod else None), captured_at=now or datetime.utcnow(), source="reviews",
        review_id=rr.id, rating=rr.rating, text=text, has_text=has_text,
        created_at=rr.created_at, answered=answered, answer_text=rr.response_text,
        answer_created_at=rr.published_at,
        product_name=(prod.name if prod else None), brand=None,
        category=(prod.category if prod else None),
        safety_category=safety.category, allowed_modes=safety.allowed_modes,
        default_mode=safety.default_mode, field_availability=availability,
    )
