from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.legal_case import LegalCase
from models.product import Product
from schemas.legal import LegalCaseOut, LegalCaseUpdate, ReviewAnalyzeRequest
from tasks.legal_ai import audit_product_card, analyze_review_legal

router = APIRouter()


async def verify_product(product_id: str, user_id: str, db: AsyncSession) -> Product:
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.user_id == user_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return product


@router.get("/products/{product_id}/legal/cases", response_model=list[LegalCaseOut])
async def list_cases(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_product(product_id, current_user.id, db)

    result = await db.execute(
        select(LegalCase)
        .where(LegalCase.product_id == product_id)
        .order_by(LegalCase.created_at.desc())
    )
    return result.scalars().all()


@router.post("/products/{product_id}/legal/card-audit", response_model=list[LegalCaseOut])
async def card_audit(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await verify_product(product_id, current_user.id, db)

    cases = await audit_product_card(
        product_id=product_id,
        product_name=product.name,
        product_category=product.category,
        db=db,
    )
    return cases


@router.post("/products/{product_id}/legal/analyze-review", response_model=LegalCaseOut)
async def analyze_review(
    product_id: str,
    body: ReviewAnalyzeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_product(product_id, current_user.id, db)

    if not body.review_text.strip():
        raise HTTPException(status_code=422, detail="Текст отзыва не может быть пустым")

    case = await analyze_review_legal(
        product_id=product_id,
        review_text=body.review_text,
        db=db,
    )
    return case


@router.patch("/products/{product_id}/legal/cases/{case_id}", response_model=LegalCaseOut)
async def update_case(
    product_id: str,
    case_id: str,
    body: LegalCaseUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_product(product_id, current_user.id, db)

    result = await db.execute(
        select(LegalCase).where(LegalCase.id == case_id, LegalCase.product_id == product_id)
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Кейс не найден")

    if body.status is not None:
        case.status = body.status
    if body.user_response is not None:
        case.user_response = body.user_response

    await db.commit()
    await db.refresh(case)
    return case
