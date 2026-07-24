"""Read-only store catalog (PULT-LAUNCH-1.4.5B).

Answers the two questions a seller asks about ONE store:
  * GET /api/marketplace-stores/{store_id}/products — what is in this store
  * GET /api/marketplace-stores/{store_id}/imports  — what was loaded into this store

Both are pure reads: nothing here creates a Product or a ProductPlacement, and nothing
mutates an import. Three rules shape the code:

  * Ownership is Store -> Account -> Workspace, never the client's word. A store that does
    not exist and a store belonging to another workspace return the SAME 404, so the reply
    can never be used to probe for ids.
  * An ARCHIVED store stays readable. Archiving is a soft state (PULT-LAUNCH-1.4.1) — the
    history must not disappear. Only the import path refuses an archived store, and that
    refusal lives in csv_import, not here.
  * Conflict / unassigned counts are derived from the import's own rows, because
    ImportRecord has no such columns. They are aggregated per page in one grouped query per
    import_type, so a page of N imports never turns into N queries.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.import_record import ImportRecord
from models.imported_card_content import ImportedCardContentRow
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.imported_return import ImportedReturnRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.user import User
from schemas.store_catalog import (
    StoreImportItem, StoreImportsPage, StoreProductItem, StoreProductsPage, StoreRef,
)
from services.workspace_resolver import WorkspaceMissing, resolve_workspace_id

import logging

log = logging.getLogger(__name__)
router = APIRouter()

# A page is capped so one request can never ask the DB for the whole catalog.
_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 25

# import_type -> the table holding that import's rows. Mirrors csv_import._ROW_MODEL; a test
# pins the two together so a new import type cannot silently lose its conflict counts.
_ROW_MODEL = {
    "finance":      ImportedFinanceRow,
    "products":     ImportedProductRow,
    "returns":      ImportedReturnRow,
    "card_content": ImportedCardContentRow,
}

_COUNTED_STATUSES = ("conflict", "unassigned")


async def _workspace_id(db: AsyncSession, user: User) -> str:
    try:
        return await resolve_workspace_id(db, str(user.id))
    except WorkspaceMissing:
        log.error("workspace missing for authenticated user")
        raise HTTPException(500, "Не удалось определить рабочее пространство")


async def _owned_store(db: AsyncSession, user: User, store_id: str) -> MarketplaceStore:
    """The caller's store, archived or not, or the same 404 used for a foreign store."""
    workspace_id = await _workspace_id(db, user)
    store = (await db.execute(
        select(MarketplaceStore)
        .join(MarketplaceAccount, MarketplaceAccount.id == MarketplaceStore.marketplace_account_id)
        .where(MarketplaceStore.id == store_id,
               MarketplaceAccount.workspace_id == workspace_id)
    )).scalars().first()
    if store is None:
        raise HTTPException(404, "Магазин не найден")
    return store


def _pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size if total else 0


def _store_ref(store: MarketplaceStore) -> StoreRef:
    return StoreRef(id=store.id, label=store.label,
                    marketplace=store.marketplace, status=store.status)


# ── Products of one store ─────────────────────────────────────────────────────
@router.get("/marketplace-stores/{store_id}/products", response_model=StoreProductsPage)
async def store_products(
    store_id: str,
    page:      int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    search:    Optional[str] = Query(None, max_length=120),
    status:    Optional[str] = Query(None),
    db:   AsyncSession = Depends(get_db),
    user: User         = Depends(get_current_user),
):
    """Products present in THIS store, through their placements.

    The join is on (product_id, marketplace_account_id) — the same composite key the DB's
    foreign keys use — so a product of another cabinet cannot be reached even if ids were
    guessed. UNIQUE(marketplace_store_id, product_id) means one placement per product per
    store, so the join cannot duplicate a row.

    A product with no sales and no metrics is still returned: presence in the store is the
    fact being reported, not performance.
    """
    store = await _owned_store(db, user, store_id)

    conditions = [
        ProductPlacement.marketplace_store_id == store.id,
        # Redundant with the composite FK, but it keeps account isolation explicit in the
        # query itself rather than relying on the constraint alone.
        ProductPlacement.marketplace_account_id == store.marketplace_account_id,
    ]
    if status:
        conditions.append(ProductPlacement.status == status)
    if search and search.strip():
        # Only fields a seller actually knows: their SKU and the product name.
        # ilike is case-insensitive for ASCII on SQLite (its lower() does not fold Cyrillic),
        # and fully case-insensitive on Postgres. No full-text index is introduced here.
        like = f"%{search.strip()}%"
        conditions.append(or_(Product.sku.ilike(like), Product.name.ilike(like)))

    join_on = and_(
        ProductPlacement.product_id == Product.id,
        ProductPlacement.marketplace_account_id == Product.marketplace_account_id,
    )

    total = (await db.execute(
        select(func.count())
        .select_from(ProductPlacement)
        .join(Product, join_on)
        .where(*conditions)
    )).scalar_one()

    rows = []
    if total:
        rows = (await db.execute(
            select(Product, ProductPlacement)
            .join(ProductPlacement, join_on)
            .where(*conditions)
            # name then id: a deterministic order, so page 2 can never repeat or skip a row
            # that page 1 already showed.
            .order_by(Product.name.asc(), Product.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).all()

    return StoreProductsPage(
        store=_store_ref(store),
        items=[
            StoreProductItem(
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                placement_status=placement.status,
                placement_source=placement.source,
                first_seen_at=placement.first_seen_at,
                last_seen_at=placement.last_seen_at,
            )
            for product, placement in rows
        ],
        page=page, page_size=page_size, total=total, pages=_pages(total, page_size),
    )


# ── Import history of one store ───────────────────────────────────────────────
@router.get("/marketplace-stores/{store_id}/imports", response_model=StoreImportsPage)
async def store_imports(
    store_id: str,
    page:        int = Query(1, ge=1),
    page_size:   int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    status:      Optional[str] = Query(None),
    import_type: Optional[str] = Query(None),
    db:   AsyncSession = Depends(get_db),
    user: User         = Depends(get_current_user),
):
    """Every upload bound to THIS store, newest first.

    Scoped by marketplace_store_id alone — the store was already proven to belong to the
    caller's workspace, and an import of another store (or another cabinet) simply does not
    carry this store's id. Only fields that exist are returned: no temp path, no file hash,
    no credentials.
    """
    store = await _owned_store(db, user, store_id)

    conditions = [ImportRecord.marketplace_store_id == store.id]
    if status:
        conditions.append(ImportRecord.status == status)
    if import_type:
        conditions.append(ImportRecord.import_type == import_type)

    total = (await db.execute(
        select(func.count()).select_from(ImportRecord).where(*conditions)
    )).scalar_one()

    records: list[ImportRecord] = []
    if total:
        records = list((await db.execute(
            select(ImportRecord)
            .where(*conditions)
            # created_at can tie on a fast double upload; id breaks the tie so paging is stable.
            .order_by(ImportRecord.created_at.desc(), ImportRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).scalars().all())

    counts = await _row_counts(db, records)

    return StoreImportsPage(
        store=_store_ref(store),
        items=[
            StoreImportItem(
                import_id=rec.id,
                filename=rec.filename,
                import_type=rec.import_type,
                status=rec.status,
                created_at=rec.created_at,
                confirmed_at=rec.confirmed_at,
                total_rows=rec.total_rows,
                imported_count=rec.imported_count,
                skipped_rows=rec.skipped_rows,
                conflicts=counts.get(rec.id, {}).get("conflict", 0),
                unassigned=counts.get(rec.id, {}).get("unassigned", 0),
                has_unresolved_conflicts=counts.get(rec.id, {}).get("conflict", 0) > 0,
                source=rec.source,
            )
            for rec in records
        ],
        page=page, page_size=page_size, total=total, pages=_pages(total, page_size),
    )


async def _row_counts(db: AsyncSession, records: list[ImportRecord]) -> dict[str, dict[str, int]]:
    """conflict / unassigned counts for a whole page of imports.

    One grouped query per import_type present on the page (at most four), never one per
    import — the history of a store with many uploads must not degrade into N+1.
    """
    if not records:
        return {}
    by_type: dict[str, list[str]] = {}
    for rec in records:
        if rec.import_type in _ROW_MODEL:
            by_type.setdefault(rec.import_type, []).append(rec.id)

    out: dict[str, dict[str, int]] = {}
    for import_type, ids in by_type.items():
        model = _ROW_MODEL[import_type]
        grouped = (await db.execute(
            select(model.import_id, model.link_status, func.count())
            .where(model.import_id.in_(ids), model.link_status.in_(_COUNTED_STATUSES))
            .group_by(model.import_id, model.link_status)
        )).all()
        for import_id, link_status, count in grouped:
            out.setdefault(import_id, {})[link_status] = count
    return out
