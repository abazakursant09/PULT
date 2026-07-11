"""
Card-Content Import Foundation (Phase C0) — ingestion only, UPLOAD Evidence for SEO.

Proves the fourth import_type ("card_content") parses WB / Ozon / Yandex card exports into rows
that persist into ImportedCardContentRow, that existing finance/products/returns imports are
unaffected, that alembic has a single head, and — critically — that card_content is INGESTION
ONLY: no producer, not in the Advisory Runtime registry, not in the Decision Feed. The advisory
layer stays DB-headless (file-upload path only, no marketplace API). Per the Evidence Source
Doctrine this is UPLOAD Evidence; category schema / constraints remain a separate API-Snapshot gap.
"""
import asyncio
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_card_content import ImportedCardContentRow

from tasks.csv_parser import parse_csv, get_template


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


WB_CARD = (
    "Артикул WB,Название,Описание,Бренд,Категория,Характеристики,Количество фото,Ссылки на фото\n"
    "12345678,Крем для рук,Увлажняющий крем 75 мл,AquaCare,Красота,\"Объём: 75 мл; Тип: крем\",5,\"https://a.jpg https://b.jpg\"\n"
).encode("utf-8")

OZON_CARD = (
    "SKU Ozon,Название,Описание,Бренд,Категория,Характеристики,Количество фото\n"
    "987654321,Крем для рук,Питательный крем,AquaCare,Уход,\"Объём: 75 мл\",4\n"
).encode("utf-8")

YM_CARD = (
    "Артикул Яндекс,Название,Описание,Бренд,Категория,Характеристики,Количество фото\n"
    "offer-001,Крем для рук,Крем для сухой кожи,AquaCare,Косметика,\"Объём: 75 мл\",3\n"
).encode("utf-8")


# ── parser detection ─────────────────────────────────────────────────────────

def test_parse_wb_card_content():
    r = parse_csv(WB_CARD)
    assert r.import_type == "card_content"
    assert r.marketplace == "wb"
    assert r.valid_rows == 1 and not r.errors
    row = r.parsed_data[0]
    assert row["sku"] == "12345678"
    assert row["title"] == "Крем для рук"
    assert row["description"] == "Увлажняющий крем 75 мл"
    assert row["brand"] == "AquaCare"
    assert row["category"] == "Красота"
    assert row["image_count"] == 5
    # characteristics captured as JSON string
    assert json.loads(row["characteristics_json"]) == {"raw": "Объём: 75 мл; Тип: крем"}
    # image urls captured as JSON list
    assert json.loads(row["image_urls_json"]) == ["https://a.jpg", "https://b.jpg"]


def test_parse_ozon_card_content():
    r = parse_csv(OZON_CARD)
    assert r.import_type == "card_content" and r.marketplace == "ozon"
    assert r.valid_rows == 1
    row = r.parsed_data[0]
    assert row["sku"] == "987654321" and row["image_count"] == 4
    assert row["image_urls_json"] is None


def test_parse_ym_card_content():
    r = parse_csv(YM_CARD)
    assert r.import_type == "card_content"
    assert r.valid_rows == 1 and r.parsed_data[0]["sku"] == "offer-001"


def test_explicit_import_type_honored():
    r = parse_csv(WB_CARD, import_type="card_content")
    assert r.import_type == "card_content" and r.valid_rows == 1


def test_card_content_templates_exist():
    for mp in ("wb", "ozon", "ym"):
        assert get_template(mp, "card_content") is not None


def test_missing_sku_skipped():
    bad = ("Артикул WB,Название,Описание,Характеристики\n"
           ",Крем,Описание,Объём: 75\n").encode("utf-8")
    r = parse_csv(bad, import_type="card_content")
    assert r.skipped_rows == 1 and r.valid_rows == 0


def test_characteristics_accepts_json():
    js = ("Артикул WB,Название,Описание,Характеристики,Количество фото\n"
          "111,Товар,Описание товара,\"{\"\"Объём\"\":\"\"75 мл\"\"}\",2\n").encode("utf-8")
    r = parse_csv(js, import_type="card_content")
    assert json.loads(r.parsed_data[0]["characteristics_json"]) == {"Объём": "75 мл"}


# ── existing imports unaffected (regression) ─────────────────────────────────

def test_finance_still_parses():
    r = parse_csv(get_template("wb", "finance").encode("utf-8"))
    assert r.import_type == "finance" and r.valid_rows >= 1


def test_products_still_parses():
    r = parse_csv(get_template("wb", "products").encode("utf-8"))
    assert r.import_type == "products" and r.valid_rows >= 1


def test_returns_still_parses():
    r = parse_csv(get_template("wb", "returns").encode("utf-8"))
    assert r.import_type == "returns" and r.valid_rows >= 1


# ── persist roundtrip: parsed card content → ImportedCardContentRow ──────────

def test_persist_roundtrip_creates_card_rows():
    async def go():
        db = await _db(); uid = str(uuid.uuid4()); iid = str(uuid.uuid4())
        r = parse_csv(WB_CARD)
        for row in r.parsed_data:
            db.add(ImportedCardContentRow(
                import_id=iid, user_id=uid, marketplace="wb",
                date=row.get("date"), sku=row.get("sku", ""),
                title=row.get("title"), description=row.get("description"),
                brand=row.get("brand"), category=row.get("category"),
                characteristics_json=row.get("characteristics_json"),
                image_count=row.get("image_count"),
                image_urls_json=row.get("image_urls_json"), product_id=None))
        await db.commit()
        rows = (await db.execute(select(ImportedCardContentRow).where(
            ImportedCardContentRow.user_id == uid))).scalars().all()
        assert len(rows) == 1
        card = rows[0]
        assert card.sku == "12345678" and card.brand == "AquaCare"
        assert card.image_count == 5 and card.description == "Увлажняющий крем 75 мл"
        assert json.loads(card.image_urls_json) == ["https://a.jpg", "https://b.jpg"]
    _run(go())


def test_table_registered_on_metadata():
    assert "imported_card_content_rows" in Base.metadata.tables


# ── alembic single head ──────────────────────────────────────────────────────

def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["cri1a2b3c4d01"], heads   # head advanced by marketplace category reference (C2b)


# ── ingestion only: no producer, not in registry, not in feed ────────────────

def test_no_card_content_producer_in_registry():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    keys = {s.key for s in ADVISORY_PRODUCERS}
    assert "card_content" not in keys and "card" not in keys  # (seo producer added disabled in C3a)


def test_card_content_not_in_decision_feed():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "imported_card_content_rows" not in tables
    contours = {c for (c, _m, _t) in _ENGINES}
    assert "card_content" not in contours


def test_all_eight_live_contours_untouched():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    for wired in ("revenue_signal", "money_leak_signal", "supply_signal", "rating_signal",
                  "review_velocity_signal", "overstock_signal", "price_erosion_signal",
                  "returns_signal"):
        assert wired in tables, f"missing {wired}"
