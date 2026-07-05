"""
Returns Import Foundation (Phase R0) — ingestion only, no diagnosis.

Proves the third import_type ("returns") parses WB / Ozon returns exports into rows that persist
into ImportedReturnRow, that existing finance/products imports are unaffected, that alembic has a
single head, and — critically — that returns is INGESTION ONLY: no producer, not in the Advisory
Runtime registry, not in the Decision Feed. The advisory layer stays DB-headless (file-upload
path only, no marketplace API call).
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_return import ImportedReturnRow

from tasks.csv_parser import parse_csv, get_template


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


WB_RETURNS = (
    "Дата возврата,Артикул WB,Количество возвратов,Сумма возврата,Причина возврата\n"
    "05.01.2025,12345678,3,1497,Не подошёл размер\n"
    "07.01.2025,87654321,1,299,Брак\n"
).encode("utf-8")

OZON_RETURNS = (
    "Дата возврата,SKU Ozon,Количество возвратов,Сумма возврата,Причина возврата\n"
    "05.01.2025,987654321,2,998,Не подошёл товар\n"
).encode("utf-8")


# ── parser: WB returns ───────────────────────────────────────────────────────

def test_parse_wb_returns():
    r = parse_csv(WB_RETURNS)
    assert r.import_type == "returns"
    assert r.marketplace == "wb"
    assert r.valid_rows == 2 and not r.errors
    first = r.parsed_data[0]
    assert first["sku"] == "12345678"
    assert first["date"] == "2025-01-05"
    assert first["returns_qty"] == 3
    assert first["return_amount"] == 1497.0
    assert first["reason"] == "Не подошёл размер"


# ── parser: Ozon returns ─────────────────────────────────────────────────────

def test_parse_ozon_returns():
    r = parse_csv(OZON_RETURNS)
    assert r.import_type == "returns"
    assert r.marketplace == "ozon"
    assert r.valid_rows == 1 and not r.errors
    row = r.parsed_data[0]
    assert row["sku"] == "987654321" and row["returns_qty"] == 2
    assert row["return_amount"] == 998.0 and row["reason"] == "Не подошёл товар"


def test_returns_explicit_import_type_forces_returns():
    # even if auto-detect were ambiguous, an explicit import_type is honored
    r = parse_csv(WB_RETURNS, import_type="returns")
    assert r.import_type == "returns" and r.valid_rows == 2


def test_returns_templates_exist():
    for mp in ("wb", "ozon", "ym"):
        assert get_template(mp, "returns") is not None


def test_returns_row_missing_sku_skipped():
    bad = ("Дата возврата,Артикул WB,Количество возвратов,Сумма возврата\n"
           "05.01.2025,,3,1497\n").encode("utf-8")
    r = parse_csv(bad, import_type="returns")
    assert r.skipped_rows == 1 and r.valid_rows == 0


# ── existing imports unaffected (regression) ─────────────────────────────────

def test_finance_import_still_parses():
    r = parse_csv(get_template("wb", "finance").encode("utf-8"))
    assert r.import_type == "finance" and r.valid_rows >= 1


def test_products_import_still_parses():
    r = parse_csv(get_template("wb", "products").encode("utf-8"))
    assert r.import_type == "products" and r.valid_rows >= 1


# ── persist roundtrip: parsed returns → ImportedReturnRow ─────────────────────

def test_persist_roundtrip_creates_return_rows():
    async def go():
        db = await _db(); uid = str(uuid.uuid4()); iid = str(uuid.uuid4())
        r = parse_csv(WB_RETURNS)
        # persist exactly as the confirm/persist path does (resolve → product_id None here)
        for row in r.parsed_data:
            db.add(ImportedReturnRow(
                import_id=iid, user_id=uid, marketplace="wb",
                date=row.get("date"), sku=row.get("sku", ""),
                returns_qty=row.get("returns_qty", 0),
                return_amount=row.get("return_amount", 0.0),
                reason=row.get("reason"), product_id=None))
        await db.commit()
        rows = (await db.execute(select(ImportedReturnRow).where(
            ImportedReturnRow.user_id == uid))).scalars().all()
        assert len(rows) == 2
        by_sku = {r.sku: r for r in rows}
        assert by_sku["12345678"].returns_qty == 3
        assert by_sku["12345678"].return_amount == 1497.0
        assert by_sku["87654321"].reason == "Брак"
    _run(go())


def test_table_registered_on_metadata():
    assert "imported_return_rows" in Base.metadata.tables


# ── alembic single head ──────────────────────────────────────────────────────

def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["ret1a2b3c4d01"], heads


# ── ingestion only: no producer, not in registry, not in feed ────────────────

def test_no_returns_producer_in_registry():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    keys = {s.key for s in ADVISORY_PRODUCERS}
    assert "returns" not in keys and "return" not in keys


def test_returns_not_in_decision_feed():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "imported_return_rows" not in tables
    assert "returns_signal" not in tables
    contours = {c for (c, _m, _t) in _ENGINES}
    assert "returns" not in contours


def test_all_seven_live_contours_still_wired():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    for wired in ("revenue_signal", "money_leak_signal", "supply_signal", "rating_signal",
                  "review_velocity_signal", "overstock_signal", "price_erosion_signal"):
        assert wired in tables, f"missing {wired}"
