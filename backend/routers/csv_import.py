"""
CSV Import router — /api/import/*
Flow: upload → parse → preview → confirm → persist
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from typing import Literal
from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request,
                     UploadFile)
from rate_limit import limit_import
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, false as sa_false, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.import_record import ImportRecord
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.imported_return import ImportedReturnRow
from models.imported_card_content import ImportedCardContentRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.user import User
from services.account_product_resolver import AMBIGUOUS, AccountProductIndex
from services.marketplace.identity_normalize import to_parser_code
from services.workspace_resolver import WorkspaceMissing, resolve_workspace_id
from services.advisory_runtime.after_import import run_producers_for_user
from tasks.csv_parser import parse_csv, get_template

logger = logging.getLogger(__name__)
router = APIRouter()

# Temp upload dir — relative to this file's directory (backend/)
_UPLOAD_DIR = Path(__file__).parent.parent / "uploads" / "imports"
_MAX_FILE_BYTES = 10 * 1024 * 1024   # 10 MB

# How long an uploaded file may sit unconfirmed. It exists only to carry a seller from preview to
# confirm — nothing reads it afterwards — so an hour is already generous for deciding whether to
# press the button. Named here rather than written twice: tasks/uploads_cleanup.py sweeps by the
# SAME number, and two retention periods that drifted apart would be a silent data-retention bug.
_ORPHAN_TTL_SECONDS = 3600


# ── Schemas ───────────────────────────────────────────────────────────────────

class PreviewResponse(BaseModel):
    import_id:          str
    marketplace_account_id: Optional[str]
    marketplace_store_id:   Optional[str]
    account_label:      Optional[str]
    store_label:        Optional[str]
    marketplace:        Optional[str]
    import_type:        Optional[str]
    # Advisory dry-run counts (PULT-LAUNCH-1.4.4). Preview writes nothing; confirm re-runs the
    # resolver inside the transaction, and confirm's result is the truth if state changed since.
    new_products:       int = 0
    updates:            int = 0
    conflicts:          int = 0
    unassigned:         int = 0
    rows_to_replace:    int = 0
    total_rows:         int
    valid_rows:         int
    skipped_rows:       int
    headers:            list[str]
    mapped_columns:     dict[str, str]
    unmapped_required:  list[str]
    preview_rows:       list[dict]
    warnings:           list[str]
    errors:             list[str]
    file_hash:          str
    duplicate_import_id: Optional[str]   # if same file was imported before
    duplicate_date:      Optional[str]


class ConfirmRequest(BaseModel):
    # "new" appends (the original behaviour). "overwrite" first drops the rows of any prior
    # confirmed import of the SAME file (same user + file_hash), so re-uploading a report
    # replaces the old copy instead of double-counting it. The UI offered "Перезаписать" for
    # a long time while the backend only ever appended — this makes the label true.
    mode: Literal["new", "overwrite"] = "new"


# import_type → the table its rows live in, for scope-correct overwrite deletes.
_ROW_MODEL = {
    "finance":      ImportedFinanceRow,
    "products":     ImportedProductRow,
    "returns":      ImportedReturnRow,
    "card_content": ImportedCardContentRow,
}


class ConfirmResponse(BaseModel):
    import_id:      str
    imported_count: int
    skipped_count:  int
    linked:         int = 0
    conflicts:      int = 0
    unassigned:     int = 0
    replaced:       int = 0
    failed:         int = 0


class ConflictCandidate(BaseModel):
    product_id: str
    sku:        Optional[str]
    name:       Optional[str]


class ConflictRow(BaseModel):
    row_type:    str
    row_id:      str
    sku:         Optional[str]
    title:       Optional[str]
    marketplace: Optional[str]
    marketplace_store_id: Optional[str]
    candidates:  list[ConflictCandidate]


class ResolveRequest(BaseModel):
    row_id:     str
    action:     Literal["link_existing", "create_new", "leave_unassigned"]
    product_id: Optional[str] = None      # required for link_existing; must be in the same account


class ResolveResponse(BaseModel):
    row_id:      str
    link_status: str
    product_id:  Optional[str]


class FinanceSummaryResponse(BaseModel):
    has_data:       bool
    row_count:      int
    total_revenue:  float
    total_profit:   float
    total_commission: float
    total_logistics:  float
    total_ad_spend:   float
    margin_percent:   float
    by_marketplace:   dict
    by_period:        list[dict]   # [{period, period_label, revenue, profit, commission, logistics, ad_spend, quantity}]
    by_product:       list[dict]   # top 15 [{sku, title, revenue, profit, margin, sales, marketplace}]
    last_import_date: Optional[str]


class ImportStatsResponse(BaseModel):
    has_finance:    bool
    has_products:   bool
    products_count: int
    revenue_total:  float
    last_import_date: Optional[str]


class ImportHistoryItem(BaseModel):
    id:            str
    filename:      str
    marketplace:   str
    import_type:   str
    status:        str
    total_rows:    int
    imported_count: int
    created_at:    str
    confirmed_at:  Optional[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_upload_dir(user_id: str) -> Path:
    d = _UPLOAD_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _workspace_id(db: AsyncSession, user: User) -> str:
    try:
        return await resolve_workspace_id(db, str(user.id))
    except WorkspaceMissing:
        logger.error("workspace missing for authenticated user")
        raise HTTPException(500, "Не удалось определить рабочее пространство")


async def _owned_active_store(db: AsyncSession, user: User, store_id: str):
    """Load the caller's ACTIVE store together with its cabinet, or raise.

    Ownership is Store -> Account -> Workspace. marketplace and account_id are READ from the
    DB here and never trusted from the client. A foreign or missing store returns the same 404.
    """
    workspace_id = await _workspace_id(db, user)
    hit = (await db.execute(
        select(MarketplaceStore, MarketplaceAccount)
        .join(MarketplaceAccount, MarketplaceAccount.id == MarketplaceStore.marketplace_account_id)
        .where(MarketplaceStore.id == store_id, MarketplaceAccount.workspace_id == workspace_id)
    )).first()
    if hit is None:
        raise HTTPException(404, "Магазин не найден")
    store, account = hit
    if store.status != "active":
        raise HTTPException(
            409, "Магазин архивирован — импорт недоступен. Восстановите магазин или выберите другой.")
    return store, account


def _month_label(period: str) -> str:
    months = ["Январь","Февраль","Март","Апрель","Май","Июнь",
              "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]
    try:
        parts = period.split("-")
        return f"{months[int(parts[1]) - 1]} {parts[0]}"
    except Exception:
        return period


# ── Upload + parse ────────────────────────────────────────────────────────────

@router.post("/import/upload", response_model=PreviewResponse)
async def upload_csv(
    request:      Request,
    file:         UploadFile = File(...),
    marketplace_store_id: str = Form(None),
    # Legacy Form field. Kept so the OLD request shape does not 400 at validation, but it is NOT
    # trusted: the marketplace and account are read from the chosen Store (see below). Form(None)
    # so the cheap size/empty guards still fire before store validation.
    marketplace:  str        = Form(None),
    import_type:  str        = Form(""),
    db:           AsyncSession = Depends(get_db),
    user:         User         = Depends(get_current_user),
    _rl:          None         = Depends(limit_import),
):
    # Validate mime / extension
    filename = (file.filename or "upload.csv").replace("/", "_").replace("\\", "_")
    if not filename.lower().endswith(".csv"):
        raise HTTPException(400, "Поддерживаются только CSV файлы (.csv)")
    if file.content_type and file.content_type not in (
        "text/csv", "application/csv", "text/plain",
        "application/vnd.ms-excel", "application/octet-stream",
    ):
        raise HTTPException(400, f"Неожиданный тип файла: {file.content_type}")

    # Cheap early reject on the declared body size. The header can lie or be absent, so it
    # is not the guarantee — just a way to refuse an obviously oversized upload before
    # touching the body.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_FILE_BYTES:
        raise HTTPException(400, "Файл слишком большой. Максимум 10 МБ.")

    # The guarantee: read AT MOST _MAX_FILE_BYTES + 1 into memory. `await file.read()` with no
    # bound loaded the whole upload first and checked the size after — a 2 GB file became 2 GB
    # of RAM before it was rejected, enough to OOM the single-worker backend. A bounded read
    # caps memory regardless of the real body size or a spoofed Content-Length: if we get more
    # than the limit back, the file is too big, full stop.
    raw = await file.read(_MAX_FILE_BYTES + 1)
    if len(raw) > _MAX_FILE_BYTES:
        raise HTTPException(400, "Файл слишком большой. Максимум 10 МБ.")
    if not raw.strip():
        raise HTTPException(400, "Файл пустой.")

    # Store binding (1.4.2): a CSV always lands in ONE chosen store. store_id is Form(None) so the
    # cheap size/empty guards above still fire first; here we require it and trust ONLY the DB for
    # the marketplace and account — never the client's `marketplace` form field.
    if not marketplace_store_id:
        raise HTTPException(422, "Выберите магазин перед загрузкой файла")
    store, account = await _owned_active_store(db, user, marketplace_store_id)

    # Parse — the parser speaks short codes, so convert the store's full marketplace at the boundary.
    itype = import_type.strip() or None
    parser_code = to_parser_code(store.marketplace)
    if parser_code is None:
        raise HTTPException(422, "Маркетплейс магазина не поддерживается")
    result = parse_csv(raw, parser_code, itype)

    eff_type = result.import_type or itype or "unknown"

    # Duplicate check — STORE-scoped (PULT-LAUNCH-1.4.4). The same file is a duplicate only within
    # the same account + store + import_type + source=csv; the same file in a different store or a
    # different account is NOT a duplicate. card_content is cabinet-level, so its scope omits the
    # store (account + import_type + source + file_hash).
    dup_id, dup_date = None, None
    dup_conds = [
        ImportRecord.file_hash == result.file_hash,
        ImportRecord.status == "confirmed",
        ImportRecord.marketplace_account_id == account.id,
        ImportRecord.import_type == eff_type,
        ImportRecord.source == "csv",
    ]
    if eff_type != "card_content":
        dup_conds.append(ImportRecord.marketplace_store_id == store.id)
    dup_rec = (await db.execute(
        select(ImportRecord).where(*dup_conds)
        .order_by(ImportRecord.confirmed_at.desc()).limit(1))).scalar_one_or_none()
    if dup_rec:
        dup_id   = dup_rec.id
        dup_date = dup_rec.confirmed_at.strftime("%d.%m.%Y") if dup_rec.confirmed_at else None

    # Save temp file
    temp_path = None
    if not result.errors:
        user_dir  = _user_upload_dir(str(user.id))
        temp_path = str(user_dir / f"{uuid.uuid4()}.csv")
        with open(temp_path, "wb") as f:
            f.write(raw)

    # Persist pending record
    rec = ImportRecord(
        user_id     = str(user.id),
        filename    = filename[:255],
        file_hash   = result.file_hash,
        marketplace = store.marketplace,                 # FULL identity name, trusted from the store
        import_type = eff_type,
        status      = "failed" if result.errors else "pending",
        temp_path   = temp_path,
        marketplace_account_id = account.id,
        marketplace_store_id   = store.id,
        source      = "csv",
        total_rows  = result.total_rows,
        valid_rows  = result.valid_rows,
        skipped_rows = result.skipped_rows,
        warnings_json = json.dumps(result.warnings, ensure_ascii=False),
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)

    # Advisory dry-run counts (read-only; nothing is created here).
    pv = ({"new_products": 0, "updates": 0, "conflicts": 0, "unassigned": 0, "rows_to_replace": 0}
          if result.errors else
          await _preview_counts(db, account.id, store.id, eff_type, result))

    return PreviewResponse(
        import_id          = rec.id,
        marketplace_account_id = account.id,
        marketplace_store_id   = store.id,
        account_label      = account.label,
        store_label        = store.label,
        marketplace        = store.marketplace,
        import_type        = result.import_type,
        new_products       = pv["new_products"],
        updates            = pv["updates"],
        conflicts          = pv["conflicts"],
        unassigned         = pv["unassigned"],
        rows_to_replace    = pv["rows_to_replace"],
        total_rows         = result.total_rows,
        valid_rows         = result.valid_rows,
        skipped_rows       = result.skipped_rows,
        headers            = result.headers,
        mapped_columns     = result.mapped_columns,
        unmapped_required  = result.unmapped_required,
        preview_rows       = result.preview_rows,
        warnings           = result.warnings,
        errors             = result.errors,
        file_hash          = result.file_hash,
        duplicate_import_id = dup_id,
        duplicate_date      = dup_date,
    )


# ── Confirm import ────────────────────────────────────────────────────────────

def _sku_present(value) -> bool:
    return bool((value or "").strip())


@dataclass
class _Counts:
    imported: int = 0
    linked: int = 0
    conflict: int = 0
    unassigned: int = 0
    replaced: int = 0


def _overwrite_conditions(import_type: str, account_id: str, store_id: str, result):
    """Exact overwrite scope for a re-upload (PULT-LAUNCH-1.4.4). Returns (row_model, conditions)
    or (None, None). Deletes ONLY csv metric rows in this cabinet — never a Product, a
    ProductPlacement, an api-sourced row, another store, or another period.

      finance / returns — account + store + source=csv + the dates present in the file
      products          — account + store + source=csv (a store snapshot; ImportedProductRow has
                          no period, so the whole store's csv snapshot is replaced)
      card_content      — account + source=csv + the SKUs present in the file (cabinet-level, no
                          store)
    """
    model = _ROW_MODEL.get(import_type)
    if model is None:
        return None, None
    conds = [model.marketplace_account_id == account_id, model.source == "csv"]
    if import_type in ("finance", "returns"):
        conds.append(model.marketplace_store_id == store_id)
        dates = sorted({r.get("date") for r in result.parsed_data if r.get("date")})
        conds.append(model.date.in_(dates) if dates else sa_false())
    elif import_type == "products":
        conds.append(model.marketplace_store_id == store_id)
    elif import_type == "card_content":
        skus = sorted({(r.get("sku") or "").strip() for r in result.parsed_data
                       if (r.get("sku") or "").strip()})
        conds.append(model.sku.in_(skus) if skus else sa_false())
    return model, conds


async def _preview_counts(db: AsyncSession, account_id: str, store_id: str,
                          import_type: str, result) -> dict:
    """READ-ONLY advisory counts for preview (PULT-LAUNCH-1.4.4): new_products / updates / conflicts
    / unassigned + rows_to_replace (the exact overwrite scope). Creates nothing; confirm re-runs the
    resolver in the transaction and confirm's result wins if state changed."""
    prods = (await db.execute(
        select(Product).where(Product.marketplace_account_id == account_id))).scalars().all()
    index = AccountProductIndex(prods, account_id)
    new_products = updates = conflicts = unassigned = 0
    for row in result.parsed_data:
        res = index.resolve(sku=row.get("sku", ""))
        if res.status == AMBIGUOUS:
            conflicts += 1
        elif res.product_id is not None:
            updates += 1
        elif import_type == "products" and _sku_present(row.get("sku", "")):
            new_products += 1
        else:
            unassigned += 1
    model, conds = _overwrite_conditions(import_type, account_id, store_id, result)
    rows_to_replace = 0
    if model is not None:
        rows_to_replace = (await db.execute(
            select(func.count()).select_from(model).where(*conds))).scalar() or 0
    return {"new_products": new_products, "updates": updates, "conflicts": conflicts,
            "unassigned": unassigned, "rows_to_replace": rows_to_replace}


async def _reconcile_placements(db: AsyncSession, present: set, existing: dict,
                                account_id: str, store_id: str, now: datetime) -> None:
    """Ensure a ProductPlacement exists for every product PRESENT in this store per this import.

    Idempotent: a product already placed keeps its first_seen_at and only bumps last_seen_at; a
    new one is created. A concurrent import creating the same (store, product) races on the DB
    UNIQUE — we swallow that IntegrityError (it now exists) rather than 500, and still bump
    last_seen_at. Cross-cabinet placement is impossible: the composite FK (1.3) rejects it.
    """
    prior = [pid for pid in present if pid in existing]
    for pid in [pid for pid in present if pid not in existing]:
        try:
            async with db.begin_nested():
                db.add(ProductPlacement(
                    id=str(uuid.uuid4()), product_id=pid, marketplace_store_id=store_id,
                    marketplace_account_id=account_id, source="csv", status="active",
                    first_seen_at=now, last_seen_at=now))
                await db.flush()
        except IntegrityError:
            prior.append(pid)     # another writer created it first — treat as existing
    if prior:
        await db.execute(
            update(ProductPlacement)
            .where(ProductPlacement.marketplace_store_id == store_id,
                   ProductPlacement.product_id.in_(prior))
            .values(last_seen_at=now))


async def _ensure_one_placement(db: AsyncSession, product_id: str, account_id: str,
                                store_id: str) -> None:
    """Create-or-touch a single ProductPlacement (used by conflict resolution). Idempotent; a race
    on the (store, product) UNIQUE is swallowed."""
    now = datetime.utcnow()
    exists = (await db.execute(
        select(ProductPlacement.id).where(
            ProductPlacement.marketplace_store_id == store_id,
            ProductPlacement.product_id == product_id))).scalar_one_or_none()
    if exists:
        await db.execute(update(ProductPlacement).where(ProductPlacement.id == exists)
                         .values(last_seen_at=now))
        return
    try:
        async with db.begin_nested():
            db.add(ProductPlacement(
                id=str(uuid.uuid4()), product_id=product_id, marketplace_store_id=store_id,
                marketplace_account_id=account_id, source="csv", status="active",
                first_seen_at=now, last_seen_at=now))
            await db.flush()
    except IntegrityError:
        pass


async def _persist_import_rows(db: AsyncSession, rec: ImportRecord, result, *, mode: str,
                               user: User) -> _Counts:
    """Insert the parsed rows store-aware with per-row conflict handling (PULT-LAUNCH-1.4.4).

    Every row lands in the ImportRecord's cabinet + store. Products are resolved WITHIN the account:
    a SKU that maps to >1 product is a CONFLICT — the row is saved with link_status='conflict' and
    product_id NULL (never auto-picked), while every safe row still imports. Only products-import
    creates a Product. card_content is cabinet-level (account_id, no store_id, no placement).
    """
    account_id = rec.marketplace_account_id
    store_id   = rec.marketplace_store_id
    now = datetime.utcnow()
    c = _Counts()

    # Safe overwrite: replace only this cabinet's csv rows in the exact scope (store/period/skus).
    if mode == "overwrite":
        model, conds = _overwrite_conditions(rec.import_type, account_id, store_id, result)
        if model is not None:
            # Serialize concurrent overwrite of the same scope: lock the store (card: the account).
            # A real row lock on PostgreSQL; a no-op on SQLite, which already serializes writers.
            if rec.import_type == "card_content":
                await db.execute(select(MarketplaceAccount.id)
                                 .where(MarketplaceAccount.id == account_id).with_for_update())
            else:
                await db.execute(select(MarketplaceStore.id)
                                 .where(MarketplaceStore.id == store_id).with_for_update())
            c.replaced = (await db.execute(
                select(func.count()).select_from(model).where(*conds))).scalar() or 0
            if c.replaced:
                await db.execute(delete(model).where(*conds))

    prods = (await db.execute(
        select(Product).where(Product.marketplace_account_id == account_id))).scalars().all()
    index = AccountProductIndex(prods, account_id)
    existing_pl = dict((await db.execute(
        select(ProductPlacement.product_id, ProductPlacement.id)
        .where(ProductPlacement.marketplace_store_id == store_id))).all())
    present: set = set()

    def _classify(sku, *, may_create=False, title=None, price=None):
        """Return (product_id, link_status). A row that resolves to >1 product is a conflict and is
        NEVER auto-picked; may_create=True (products only) creates a Product for a missing SKU."""
        res = index.resolve(sku=sku)
        if res.status == AMBIGUOUS:
            c.conflict += 1
            return None, "conflict"
        if res.product_id is not None:
            c.linked += 1
            present.add(res.product_id)
            return res.product_id, "linked"
        if may_create and _sku_present(sku):
            new_p = Product(id=str(uuid.uuid4()), user_id=str(user.id),
                            name=title or sku, marketplace=rec.marketplace, sku=sku, price=price,
                            marketplace_account_id=account_id, external_product_id=None)
            db.add(new_p)
            index.add(new_p)
            c.linked += 1
            present.add(new_p.id)
            return new_p.id, "linked"
        c.unassigned += 1
        return None, "unassigned"

    to_insert: list = []

    if rec.import_type == "finance":
        for row in result.parsed_data:
            pid, status = _classify(row.get("sku", ""))
            to_insert.append(ImportedFinanceRow(
                import_id=rec.id, user_id=str(user.id), marketplace=rec.marketplace,
                date=row.get("date"), sku=row.get("sku", ""), title=row.get("title", ""),
                revenue=row.get("revenue", 0.0), commission=row.get("commission", 0.0),
                logistics=row.get("logistics", 0.0), ad_spend=row.get("ad_spend", 0.0),
                net_profit=row.get("net_profit", 0.0), quantity=row.get("quantity", 0),
                product_id=pid, marketplace_account_id=account_id,
                marketplace_store_id=store_id, source="csv", fetched_at=now, link_status=status))

    elif rec.import_type == "products":
        for row in result.parsed_data:
            pid, status = _classify(row.get("sku", ""), may_create=True,
                                    title=row.get("title"), price=row.get("price"))
            to_insert.append(ImportedProductRow(
                import_id=rec.id, user_id=str(user.id), marketplace=rec.marketplace,
                sku=row.get("sku", ""), title=row.get("title", ""), price=row.get("price"),
                stock=row.get("stock"), rating=row.get("rating"),
                reviews_count=row.get("reviews_count"), product_id=pid,
                marketplace_account_id=account_id, marketplace_store_id=store_id,
                source="csv", fetched_at=now, link_status=status))

    elif rec.import_type == "returns":
        for row in result.parsed_data:
            pid, status = _classify(row.get("sku", ""))
            to_insert.append(ImportedReturnRow(
                import_id=rec.id, user_id=str(user.id), marketplace=rec.marketplace,
                date=row.get("date"), sku=row.get("sku", ""),
                returns_qty=row.get("returns_qty", 0), return_amount=row.get("return_amount", 0.0),
                reason=row.get("reason"), product_id=pid,
                marketplace_account_id=account_id, marketplace_store_id=store_id,
                source="csv", fetched_at=now, link_status=status))

    elif rec.import_type == "card_content":
        for row in result.parsed_data:
            pid, status = _classify(row.get("sku", ""))   # cabinet-level: no store, no placement
            to_insert.append(ImportedCardContentRow(
                import_id=rec.id, user_id=str(user.id), marketplace=rec.marketplace,
                date=row.get("date"), sku=row.get("sku", ""), title=row.get("title"),
                description=row.get("description"), brand=row.get("brand"),
                category=row.get("category"), characteristics_json=row.get("characteristics_json"),
                image_count=row.get("image_count"), image_urls_json=row.get("image_urls_json"),
                product_id=pid, marketplace_account_id=account_id, source="csv",
                fetched_at=now, link_status=status))

    # Atomic write: all rows in one flush. Any DB error propagates → confirm rolls back → failed.
    db.add_all(to_insert)
    await db.flush()
    c.imported = len(to_insert)

    await _reconcile_placements(db, present, existing_pl, account_id, store_id, now)
    return c


@router.post("/import/{import_id}/confirm", response_model=ConfirmResponse)
async def confirm_import(
    import_id: str,
    background_tasks: BackgroundTasks,
    body: ConfirmRequest = ConfirmRequest(),   # only `mode`; account_id/store_id are NEVER accepted
    db:   AsyncSession = Depends(get_db),
    user: User         = Depends(get_current_user),
):
    rec = (await db.execute(
        select(ImportRecord).where(
            ImportRecord.id == import_id, ImportRecord.user_id == str(user.id))
    )).scalar_one_or_none()
    if not rec:
        raise HTTPException(404, "Запись импорта не найдена")
    if rec.status == "confirmed":
        raise HTTPException(400, "Этот импорт уже подтверждён")
    if rec.status == "processing":
        raise HTTPException(409, "Импорт уже обрабатывается")
    if rec.status == "failed":
        raise HTTPException(400, "Импорт завершился ошибкой. Загрузите файл заново.")
    # Legacy record predating store binding (1.4.2): the store is unknown and must never be guessed.
    if not rec.marketplace_store_id:
        raise HTTPException(400, "Этот импорт создан до выбора магазина. Загрузите файл заново.")
    # Re-verify the store is still the caller's and active — it may have been archived since preview.
    # This also re-proves ownership from the DB, so a body/query cannot redirect the import.
    await _owned_active_store(db, user, rec.marketplace_store_id)
    if not rec.temp_path or not os.path.exists(rec.temp_path):
        raise HTTPException(400, "Временный файл недоступен. Загрузите файл заново.")

    # Atomically claim the record: only the request that flips pending -> processing may write rows.
    # A concurrent confirm sees rowcount 0 and is refused, so two confirms can never both insert.
    claim = await db.execute(
        update(ImportRecord)
        .where(ImportRecord.id == rec.id, ImportRecord.user_id == str(user.id),
               ImportRecord.status == "pending")
        .values(status="processing")
    )
    await db.commit()
    if claim.rowcount == 0:
        fresh = (await db.execute(
            select(ImportRecord.status).where(ImportRecord.id == rec.id))).scalar_one_or_none()
        if fresh == "confirmed":
            raise HTTPException(400, "Этот импорт уже подтверждён")
        if fresh == "processing":
            raise HTTPException(409, "Импорт уже обрабатывается")
        raise HTTPException(409, "Импорт нельзя подтвердить в текущем состоянии")

    # The record is now 'processing'. Everything below runs under a guard that leaves it 'failed'
    # on ANY error — it can never become 'confirmed' with a partial write.
    try:
        try:
            with open(rec.temp_path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            raise HTTPException(400, "Временный файл недоступен. Загрузите файл заново.")

        result = parse_csv(raw, to_parser_code(rec.marketplace), rec.import_type)
        if result.errors:
            raise HTTPException(422, f"Ошибки при повторном разборе: {'; '.join(result.errors)}")
        if result.valid_rows == 0:
            raise HTTPException(
                422,
                "В файле не распознано ни одной строки — импортировать нечего. "
                "Проверьте, что колонки заполнены, и загрузите файл заново.",
            )

        counts = await _persist_import_rows(db, rec, result, mode=body.mode, user=user)

        path_to_delete     = rec.temp_path
        rec.status         = "confirmed"      # conflicts do NOT fail the import; the file is processed
        rec.imported_count = counts.imported
        rec.skipped_rows   = result.skipped_rows
        rec.confirmed_at   = datetime.utcnow()
        rec.temp_path      = None
        await db.commit()
    except HTTPException:
        # rollback expires `rec`; use the path param (a plain str), never rec.id, to avoid a lazy
        # load outside the greenlet. Failed-status write is a separate transaction after rollback.
        await db.rollback()
        await db.execute(update(ImportRecord).where(ImportRecord.id == import_id).values(status="failed"))
        await db.commit()
        raise
    except Exception:
        await db.rollback()
        await db.execute(update(ImportRecord).where(ImportRecord.id == import_id).values(status="failed"))
        await db.commit()
        logger.exception("import_confirm_failed", extra={"import_id": import_id})
        raise HTTPException(500, "Не удалось завершить импорт")

    # ── post-commit best-effort side effects (a failure here does NOT unconfirm the import) ──
    try:
        if path_to_delete:
            os.unlink(path_to_delete)
    except OSError:
        logger.warning("Could not delete temp file: %s", path_to_delete)

    try:
        import time
        user_dir = _user_upload_dir(str(user.id))
        now = time.time()
        for f in user_dir.glob("*.csv"):
            if now - f.stat().st_mtime > _ORPHAN_TTL_SECONDS:
                f.unlink(missing_ok=True)
    except Exception:
        pass

    logger.info("import_confirmed",
                extra={"import_id": import_id, "imported": counts.imported,
                       "linked": counts.linked, "conflicts": counts.conflict,
                       "unassigned": counts.unassigned, "replaced": counts.replaced})

    if counts.imported > 0:
        background_tasks.add_task(run_producers_for_user, str(user.id))

    return ConfirmResponse(
        import_id=import_id, imported_count=counts.imported, skipped_count=result.skipped_rows,
        linked=counts.linked, conflicts=counts.conflict, unassigned=counts.unassigned,
        replaced=counts.replaced, failed=0)


# ── Conflicts: view & resolve ─────────────────────────────────────────────────

async def _owned_import(db: AsyncSession, import_id: str, user: User) -> ImportRecord:
    rec = (await db.execute(
        select(ImportRecord).where(
            ImportRecord.id == import_id, ImportRecord.user_id == str(user.id))
    )).scalar_one_or_none()
    if rec is None:
        raise HTTPException(404, "Запись импорта не найдена")
    return rec


@router.get("/import/{import_id}/conflicts", response_model=list[ConflictRow])
async def import_conflicts(
    import_id: str,
    db:   AsyncSession = Depends(get_db),
    user: User         = Depends(get_current_user),
):
    """Only THIS import's conflict rows, for the current user. Candidate products are recomputed
    within the account — never another cabinet's data, never secrets or the whole CSV."""
    rec = await _owned_import(db, import_id, user)
    model = _ROW_MODEL.get(rec.import_type)
    if model is None:
        return []
    rows = (await db.execute(
        select(model).where(model.import_id == import_id,
                            model.user_id == str(user.id),
                            model.link_status == "conflict"))).scalars().all()
    if not rows:
        return []
    prods = (await db.execute(
        select(Product).where(Product.marketplace_account_id == rec.marketplace_account_id))).scalars().all()
    index = AccountProductIndex(prods, rec.marketplace_account_id)
    meta = {p.id: (p.sku, p.name) for p in prods}
    out: list[ConflictRow] = []
    for r in rows:
        cands = [ConflictCandidate(product_id=pid, sku=meta.get(pid, (None, None))[0],
                                   name=meta.get(pid, (None, None))[1])
                 for pid in index.candidates(sku=r.sku)]
        out.append(ConflictRow(
            row_type=rec.import_type, row_id=r.id, sku=r.sku,
            title=getattr(r, "title", None), marketplace=rec.marketplace,
            marketplace_store_id=getattr(r, "marketplace_store_id", None), candidates=cands))
    return out


@router.post("/import/{import_id}/conflicts/resolve", response_model=ResolveResponse)
async def resolve_conflict(
    import_id: str,
    body: ResolveRequest,
    db:   AsyncSession = Depends(get_db),
    user: User         = Depends(get_current_user),
):
    """Seller resolves ONE conflict row: link an existing Product (same account only), create a new
    Product from the row's safe data, or leave it unassigned. Idempotent — an already-resolved row
    is returned unchanged. Other rows of the import are never rewritten."""
    rec = await _owned_import(db, import_id, user)
    model = _ROW_MODEL.get(rec.import_type)
    if model is None:
        raise HTTPException(400, "Импорт неизвестного типа")
    row = (await db.execute(
        select(model).where(model.id == body.row_id, model.import_id == import_id,
                            model.user_id == str(user.id)))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Строка не найдена")

    account_id = rec.marketplace_account_id
    store_id = getattr(row, "marketplace_store_id", None)

    # Idempotent: a row already resolved is returned as-is (no duplicate product, no re-link).
    if row.link_status != "conflict":
        return ResolveResponse(row_id=row.id, link_status=row.link_status, product_id=row.product_id)

    if body.action == "leave_unassigned":
        row.product_id = None
        row.link_status = "unassigned"

    elif body.action == "link_existing":
        if not body.product_id:
            raise HTTPException(422, "Не указан товар")
        prod = (await db.execute(
            select(Product).where(Product.id == body.product_id,
                                  Product.marketplace_account_id == account_id))).scalar_one_or_none()
        if prod is None:                                   # missing OR another cabinet's product
            raise HTTPException(404, "Товар не найден в этом кабинете")
        row.product_id = prod.id
        row.link_status = "linked"
        if store_id:
            await _ensure_one_placement(db, prod.id, account_id, store_id)

    elif body.action == "create_new":
        if not _sku_present(row.sku):
            raise HTTPException(422, "Недостаточно данных для создания товара")
        new_p = Product(id=str(uuid.uuid4()), user_id=str(user.id),
                        name=getattr(row, "title", None) or row.sku, marketplace=rec.marketplace,
                        sku=row.sku, price=getattr(row, "price", None),
                        marketplace_account_id=account_id, external_product_id=None)
        db.add(new_p)
        await db.flush()
        row.product_id = new_p.id
        row.link_status = "linked"
        if store_id:
            await _ensure_one_placement(db, new_p.id, account_id, store_id)

    await db.commit()
    return ResolveResponse(row_id=row.id, link_status=row.link_status, product_id=row.product_id)


# ── Finance summary ───────────────────────────────────────────────────────────

@router.get("/import/finance/summary", response_model=FinanceSummaryResponse)
async def finance_summary(
    db:   AsyncSession = Depends(get_db),
    user: User         = Depends(get_current_user),
):
    uid = str(user.id)

    # Total aggregates
    agg = await db.execute(
        select(
            func.sum(ImportedFinanceRow.revenue).label("revenue"),
            func.sum(ImportedFinanceRow.commission).label("commission"),
            func.sum(ImportedFinanceRow.logistics).label("logistics"),
            func.sum(ImportedFinanceRow.ad_spend).label("ad_spend"),
            func.sum(ImportedFinanceRow.net_profit).label("net_profit"),
            func.count(ImportedFinanceRow.id).label("row_count"),
        ).where(ImportedFinanceRow.user_id == uid)
    )
    totals = agg.one_or_none()

    if totals is None or not totals.row_count:
        return FinanceSummaryResponse(
            has_data=False, row_count=0,
            total_revenue=0, total_profit=0, total_commission=0,
            total_logistics=0, total_ad_spend=0, margin_percent=0,
            by_marketplace={}, by_period=[], by_product=[],
            last_import_date=None,
        )

    rev   = totals.revenue or 0.0
    prof  = totals.net_profit or 0.0
    margin = round(prof / rev * 100, 1) if rev > 0 else 0.0

    # By marketplace
    mp_agg = await db.execute(
        select(
            ImportedFinanceRow.marketplace,
            func.sum(ImportedFinanceRow.revenue).label("revenue"),
            func.sum(ImportedFinanceRow.net_profit).label("profit"),
            func.sum(ImportedFinanceRow.commission).label("commission"),
            func.sum(ImportedFinanceRow.logistics).label("logistics"),
            func.sum(ImportedFinanceRow.ad_spend).label("ad_spend"),
        ).where(ImportedFinanceRow.user_id == uid)
        .group_by(ImportedFinanceRow.marketplace)
    )
    by_mp: dict = {}
    for row in mp_agg:
        mp_rev = row.revenue or 0.0
        mp_prof = row.profit or 0.0
        by_mp[row.marketplace] = {
            "revenue":    round(mp_rev),
            "profit":     round(mp_prof),
            "commission": round(row.commission or 0.0),
            "logistics":  round(row.logistics or 0.0),
            "ad_spend":   round(row.ad_spend or 0.0),
            "margin":     round(mp_prof / mp_rev * 100, 1) if mp_rev > 0 else 0.0,
        }

    # By period (YYYY-MM)
    period_agg = await db.execute(
        select(
            func.substr(ImportedFinanceRow.date, 1, 7).label("period"),
            func.sum(ImportedFinanceRow.revenue).label("revenue"),
            func.sum(ImportedFinanceRow.net_profit).label("profit"),
            func.sum(ImportedFinanceRow.commission).label("commission"),
            func.sum(ImportedFinanceRow.logistics).label("logistics"),
            func.sum(ImportedFinanceRow.ad_spend).label("ad_spend"),
            func.sum(ImportedFinanceRow.quantity).label("quantity"),
        ).where(
            ImportedFinanceRow.user_id == uid,
            ImportedFinanceRow.date.isnot(None),
        )
        .group_by(func.substr(ImportedFinanceRow.date, 1, 7))
        .order_by(func.substr(ImportedFinanceRow.date, 1, 7))
    )
    by_period = []
    for row in period_agg:
        p_rev  = row.revenue  or 0.0
        p_prof = row.profit   or 0.0
        by_period.append({
            "period":       row.period or "unknown",
            "period_label": _month_label(row.period or ""),
            "revenue":      round(p_rev),
            "profit":       round(p_prof),
            "commission":   round(row.commission or 0.0),
            "logistics":    round(row.logistics  or 0.0),
            "ad_spend":     round(row.ad_spend   or 0.0),
            "quantity":     int(row.quantity or 0),
            "margin":       round(p_prof / p_rev * 100, 1) if p_rev > 0 else 0.0,
        })

    # By product (top 15)
    prod_agg = await db.execute(
        select(
            ImportedFinanceRow.sku,
            ImportedFinanceRow.title,
            ImportedFinanceRow.marketplace,
            func.sum(ImportedFinanceRow.revenue).label("revenue"),
            func.sum(ImportedFinanceRow.net_profit).label("profit"),
            func.sum(ImportedFinanceRow.quantity).label("sales"),
        ).where(ImportedFinanceRow.user_id == uid)
        .group_by(ImportedFinanceRow.sku, ImportedFinanceRow.marketplace)
        .order_by(func.sum(ImportedFinanceRow.revenue).desc())
        .limit(15)
    )
    by_product = []
    for row in prod_agg:
        pr_rev  = row.revenue or 0.0
        pr_prof = row.profit  or 0.0
        by_product.append({
            "sku":         row.sku or "",
            "title":       row.title or row.sku or "",
            "marketplace": row.marketplace,
            "revenue":     round(pr_rev),
            "profit":      round(pr_prof),
            "margin":      round(pr_prof / pr_rev * 100, 1) if pr_rev > 0 else 0.0,
            "sales":       int(row.sales or 0),
        })

    # Last import date
    last_rec = await db.execute(
        select(ImportRecord.confirmed_at)
        .where(ImportRecord.user_id == uid, ImportRecord.status == "confirmed")
        .order_by(ImportRecord.confirmed_at.desc()).limit(1)
    )
    last_row = last_rec.scalar_one_or_none()
    last_date = last_row.strftime("%d.%m.%Y") if last_row else None

    return FinanceSummaryResponse(
        has_data         = True,
        row_count        = totals.row_count,
        total_revenue    = round(rev),
        total_profit     = round(prof),
        total_commission = round(totals.commission or 0.0),
        total_logistics  = round(totals.logistics  or 0.0),
        total_ad_spend   = round(totals.ad_spend   or 0.0),
        margin_percent   = margin,
        by_marketplace   = by_mp,
        by_period        = by_period,
        by_product       = by_product,
        last_import_date = last_date,
    )


# ── Dashboard stats ───────────────────────────────────────────────────────────

@router.get("/import/stats", response_model=ImportStatsResponse)
async def import_stats(
    db:   AsyncSession = Depends(get_db),
    user: User         = Depends(get_current_user),
):
    uid = str(user.id)

    fin_count = await db.execute(
        select(func.count(ImportedFinanceRow.id)).where(ImportedFinanceRow.user_id == uid)
    )
    fin_rows = fin_count.scalar() or 0

    prod_count = await db.execute(
        select(func.count(ImportedProductRow.id)).where(ImportedProductRow.user_id == uid)
    )
    prod_rows = prod_count.scalar() or 0

    rev_agg = await db.execute(
        select(func.sum(ImportedFinanceRow.revenue))
        .where(ImportedFinanceRow.user_id == uid)
    )
    revenue = rev_agg.scalar() or 0.0

    last_rec = await db.execute(
        select(ImportRecord.confirmed_at)
        .where(ImportRecord.user_id == uid, ImportRecord.status == "confirmed")
        .order_by(ImportRecord.confirmed_at.desc()).limit(1)
    )
    last_row = last_rec.scalar_one_or_none()
    last_date = last_row.strftime("%d.%m.%Y") if last_row else None

    return ImportStatsResponse(
        has_finance    = fin_rows > 0,
        has_products   = prod_rows > 0,
        products_count = prod_rows,
        revenue_total  = round(revenue),
        last_import_date = last_date,
    )


# ── History ───────────────────────────────────────────────────────────────────

@router.get("/import/history", response_model=list[ImportHistoryItem])
async def import_history(
    db:   AsyncSession = Depends(get_db),
    user: User         = Depends(get_current_user),
):
    rows = await db.execute(
        select(ImportRecord)
        .where(ImportRecord.user_id == str(user.id))
        .order_by(ImportRecord.created_at.desc())
        .limit(50)
    )
    items = []
    for rec in rows.scalars():
        items.append(ImportHistoryItem(
            id            = rec.id,
            filename      = rec.filename,
            marketplace   = rec.marketplace,
            import_type   = rec.import_type,
            status        = rec.status,
            total_rows    = rec.total_rows,
            imported_count = rec.imported_count,
            created_at    = rec.created_at.strftime("%d.%m.%Y %H:%M"),
            confirmed_at  = rec.confirmed_at.strftime("%d.%m.%Y %H:%M") if rec.confirmed_at else None,
        ))
    return items


# ── Template download ─────────────────────────────────────────────────────────

@router.get("/import/templates/{marketplace}/{import_type}")
async def download_template(marketplace: str, import_type: str):
    content = get_template(marketplace.lower(), import_type.lower())
    if not content:
        raise HTTPException(404, "Шаблон не найден")
    filename = f"template_{marketplace}_{import_type}.csv"
    return Response(
        content       = content.encode("utf-8-sig"),
        media_type    = "text/csv",
        headers       = {"Content-Disposition": f'attachment; filename="{filename}"'},
    )
