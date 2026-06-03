import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.product import Product
from models.review_response import ReviewResponse
from schemas.review import ReviewResponseOut, ReviewResponseUpdate
from tasks.generate_review_responses import generate_review_responses

log = logging.getLogger(__name__)
router = APIRouter()


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
    await _get_product_or_404(product_id, current_user.id, db)
    background_tasks.add_task(generate_review_responses, product_id)
    return {"message": "Генерация ответов запущена", "product_id": product_id}


@router.patch("/reviews/{product_id}/{review_id}", response_model=ReviewResponseOut)
async def update_review_response(
    product_id: str,
    review_id: str,
    data: ReviewResponseUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
        old_status = review.status
        review.status = data.status
        if data.status in ("published", "approved") and old_status != data.status:
            log.info(
                "review_response_published: user=%s product=%s review=%s status=%s",
                current_user.id, product_id, review_id, data.status,
            )

    await db.commit()
    await db.refresh(review)
    return review
