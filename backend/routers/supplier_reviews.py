from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from database import get_db
from models.supplier_review import SupplierReview
from models.supplier import Supplier
from models.transport_company import TransportCompany
from dependencies import get_current_user
from models.user import User

router = APIRouter()

TARGET_TYPES = {"supplier", "transport_company"}


class ReviewCreate(BaseModel):
    target_type: str
    target_id:   str
    deal_id:     Optional[str] = None
    rating:      int = Field(..., ge=1, le=5)
    text:        Optional[str] = None


class ReviewOut(BaseModel):
    id:          str
    reviewer_id: str
    target_type: str
    target_id:   str
    deal_id:     Optional[str]
    rating:      int
    text:        Optional[str]
    created_at:  datetime

    class Config:
        from_attributes = True


async def _recalc_rating(db: AsyncSession, target_type: str, target_id: str) -> None:
    q = select(
        func.avg(SupplierReview.rating),
        func.count(SupplierReview.id),
    ).where(
        SupplierReview.target_type == target_type,
        SupplierReview.target_id   == target_id,
    )
    row = (await db.execute(q)).one()
    avg_rating   = float(row[0] or 0)
    total_reviews = int(row[1] or 0)

    if target_type == "supplier":
        obj = await db.get(Supplier, target_id)
    else:
        obj = await db.get(TransportCompany, target_id)

    if obj:
        obj.rating       = round(avg_rating, 1)
        obj.total_reviews = total_reviews


@router.post("/supplier-reviews", response_model=ReviewOut)
async def create_review(
    body: ReviewCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.target_type not in TARGET_TYPES:
        raise HTTPException(400, f"target_type must be one of {TARGET_TYPES}")

    # Check no duplicate review from same user for same target
    dup = await db.execute(
        select(SupplierReview).where(
            SupplierReview.reviewer_id == current_user.id,
            SupplierReview.target_type == body.target_type,
            SupplierReview.target_id   == body.target_id,
        )
    )
    if dup.scalars().first():
        raise HTTPException(400, "You have already reviewed this entity")

    review = SupplierReview(
        reviewer_id=current_user.id,
        target_type=body.target_type,
        target_id=body.target_id,
        deal_id=body.deal_id,
        rating=body.rating,
        text=body.text,
    )
    db.add(review)
    await db.flush()
    await _recalc_rating(db, body.target_type, body.target_id)
    await db.commit()
    await db.refresh(review)
    return review


@router.get("/supplier-reviews/{target_type}/{target_id}", response_model=list[ReviewOut])
async def list_reviews(
    target_type: str,
    target_id:   str,
    limit:  int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    if target_type not in TARGET_TYPES:
        raise HTTPException(400, f"target_type must be one of {TARGET_TYPES}")
    q = (
        select(SupplierReview)
        .where(
            SupplierReview.target_type == target_type,
            SupplierReview.target_id   == target_id,
        )
        .order_by(SupplierReview.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(q)
    return result.scalars().all()
