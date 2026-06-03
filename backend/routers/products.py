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
from models.competitor_analysis import CompetitorAnalysis
from schemas.product import ProductCreate, ProductResponse
from schemas.competitor import CompetitorResponse, CompetitorReport
from tasks.collect_competitors import collect_competitors

router = APIRouter()


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
    background_tasks.add_task(collect_competitors, str(product.id), product.marketplace)
    return product


@router.get("/{product_id}/competitors", response_model=List[CompetitorResponse])
async def get_competitors(
    product_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product_id_str = str(product_id)
    result = await db.execute(
        select(Product).where(Product.id == product_id_str, Product.user_id == current_user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Товар не найден")

    result = await db.execute(
        select(CompetitorAnalysis)
        .where(CompetitorAnalysis.product_id == product_id_str)
        .order_by(CompetitorAnalysis.rank)
    )
    return result.scalars().all()


@router.post("/{product_id}/competitors/refresh", status_code=202)
async def refresh_competitors(
    product_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product_id_str = str(product_id)
    result = await db.execute(
        select(Product).where(Product.id == product_id_str, Product.user_id == current_user.id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")

    background_tasks.add_task(collect_competitors, product_id_str, product.marketplace)
    return {"message": "Сбор данных запущен", "product_id": product_id_str}


@router.get("/{product_id}/report", response_model=CompetitorReport)
async def get_report(
    product_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product_id_str = str(product_id)
    result = await db.execute(
        select(Product).where(Product.id == product_id_str, Product.user_id == current_user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Товар не найден")

    result = await db.execute(
        select(CompetitorAnalysis)
        .where(CompetitorAnalysis.product_id == product_id_str)
        .order_by(CompetitorAnalysis.rank)
    )
    all_competitors = result.scalars().all()

    return CompetitorReport(
        product_id=product_id,
        total_competitors=len(all_competitors),
        direct=[c for c in all_competitors if c.significance == "direct"],
        significant=[c for c in all_competitors if c.significance == "significant"],
        minor=[c for c in all_competitors if c.significance == "minor"],
        generated_at=datetime.utcnow(),
    )
