from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from database import get_db
from models.deal import Deal
from dependencies import get_current_user
from models.user import User

router = APIRouter()

VALID_STATUSES = ["draft", "agreed", "paid", "shipped", "delivered", "cancelled"]


class DealCreate(BaseModel):
    supplier_id:    str
    product_name:   str
    specification:  Optional[str] = None
    price_per_unit: float = Field(..., gt=0)
    quantity:       int   = Field(..., gt=0)
    deadline:       Optional[datetime] = None


class DealStatusUpdate(BaseModel):
    status: str


class DealOut(BaseModel):
    id:              str
    seller_id:       str
    supplier_id:     str
    product_name:    str
    specification:   Optional[str]
    price_per_unit:  float
    quantity:        int
    total_price:     float
    deadline:        Optional[datetime]
    status:          str
    contract_text:   Optional[str]
    signed_by_seller: bool
    signed_at:       Optional[datetime]
    created_at:      datetime
    updated_at:      datetime

    class Config:
        from_attributes = True


def _generate_contract(deal: Deal) -> str:
    return (
        f"ДОГОВОР ПОСТАВКИ № {deal.id[:8].upper()}\n"
        f"Дата: {deal.created_at.strftime('%d.%m.%Y')}\n\n"
        f"Предмет договора: {deal.product_name}\n"
        f"Спецификация: {deal.specification or '—'}\n"
        f"Цена за единицу: {deal.price_per_unit:.2f} руб.\n"
        f"Количество: {deal.quantity} ед.\n"
        f"Итоговая сумма: {deal.total_price:.2f} руб.\n"
        f"Срок поставки: {deal.deadline.strftime('%d.%m.%Y') if deal.deadline else '—'}\n\n"
        f"Поставщик ID: {deal.supplier_id}\n"
        f"Покупатель ID: {deal.seller_id}\n\n"
        f"Стороны обязуются исполнять условия настоящего договора в полном объёме."
    )


@router.post("/deals", response_model=DealOut)
async def create_deal(
    body: DealCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    total = body.price_per_unit * body.quantity
    deal = Deal(
        seller_id=current_user.id,
        supplier_id=body.supplier_id,
        product_name=body.product_name,
        specification=body.specification,
        price_per_unit=body.price_per_unit,
        quantity=body.quantity,
        total_price=total,
        deadline=body.deadline,
        status="draft",
    )
    db.add(deal)
    await db.flush()
    deal.contract_text = _generate_contract(deal)
    await db.commit()
    await db.refresh(deal)
    return deal


@router.get("/deals/my", response_model=list[DealOut])
async def my_deals(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(Deal).where(Deal.seller_id == current_user.id).order_by(Deal.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/deals/{deal_id}", response_model=DealOut)
async def get_deal(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(404, "Deal not found")
    if deal.seller_id != current_user.id:
        raise HTTPException(403, "Access denied")
    return deal


@router.patch("/deals/{deal_id}/status", response_model=DealOut)
async def update_deal_status(
    deal_id: str,
    body: DealStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(404, "Deal not found")
    if deal.seller_id != current_user.id:
        raise HTTPException(403, "Access denied")
    if body.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Allowed: {VALID_STATUSES}")
    deal.status = body.status
    deal.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(deal)
    return deal


@router.post("/deals/{deal_id}/sign", response_model=DealOut)
async def sign_deal(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(404, "Deal not found")
    if deal.seller_id != current_user.id:
        raise HTTPException(403, "Access denied")
    if deal.signed_by_seller:
        raise HTTPException(400, "Already signed")
    deal.signed_by_seller = True
    deal.signed_at = datetime.utcnow()
    deal.status = "agreed"
    deal.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(deal)
    return deal
