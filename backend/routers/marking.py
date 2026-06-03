from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.product import Product

router = APIRouter()

# Категории, требующие маркировки "Честный ЗНАК"
MARKED_CATEGORIES: dict[str, str] = {
    "одежда":          "Постановление Правительства РФ № 1956",
    "обувь":           "Постановление Правительства РФ № 860",
    "парфюмерия":      "Постановление Правительства РФ № 1957",
    "постельное":      "Постановление Правительства РФ № 791",
    "шины":            "Постановление Правительства РФ № 515",
    "фото":            "Постановление Правительства РФ № 515",
    "молоко":          "Постановление Правительства РФ № 792",
    "молочн":          "Постановление Правительства РФ № 792",
    "вода":            "Постановление Правительства РФ № 841",
    "пиво":            "Постановление Правительства РФ № 1735",
    "табак":           "Постановление Правительства РФ № 224",
    "лекарств":        "Постановление Правительства РФ № 1018",
    "бад":             "Постановление Правительства РФ № 64",
    "антисептик":      "Постановление Правительства РФ № 64",
    "кресла":          "Постановление Правительства РФ № 1575",
    "велосипед":       "Постановление Правительства РФ № 64",
    "коляск":          "Постановление Правительства РФ № 64",
    "медицин":         "Постановление Правительства РФ № 1018",
    "духи":            "Постановление Правительства РФ № 1957",
    "туалетная вода":  "Постановление Правительства РФ № 1957",
}


def _needs_marking(category: str) -> Optional[str]:
    cat_lower = category.lower()
    for keyword, regulation in MARKED_CATEGORIES.items():
        if keyword in cat_lower:
            return regulation
    return None


class MarkingResult(BaseModel):
    category: str
    requires_marking: bool
    regulation: Optional[str] = None
    warning: Optional[str] = None


class ProductMarkingStatus(BaseModel):
    product_id: str
    product_name: str
    category: Optional[str]
    requires_marking: bool
    regulation: Optional[str] = None


@router.get("/marking/check", response_model=MarkingResult)
async def check_category(
    category: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
):
    regulation = _needs_marking(category)
    if regulation:
        return MarkingResult(
            category=category,
            requires_marking=True,
            regulation=regulation,
            warning=f"Товары категории «{category}» подлежат обязательной маркировке «Честный ЗНАК». Основание: {regulation}.",
        )
    return MarkingResult(category=category, requires_marking=False)


@router.get("/marking/scan", response_model=List[ProductMarkingStatus])
async def scan_products(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product).where(Product.user_id == current_user.id)
    )
    products = result.scalars().all()

    statuses: List[ProductMarkingStatus] = []
    for p in products:
        cat = p.category or ""
        regulation = _needs_marking(cat) if cat else None
        statuses.append(ProductMarkingStatus(
            product_id=str(p.id),
            product_name=p.name,
            category=cat or None,
            requires_marking=regulation is not None,
            regulation=regulation,
        ))
    return statuses
