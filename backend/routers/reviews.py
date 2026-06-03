import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.product import Product
from models.review_response import ReviewResponse
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from schemas.review import ReviewResponseOut, ReviewResponseUpdate
from tasks.generate_review_responses import generate_review_responses
from services.marketplace import executor, credential_vault
from services.marketplace.wb_client import wb_client

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
    return result.scalars().all()


@router.post("/reviews/{product_id}/generate", status_code=202)
async def generate_reviews(
    product_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate DRAFT answers (L2). Publishing them is a separate real action."""
    await _get_product_or_404(product_id, current_user.id, db)
    background_tasks.add_task(generate_review_responses, product_id)
    return {"message": "Генерация черновиков запущена", "product_id": product_id}


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
    if product.marketplace != "wildberries":
        raise HTTPException(422, "sync currently supports Wildberries only (ME-2)")

    conn = (
        await db.execute(
            select(MarketplaceConnection).where(
                MarketplaceConnection.user_id == current_user.id,
                MarketplaceConnection.marketplace == "wildberries",
                MarketplaceConnection.status == "connected",
            )
        )
    ).scalars().first()
    if conn is None:
        raise HTTPException(409, "no connected Wildberries cabinet — add one in connections")

    cred = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.connection_id == conn.id, ApiCredential.scope == "feedbacks"
            )
        )
    ).scalars().first()
    if cred is None:
        raise HTTPException(409, "Wildberries connection lacks 'feedbacks' scope")

    token = credential_vault.decrypt(cred.secret_enc)
    feedbacks = await wb_client.list_unanswered_feedbacks(token=token, nm_id=product.sku)

    imported = 0
    for fb in feedbacks:
        ext_id = str(fb.get("id") or "")
        if not ext_id:
            continue
        exists = (
            await db.execute(
                select(ReviewResponse).where(
                    ReviewResponse.product_id == product_id,
                    ReviewResponse.external_review_id == ext_id,
                )
            )
        ).scalars().first()
        if exists:
            continue
        db.add(ReviewResponse(
            id=str(uuid.uuid4()), product_id=product_id,
            review_text=fb.get("text"), author=(fb.get("userName") or None),
            rating=fb.get("productValuation"), status="pending",
            external_review_id=ext_id, marketplace="wildberries",
        ))
        imported += 1
    await db.commit()
    return {"synced": len(feedbacks), "imported": imported, "product_id": product_id}


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
    return review


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
            "feedback_id": review.external_review_id,
            "text": review.response_text,
            "rating": review.rating,
        },
        mode="manual_l3",
        insight_key="rating_good",
        idempotency_key=f"review:{review.id}",
    )

    if not res.ok:
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
    return review
