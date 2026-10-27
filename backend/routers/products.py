import uuid
from typing import List, NoReturn

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.product import Product
from schemas.product import ProductCreate, ProductResponse
from schemas.competitor import CompetitorResponse, CompetitorReport

router = APIRouter()


def _competitor_data_unavailable() -> NoReturn:
    """No trustworthy provenance exists for the legacy competitor surface."""
    raise HTTPException(status_code=404, detail="Not found")


@router.get("", response_model=List[ProductResponse])
async def list_products(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product).where(Product.user_id == current_user.id).order_by(Product.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=ProductResponse, status_code=201)
async def create_product(
    data: ProductCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = Product(user_id=current_user.id, **data.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


@router.get("/{product_id}/competitors", response_model=List[CompetitorResponse],
            dependencies=[Depends(_competitor_data_unavailable)])
async def get_competitors(
    product_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Also refuse direct internal calls that do not execute FastAPI dependencies.
    _competitor_data_unavailable()


@router.post("/{product_id}/competitors/refresh", status_code=202,
             dependencies=[Depends(_competitor_data_unavailable)])
async def refresh_competitors(
    product_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _competitor_data_unavailable()


@router.get("/{product_id}/report", response_model=CompetitorReport,
            dependencies=[Depends(_competitor_data_unavailable)])
async def get_report(
    product_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _competitor_data_unavailable()
