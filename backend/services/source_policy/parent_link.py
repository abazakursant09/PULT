"""Attribute a money operation to a Product via its parent order/posting (PULT-LAUNCH-1.4.5H3).

Some finance operations carry no direct Product but do carry a proven parent id (WB: the finance
row's srid == the order operation's external id). When the parent operation is unambiguously tied to
one Product, its product_id may fill the child — but ONLY on an exact external_parent_id match within
the SAME account, and never when several candidate parents disagree. Works in both arrival orders:
if the parent already exists the child is filled on write; if the parent arrives later, writing the
parent backfills the waiting children. The operation's own external id never changes.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.marketplace_operation import MarketplaceOperation


async def resolve_parent_product(db: AsyncSession, account_id: str,
                                 external_parent_id: Optional[str]) -> Optional[str]:
    """The Product of the parent operation named by external_parent_id, or None if there is no such
    parent, it has no product, or several candidate parents disagree (ambiguous → never guess)."""
    if not external_parent_id:
        return None
    pids = [p for (p,) in (await db.execute(
        select(MarketplaceOperation.product_id).where(
            MarketplaceOperation.marketplace_account_id == account_id,
            MarketplaceOperation.source == "api",
            MarketplaceOperation.external_operation_id == external_parent_id,
            MarketplaceOperation.product_id.isnot(None))
        .distinct())).all()]
    return pids[0] if len(pids) == 1 else None


async def backfill_children_product(db: AsyncSession, account_id: str, parent_external_id: str,
                                    product_id: str) -> None:
    """Fill product_id on operations that name this operation as their parent and are still
    unassigned. Same account only; the child's own external id is untouched."""
    await db.execute(
        update(MarketplaceOperation)
        .where(MarketplaceOperation.marketplace_account_id == account_id,
               MarketplaceOperation.source == "api",
               MarketplaceOperation.external_parent_id == parent_external_id,
               MarketplaceOperation.product_id.is_(None))
        .values(product_id=product_id))
