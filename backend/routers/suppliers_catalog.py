from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from database import get_db
from models.supplier import Supplier

router = APIRouter()


class SupplierOut(BaseModel):
    id: str
    company_name: str
    industry: Optional[str]
    region: Optional[str]
    country: str
    description: Optional[str]
    website: Optional[str]
    phone: Optional[str]
    min_order_qty: Optional[int]
    is_verified: bool
    rating: float
    total_reviews: int
    total_deals: int

    class Config:
        from_attributes = True


@router.get("/catalog/suppliers", response_model=list[SupplierOut])
async def list_suppliers(
    industry:  Optional[str] = Query(None),
    region:    Optional[str] = Query(None),
    country:   Optional[str] = Query(None),
    verified:  Optional[bool] = Query(None),
    search:    Optional[str] = Query(None),
    sort:      str = Query("rating"),  # rating | deals | reviews | name
    db: AsyncSession = Depends(get_db),
):
    q = select(Supplier)

    if industry:
        q = q.where(Supplier.industry == industry)
    if region:
        q = q.where(Supplier.region.ilike(f"%{region}%"))
    if country:
        q = q.where(Supplier.country == country)
    if verified is not None:
        q = q.where(Supplier.is_verified == verified)
    if search:
        term = f"%{search}%"
        q = q.where(or_(
            Supplier.company_name.ilike(term),
            Supplier.description.ilike(term),
        ))

    order_col = {
        "rating":  Supplier.rating.desc(),
        "deals":   Supplier.total_deals.desc(),
        "reviews": Supplier.total_reviews.desc(),
        "name":    Supplier.company_name.asc(),
    }.get(sort, Supplier.rating.desc())
    q = q.order_by(order_col)

    result = await db.execute(q)
    return result.scalars().all()


@router.get("/catalog/suppliers/{supplier_id}", response_model=SupplierOut)
async def get_supplier(supplier_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(Supplier, supplier_id)
    if not row:
        raise HTTPException(404, "Supplier not found")
    return row
