from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.product import Product
from schemas.finance import FinancialSnapshotOut, FinanceSummaryItem
from services.finance_aggregator import summary_by_product, product_monthly_rollup

# ── Step 2: Ledger Unification ───────────────────────────────────────────────
# Canonical money source = imported_finance_rows (ledger), aggregated by
# product_id. FinancialSnapshot is NO LONGER read or written here (random
# generator demoted). All money — strip, summary, per-product — derives from the
# same ledger as the Action Engine, so there is one money truth.

router = APIRouter()


async def _get_product_or_404(product_id: str, user_id: str, db: AsyncSession) -> Product:
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.user_id == user_id)
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return p


# ── GET per-product monthly rollup (from ledger) ──────────────────────────────

@router.get("/products/{product_id}/finance", response_model=List[FinancialSnapshotOut])
async def list_snapshots(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_product_or_404(product_id, current_user.id, db)
    return await product_monthly_rollup(product_id, str(current_user.id), db)


# ── POST "generate" — DEMOTED: returns the ledger rollup, writes nothing ──────
# Kept for frontend compatibility (the button now just refreshes from imports).
# No more random FinancialSnapshot rows enter the money path.

@router.post("/products/{product_id}/finance/generate", response_model=List[FinancialSnapshotOut])
async def generate_snapshots(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_product_or_404(product_id, current_user.id, db)
    return await product_monthly_rollup(product_id, str(current_user.id), db)


# ── GET finance summary across all user products (LEDGER) ─────────────────────

@router.get("/finance/summary", response_model=List[FinanceSummaryItem])
async def finance_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, _totals = await summary_by_product(str(current_user.id), db)
    return [FinanceSummaryItem(**it) for it in items]
