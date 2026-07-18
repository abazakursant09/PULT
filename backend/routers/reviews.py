import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.product import Product
from models.review_response import ReviewResponse
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from schemas.review import (ReviewResponseOut, ReviewResponseUpdate,
    ReviewQueueResponse, ReviewHistoryEntry, ReviewHistoryResponse)
from services.review.state import derive_state
from services.marketplace import executor, credential_vault
from services.marketplace.wb_client import wb_client
from services.marketplace.reviews import get_review_provider, review_credential
from services.review.draft import classify_review, build_draft
from services.review import ingest as review_ingest
from models.execution_log import ExecutionLog

log = logging.getLogger(__name__)
router = APIRouter()

# Statuses a draft may hold before it is really published via the executor.
_DRAFT_STATUSES = {"pending", "generated", "draft", "approved"}


async def _get_product_or_404(product_id: str, user_id: str, db: AsyncSession) -> Product:
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.user_id == user_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return product


@router.get("/reviews/queue", response_model=ReviewQueueResponse)
async def review_queue(
    state: str | None = None,
    marketplace: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Seller-wide review queue across ALL the seller's products (AR3).

    Owner-scoped via product -> user. `marketplace` filters at the DB; `state` is a derived value
    (status + safety + failure) so it is filtered after derivation. No marketplace branching —
    the marketplace is only ever a filter value, never a code path.
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    stmt = (
        select(ReviewResponse)
        .join(Product, Product.id == ReviewResponse.product_id)
        .where(Product.user_id == current_user.id)
    )
    if marketplace:
        stmt = stmt.where(ReviewResponse.marketplace == marketplace)
    stmt = stmt.order_by(ReviewResponse.created_at.desc())
    rows = (await db.execute(stmt)).scalars().all()

    items = [ReviewResponseOut.of(r) for r in rows]
    if state:
        items = [it for it in items if it.state == state]
    total = len(items)
    page = items[offset:offset + limit]
    return ReviewQueueResponse(items=page, total=total, limit=limit, offset=offset)


@router.get("/reviews/{product_id}", response_model=List[ReviewResponseOut])
async def list_reviews(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_product_or_404(product_id, current_user.id, db)
    result = await db.execute(
        select(ReviewResponse)
        .where(ReviewResponse.product_id == product_id)
        .order_by(ReviewResponse.created_at.desc())
    )
    return [ReviewResponseOut.of(r) for r in result.scalars().all()]


# NOTE: POST /reviews/{product_id}/generate was removed in AR0 (Auto Reviews). It ran
# tasks/generate_review_responses, which DELETED a product's real review rows and inserted
# fabricated reviews/answers — a "No fake data" violation. Draft generation for real synced
# reviews is a later Auto Reviews phase and will operate on real ingested data only.


@router.post("/reviews/{product_id}/sync", status_code=200)
async def sync_reviews(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Pull REAL unanswered feedbacks from Wildberries and store them with their
    external_review_id, so they can be answered for real. Read-only on the
    marketplace side (no execution authority needed).
    """
    product = await _get_product_or_404(product_id, current_user.id, db)

    # Shared ingestion service — the same fetch→classify→insert the scheduled auto-sync uses, so the
    # manual and automatic paths can never diverge. Honest errors map to HTTP codes here.
    try:
        result = await review_ingest.sync_product_reviews(db, product)
    except review_ingest.ReviewSyncUnsupported:
        raise HTTPException(422, f"приём отзывов для «{product.marketplace}» пока не поддерживается")
    except review_ingest.ReviewSyncNoConnection:
        raise HTTPException(409, "нет подключённого кабинета маркетплейса — добавьте его в connections")
    except review_ingest.ReviewSyncNoScope:
        raise HTTPException(409, "у подключения нет области доступа «feedbacks»")
    return {**result, "product_id": product_id}


@router.patch("/reviews/{product_id}/{review_id}", response_model=ReviewResponseOut)
async def update_review_response(
    product_id: str,
    review_id: str,
    data: ReviewResponseUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit the draft answer text or internal status. Publishing is NOT done here
    anymore — it must go through POST /reviews/{pid}/{rid}/publish, which calls
    the real marketplace API. Setting status='published' here is rejected."""
    await _get_product_or_404(product_id, current_user.id, db)

    result = await db.execute(
        select(ReviewResponse).where(
            ReviewResponse.id == review_id,
            ReviewResponse.product_id == product_id,
        )
    )
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Отзыв не найден")

    if data.response_text is not None:
        review.response_text = data.response_text
    if data.status is not None:
        if data.status == "published":
            raise HTTPException(
                status_code=409,
                detail="'published' нельзя выставить вручную — используйте /publish "
                       "(реальная публикация в маркетплейс)",
            )
        if data.status not in _DRAFT_STATUSES:
            raise HTTPException(422, f"недопустимый статус черновика: {data.status}")
        review.status = data.status

    await db.commit()
    await db.refresh(review)
    return ReviewResponseOut.of(review)


async def _owned_review_or_404(product_id: str, review_id: str, user_id: str, db: AsyncSession):
    """Return (review, product) with ownership enforced via product → user. The product is
    returned explicitly so callers never lazy-load review.product in the async session."""
    product = await _get_product_or_404(product_id, user_id, db)
    review = (await db.execute(
        select(ReviewResponse).where(
            ReviewResponse.id == review_id,
            ReviewResponse.product_id == product_id,
        )
    )).scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Отзыв не найден")
    return review, product


@router.post("/reviews/{product_id}/{review_id}/draft", response_model=ReviewResponseOut)
async def draft_review_response(
    product_id: str,
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a safe, deterministic draft reply for a real synced review (AR2).

    RISK reviews (negatives / complaints) never get a sendable draft — build_draft returns None
    and the row stays manual, with its manual_required_reason intact for the human. Nothing is
    published here; the draft only fills response_text and moves the row to 'drafted'.
    """
    review, product = await _owned_review_or_404(product_id, review_id, current_user.id, db)
    if review.status == "published":
        raise HTTPException(409, "Отзыв уже опубликован")

    draft = build_draft(review.rating, review.review_text, product.name if product else None)
    if draft is None:
        # RISK: no auto-draft. Leave response_text empty and keep the manual reason.
        raise HTTPException(
            422,
            review.manual_required_reason or "Этот отзыв отвечается вручную — автоматический черновик не создаётся.",
        )

    review.response_text = draft
    review.status = "drafted"
    await db.commit()
    await db.refresh(review)
    return ReviewResponseOut.of(review)


@router.post("/reviews/{product_id}/{review_id}/approve", response_model=ReviewResponseOut)
async def approve_review_response(
    product_id: str,
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Human approval of a draft (AR2). Marks the row 'approved' — it does NOT publish. Real
    publication remains the separate /publish action. A row with no reply text cannot be approved.
    """
    review, _product = await _owned_review_or_404(product_id, review_id, current_user.id, db)
    if not (review.response_text or "").strip():
        raise HTTPException(422, "Нельзя одобрить пустой ответ — сначала создайте или напишите черновик.")
    if review.status == "published":
        raise HTTPException(409, "Отзыв уже опубликован")
    review.status = "approved"
    await db.commit()
    await db.refresh(review)
    return ReviewResponseOut.of(review)


@router.post("/reviews/{product_id}/{review_id}/publish", response_model=ReviewResponseOut)
async def publish_review_response(
    product_id: str,
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    L3 — one-click Execute. Publishes the drafted answer to the real marketplace
    via the executor. ReviewResponse becomes 'published' ONLY on real API
    success (no local imitation).
    """
    await _get_product_or_404(product_id, current_user.id, db)
    review = (
        await db.execute(
            select(ReviewResponse).where(
                ReviewResponse.id == review_id,
                ReviewResponse.product_id == product_id,
            )
        )
    ).scalars().first()
    if not review:
        raise HTTPException(404, "Отзыв не найден")
    if not review.external_review_id:
        raise HTTPException(
            409, "У отзыва нет marketplace-id — публиковать можно только синхронизированные отзывы (/sync)",
        )
    if not (review.response_text or "").strip():
        raise HTTPException(422, "Нет текста ответа для публикации")
    if review.status == "published":
        raise HTTPException(409, "Отзыв уже опубликован")

    res = await executor.execute(
        db=db,
        user_id=current_user.id,
        action_type="publish_review_response",
        payload={
            "marketplace": review.marketplace,   # R-OZ2: routes to the review's marketplace provider
            "feedback_id": review.external_review_id,
            "text": review.response_text,
            "rating": review.rating,
        },
        mode="manual_l3",
        insight_key="rating_good",
        idempotency_key=f"review:{review.id}",
    )

    if res.status in ("ambiguous", "needs_reconcile"):
        # AR5: the write may have committed on the marketplace but no clean response came back.
        # Never present this as a failure the seller should simply retry — a second click hits the
        # executor idempotency (which returns needs_reconcile) and does NOT re-call the API. Record
        # it and ask the seller to verify the cabinet.
        review.failure_reason = "публикация не подтверждена — проверьте кабинет маркетплейса"
        review.publication_attempts = (review.publication_attempts or 0) + 1
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail={"message": "Публикация не подтверждена — проверьте кабинет маркетплейса перед повторной попыткой",
                    "error": res.error, "log_id": res.log_id},
        )

    if not res.ok:
        # Failure visibility (AR3): record the reason on the row and count the attempt, so the
        # workspace can show a real "Failed" state. Only this already-failing branch is touched —
        # the successful path below is unchanged, and the executor / idempotency are not involved.
        review.failure_reason = (str(res.error) if res.error else "publish failed")[:255]
        review.publication_attempts = (review.publication_attempts or 0) + 1
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail={"message": "Публикация не удалась", "error": res.error, "log_id": res.log_id},
        )

    review.status = "published"
    review.published_at = datetime.utcnow()
    review.execution_log_id = res.log_id
    await db.commit()
    await db.refresh(review)
    log.info("review published for real: user=%s review=%s log=%s",
             current_user.id, review_id, res.log_id)
    return ReviewResponseOut.of(review)


@router.get("/reviews/{product_id}/{review_id}/history", response_model=ReviewHistoryResponse)
async def review_history(
    product_id: str,
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Publish-attempt history for one review (AR3), read from the append-only ExecutionLog.

    Owner-scoped: the product ownership check runs first, and the log query is filtered by
    user_id and this review's idempotency key. Read-only; touches no marketplace.
    """
    review, _product = await _owned_review_or_404(product_id, review_id, current_user.id, db)
    logs = (await db.execute(
        select(ExecutionLog).where(
            ExecutionLog.user_id == current_user.id,
            ExecutionLog.action_type == "publish_review_response",
            ExecutionLog.idempotency_key == f"review:{review.id}",
        ).order_by(ExecutionLog.created_at.desc())
    )).scalars().all()
    return ReviewHistoryResponse(
        review_id=review.id,
        entries=[ReviewHistoryEntry(
            timestamp=(lg.finished_at or lg.created_at),
            mode=lg.mode, status=lg.status, error_code=lg.error_code,
        ) for lg in logs],
    )
