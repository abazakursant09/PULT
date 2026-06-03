"""
CSV Import router — /api/import/*
Flow: upload → parse → preview → confirm → persist
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from rate_limit import limit_import
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.import_record import ImportRecord
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.product import Product
from models.user import User
from services.product_resolver import build_product_index, resolve, resolution_key
from tasks.csv_parser import parse_csv, get_template

logger = logging.getLogger(__name__)
router = APIRouter()

# Temp upload dir — relative to this file's directory (backend/)
_UPLOAD_DIR = Path(__file__).parent.parent / "uploads" / "imports"
_MAX_FILE_BYTES = 10 * 1024 * 1024   # 10 MB


# ── Schemas ───────────────────────────────────────────────────────────────────

class PreviewResponse(BaseModel):
    import_id:          str
    marketplace:        Optional[str]
    import_type:        Optional[str]
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


class ConfirmResponse(BaseModel):
    imported_count: int
    skipped_count:  int
    import_id:      str


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
    file:         UploadFile = File(...),
    marketplace:  str        = Form(""),
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

    raw = await file.read()
    if len(raw) > _MAX_FILE_BYTES:
        raise HTTPException(400, f"Файл слишком большой. Максимум 10 МБ.")
    if not raw.strip():
        raise HTTPException(400, "Файл пустой.")

    # Parse
    mp  = marketplace.strip() or None
    itype = import_type.strip() or None
    result = parse_csv(raw, mp, itype)

    # Duplicate check — same file_hash + user + confirmed
    dup_id, dup_date = None, None
    dup = await db.execute(
        select(ImportRecord).where(
            ImportRecord.user_id   == str(user.id),
            ImportRecord.file_hash == result.file_hash,
            ImportRecord.status    == "confirmed",
        ).order_by(ImportRecord.confirmed_at.desc()).limit(1)
    )
    dup_rec = dup.scalar_one_or_none()
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
        marketplace = result.marketplace or mp or "unknown",
        import_type = result.import_type or itype or "unknown",
        status      = "failed" if result.errors else "pending",
        temp_path   = temp_path,
        total_rows  = result.total_rows,
        valid_rows  = result.valid_rows,
        skipped_rows = result.skipped_rows,
        warnings_json = json.dumps(result.warnings, ensure_ascii=False),
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)

    return PreviewResponse(
        import_id          = rec.id,
        marketplace        = result.marketplace,
        import_type        = result.import_type,
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

@router.post("/import/{import_id}/confirm", response_model=ConfirmResponse)
async def confirm_import(
    import_id: str,
    db:   AsyncSession = Depends(get_db),
    user: User         = Depends(get_current_user),
):
    rec_q = await db.execute(
        select(ImportRecord).where(
            ImportRecord.id      == import_id,
            ImportRecord.user_id == str(user.id),
        )
    )
    rec: Optional[ImportRecord] = rec_q.scalar_one_or_none()
    if not rec:
        raise HTTPException(404, "Запись импорта не найдена")
    if rec.status == "confirmed":
        raise HTTPException(400, "Этот импорт уже подтверждён")
    if rec.status == "failed":
        raise HTTPException(400, "Нельзя подтвердить импорт с ошибками")
    if not rec.temp_path or not os.path.exists(rec.temp_path):
        raise HTTPException(400, "Временный файл недоступен. Загрузите файл заново.")

    # Re-parse from temp file
    with open(rec.temp_path, "rb") as f:
        raw = f.read()
    result = parse_csv(raw, rec.marketplace, rec.import_type)

    if result.errors:
        raise HTTPException(422, f"Ошибки при повторном разборе: {'; '.join(result.errors)}")

    # Batch insert
    imported = 0
    failed   = 0
    skipped  = 0
    BATCH    = 200

    # ── Product Spine (Step 1): resolve every row → canonical Product.id ──
    # Finance rows: resolve only (no auto-create). Product rows: resolve, else
    # auto-create a Product from the catalog row. Empty sku → product_id stays None.
    _spine_res = await db.execute(
        select(Product).where(Product.user_id == str(user.id)).order_by(Product.created_at)
    )
    product_index = build_product_index(_spine_res.scalars().all())

    if rec.import_type == "finance":
        rows_to_insert = []
        for row in result.parsed_data:
            rows_to_insert.append(ImportedFinanceRow(
                import_id   = rec.id,
                user_id     = str(user.id),
                marketplace = rec.marketplace,
                date        = row.get("date"),
                sku         = row.get("sku", ""),
                title       = row.get("title", ""),
                revenue     = row.get("revenue", 0.0),
                commission  = row.get("commission", 0.0),
                logistics   = row.get("logistics", 0.0),
                ad_spend    = row.get("ad_spend", 0.0),
                net_profit  = row.get("net_profit", 0.0),
                quantity    = row.get("quantity", 0),
                product_id  = resolve(product_index, str(user.id), rec.marketplace, row.get("sku", "")),
            ))
            if len(rows_to_insert) >= BATCH:
                try:
                    async with db.begin_nested():
                        db.add_all(rows_to_insert)
                        await db.flush()
                    imported += len(rows_to_insert)
                except SQLAlchemyError as exc:
                    logger.warning("batch_insert_failed", extra={"rows": len(rows_to_insert), "error": str(exc)})
                    failed += len(rows_to_insert)
                rows_to_insert = []
        if rows_to_insert:
            try:
                async with db.begin_nested():
                    db.add_all(rows_to_insert)
                    await db.flush()
                imported += len(rows_to_insert)
            except SQLAlchemyError as exc:
                logger.warning("batch_insert_failed", extra={"rows": len(rows_to_insert), "error": str(exc)})
                failed += len(rows_to_insert)

    elif rec.import_type == "products":
        rows_to_insert = []
        for row in result.parsed_data:
            sku = row.get("sku", "")
            pid = resolve(product_index, str(user.id), rec.marketplace, sku)
            if pid is None:
                key = resolution_key(str(user.id), rec.marketplace, sku)
                if key is not None:                      # non-empty sku → auto-create Product
                    new_p = Product(
                        id          = str(uuid.uuid4()),   # set now: column default applies only at flush
                        user_id     = str(user.id),
                        name        = row.get("title") or sku,
                        marketplace = rec.marketplace,
                        sku         = sku,
                        price       = row.get("price"),
                    )
                    db.add(new_p)
                    product_index[key] = new_p.id        # reuse for later rows in this batch
                    pid = new_p.id
            rows_to_insert.append(ImportedProductRow(
                import_id     = rec.id,
                user_id       = str(user.id),
                marketplace   = rec.marketplace,
                sku           = sku,
                title         = row.get("title", ""),
                price         = row.get("price"),
                stock         = row.get("stock"),
                rating        = row.get("rating"),
                reviews_count = row.get("reviews_count"),
                product_id    = pid,
            ))
            if len(rows_to_insert) >= BATCH:
                try:
                    async with db.begin_nested():
                        db.add_all(rows_to_insert)
                        await db.flush()
                    imported += len(rows_to_insert)
                except SQLAlchemyError as exc:
                    logger.warning("batch_insert_failed", extra={"rows": len(rows_to_insert), "error": str(exc)})
                    failed += len(rows_to_insert)
                rows_to_insert = []
        if rows_to_insert:
            try:
                async with db.begin_nested():
                    db.add_all(rows_to_insert)
                    await db.flush()
                imported += len(rows_to_insert)
            except SQLAlchemyError as exc:
                logger.warning("batch_insert_failed", extra={"rows": len(rows_to_insert), "error": str(exc)})
                failed += len(rows_to_insert)

    skipped = result.skipped_rows

    # Update record — store temp_path before nulling for cleanup
    path_to_delete     = rec.temp_path
    rec.status         = "confirmed"
    rec.imported_count = imported
    rec.skipped_rows   = skipped
    rec.confirmed_at   = datetime.utcnow()
    rec.temp_path      = None
    await db.commit()

    # Delete temp file using path captured before nulling
    try:
        if path_to_delete:
            os.unlink(path_to_delete)
    except OSError:
        logger.warning("Could not delete temp file: %s", path_to_delete)

    # Best-effort cleanup of orphaned temp files older than 1h
    try:
        import time
        user_dir = _user_upload_dir(str(user.id))
        now = time.time()
        for f in user_dir.glob("*.csv"):
            if now - f.stat().st_mtime > 3600:
                f.unlink(missing_ok=True)
    except Exception:
        pass

    logger.info(
        "import_confirmed",
        extra={"import_id": rec.id, "imported": imported, "failed": failed, "skipped": skipped},
    )
    return ConfirmResponse(import_id=rec.id, imported_count=imported, skipped_count=skipped)


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
