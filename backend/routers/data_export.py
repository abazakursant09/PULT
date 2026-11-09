"""Bounded, explicit-field export of the authenticated seller's imported rows.

Not a complete personal-data or advisory-result export. No raw files or credentials.
The response is constructed before delivery: limits never produce a partial download.
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow
from models.imported_return import ImportedReturnRow
from models.imported_card_content import ImportedCardContentRow
from models.decision import Decision
from models.seo_signal import SeoSignal
from models.advertising_signal import AdvertisingSignal
from models.review_signal import ReviewSignal
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal
from models.operations_signal import OperationsSignal
from models.pricing_signal import PricingSignal
from models.revenue_signal import RevenueSignal
from models.money_leak_signal import MoneyLeakSignal
from models.supply_signal import SupplySignal
from models.rating_signal import RatingSignal
from models.review_velocity_signal import ReviewVelocitySignal
from models.overstock_signal import OverstockSignal
from models.price_erosion_signal import PriceErosionSignal
from models.returns_signal import ReturnsSignal
from rate_limit import _limiter

router = APIRouter()
MAX_ROWS = 10_000
MAX_BYTES = 8 * 1024 * 1024
COMMON = ("marketplace", "marketplace_account_id", "source", "sku")
DATASETS = (
    ("products", ImportedProductRow, COMMON + (
        "marketplace_store_id", "title", "price", "stock", "rating", "reviews_count")),
    ("finance", ImportedFinanceRow, COMMON + (
        "marketplace_store_id", "date", "title", "revenue", "commission", "logistics",
        "ad_spend", "net_profit", "quantity")),
    ("returns", ImportedReturnRow, COMMON + (
        "marketplace_store_id", "date", "returns_qty", "return_amount", "reason")),
    ("card_content", ImportedCardContentRow, COMMON + (
        "date", "title", "description", "brand", "category", "characteristics_json",
        "image_count", "image_urls_json")),
)

SIGNAL_FIELDS = ("id", "marketplace", "sku", "signal_key", "what", "why", "meaning",
                 "what_to_do", "expected_effect", "status", "created_at")
ANALYSIS_DATASETS = (
    ("decisions", Decision, ("id", "problem", "cause", "effect", "action", "pnl_impact",
                            "pnl_level", "severity", "source", "status", "created_at", "resolved_at")),
    ("seo", SeoSignal, SIGNAL_FIELDS),
    ("advertising", AdvertisingSignal, SIGNAL_FIELDS),
    ("reviews", ReviewSignal, SIGNAL_FIELDS),
    ("growth", GrowthSignal, SIGNAL_FIELDS),
    ("legal", LegalSignal, SIGNAL_FIELDS),
    ("operations", OperationsSignal, SIGNAL_FIELDS),
    ("pricing", PricingSignal, SIGNAL_FIELDS),
    ("revenue", RevenueSignal, SIGNAL_FIELDS),
    ("money_leak", MoneyLeakSignal, SIGNAL_FIELDS),
    ("supply", SupplySignal, SIGNAL_FIELDS),
    ("rating", RatingSignal, SIGNAL_FIELDS),
    ("review_velocity", ReviewVelocitySignal, SIGNAL_FIELDS),
    ("overstock", OverstockSignal, SIGNAL_FIELDS),
    ("price_erosion", PriceErosionSignal, SIGNAL_FIELDS),
    ("returns", ReturnsSignal, SIGNAL_FIELDS),
)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("Unsupported export value")


@router.get("/data/export/imports")
async def export_imports(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _export_rows(db, user, DATASETS, "imported_rows", "pult-imported-data.json")


@router.get("/data/export/analysis")
async def export_analysis(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _export_rows(db, user, ANALYSIS_DATASETS, "saved_analysis", "pult-saved-analysis.json")


async def _export_rows(db, user, datasets, scope, filename):
    # Identity is verified by get_current_user, never taken from query/body/header claims.
    # Separate fixed server-selected archive scopes: saving one download can replay
    # its GET and must not prevent the other archive from being downloaded.
    await _limiter.hit(f"data-export:{user.id}:{scope}", limit=2, window_s=600)
    chunks = [('{"format_version":1,"scope":' + json.dumps(scope) + ',"datasets":{').encode()]
    size = len(chunks[0])
    count = 0

    def append(value: bytes):
        nonlocal size
        size += len(value)
        if size > MAX_BYTES:
            raise HTTPException(413, "Выгрузка превышает лимит размера; обратитесь в поддержку")
        chunks.append(value)

    for index, (name, model, fields) in enumerate(datasets):
        append(((',' if index else '') + json.dumps(name) + ':[').encode())
        statement = select(*(getattr(model, field) for field in fields)).where(
            model.user_id == str(user.id)
        ).order_by(model.id).limit(MAX_ROWS - count + 1)
        result = await db.stream(statement)
        first = True
        try:
            async for row in result.mappings():
                count += 1
                if count > MAX_ROWS:
                    raise HTTPException(413, "Слишком много строк для выгрузки; обратитесь в поддержку")
                try:
                    value = json.dumps(dict(row), ensure_ascii=False, allow_nan=False,
                                       default=_json_default).encode("utf-8")
                except ValueError:
                    raise HTTPException(409, "В данных обнаружено некорректное число") from None
                append((b'' if first else b',') + value)
                first = False
        finally:
            await result.close()
        append(b']')
    append(b'}}')
    return Response(b''.join(chunks), media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })
