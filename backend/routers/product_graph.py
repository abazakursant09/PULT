"""
Product Graph API (Doctrine §3/§7) — read-only.

GET /api/product-graph            полное дерево Товар→Листинг→Решение + summary
GET /api/product-graph/summary    только счётчики (дёшево для бейджей)
GET /api/product-graph/{id}       один атом

Только чтение. Scope по current_user. Не мутирует граф, не трогает ingest.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from schemas.product_graph import ProductGraph, GraphSummary, AtomNode
from services.product_graph import get_product_graph, get_atom

router = APIRouter()


@router.get("", response_model=ProductGraph)
async def read_product_graph(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_product_graph(str(current_user.id), db)


@router.get("/summary", response_model=GraphSummary)
async def read_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    graph = await get_product_graph(str(current_user.id), db)
    return graph["summary"]


@router.get("/{physical_product_id}", response_model=AtomNode)
async def read_atom(
    physical_product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    atom = await get_atom(str(current_user.id), physical_product_id, db)
    if atom is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return atom
