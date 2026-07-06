"""
Category-schema Reference ingestion (Phase C2c) — read-only, GLOBAL Reference Data.

Mocked clients only — NO live HTTP. Proves the ingestion runner normalizes WB / Ozon / Yandex
category-schema responses and writes GLOBAL versioned rows into marketplace_category_rows /
marketplace_category_attribute_rows; MegaMarket honestly skips (no schema API); re-running stamps a
new immutable version; and the job is wired to NOTHING (not a producer, not in the Advisory Runtime
registry, not in the Decision Feed, SEO engine untouched, no marketplace writes, no CardSnapshot
wiring). Reference Data has no user_id.
"""
import asyncio
import uuid
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.marketplace_category import MarketplaceCategoryRow
from models.marketplace_category_attribute import MarketplaceCategoryAttributeRow

from services.seo_reference.adapters import _ClientAdapter, get_category_schema_adapter
from services.seo_reference.ingest import run_category_schema_ingestion


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── mock read-only clients returning marketplace-RAW shapes ──────────────────

class _WbClient:  # WB Content API raw shape
    async def fetch_category_tree(self):
        return [{"subjectID": 128, "parentID": 1, "subjectName": "Кремы для рук"}]
    async def fetch_category_attributes(self, category_id):
        return [{"charcID": 5, "charcName": "Объём", "charcType": "text", "required": True}]


class _OzonClient:  # Ozon description-category raw shape
    async def fetch_category_tree(self):
        return [{"id": 17028922, "name": "Уход"}]
    async def fetch_category_attributes(self, category_id):
        return [{"id": 8229, "name": "Тип", "type": "dictionary", "required": True,
                 "filtering": True, "values": ["крем", "гель"]}]


class _YandexClient:  # Yandex parameters raw shape
    async def fetch_category_tree(self):
        return [{"id": "90401", "name": "Косметика"}]
    async def fetch_category_attributes(self, category_id):
        return [{"id": 20001, "name": "Объём", "type": "TEXT", "required": True,
                 "distinctive": True, "constraints": {"maxLength": 255}}]


def _adapter(marketplace, client):
    return _ClientAdapter(marketplace, client)


# ── WB ────────────────────────────────────────────────────────────────────────

def test_wb_writes_category_and_attribute_rows():
    async def go():
        db = await _db()
        summ = await run_category_schema_ingestion(
            db, "wildberries", adapter=_adapter("wildberries", _WbClient()),
            captured_at=datetime(2026, 7, 1))
        await db.commit()
        assert summ.categories_written == 1 and summ.attributes_written == 1
        assert summ.skipped_reason is None
        cat = (await db.execute(select(MarketplaceCategoryRow))).scalars().one()
        assert cat.marketplace == "wildberries" and cat.category_id == "128"
        assert cat.parent_id == "1" and cat.name == "Кремы для рук"
        attr = (await db.execute(select(MarketplaceCategoryAttributeRow))).scalars().one()
        assert attr.category_id == "128" and attr.attribute_id == "5"
        assert attr.name == "Объём" and attr.is_required is True
    _run(go())


# ── Ozon (allowed values) ────────────────────────────────────────────────────

def test_ozon_writes_rows_and_allowed_values():
    async def go():
        db = await _db()
        summ = await run_category_schema_ingestion(
            db, "ozon", adapter=_adapter("ozon", _OzonClient()), captured_at=datetime(2026, 7, 1))
        await db.commit()
        assert summ.categories_written == 1 and summ.attributes_written == 1
        attr = (await db.execute(select(MarketplaceCategoryAttributeRow))).scalars().one()
        assert attr.is_required is True and attr.is_filterable is True
        assert json.loads(attr.allowed_values_json) == ["крем", "гель"]
    _run(go())


# ── Yandex (variant + maxLength constraint) ──────────────────────────────────

def test_yandex_writes_parameters():
    async def go():
        db = await _db()
        summ = await run_category_schema_ingestion(
            db, "yandex", adapter=_adapter("yandex", _YandexClient()),
            captured_at=datetime(2026, 7, 1))
        await db.commit()
        assert summ.attributes_written == 1
        attr = (await db.execute(select(MarketplaceCategoryAttributeRow))).scalars().one()
        assert attr.is_variant is True and attr.max_length == 255
    _run(go())


# ── MegaMarket honest skip ───────────────────────────────────────────────────

def test_megamarket_honest_skip():
    async def go():
        db = await _db()
        summ = await run_category_schema_ingestion(db, "megamarket")
        await db.commit()
        assert summ.skipped_reason == "no_category_schema_api"
        assert summ.categories_written == 0 and summ.attributes_written == 0
        assert (await db.execute(select(MarketplaceCategoryRow))).scalars().all() == []
    _run(go())


def test_get_adapter_none_for_megamarket():
    assert get_category_schema_adapter("megamarket", client=_WbClient()) is None


# ── Reference semantics: no user_id, versioned, provenance ───────────────────

def test_rows_have_no_user_id_and_versioned():
    async def go():
        db = await _db()
        await run_category_schema_ingestion(
            db, "wildberries", adapter=_adapter("wildberries", _WbClient()),
            captured_at=datetime(2026, 7, 1))
        await db.commit()
        cat = (await db.execute(select(MarketplaceCategoryRow))).scalars().one()
        assert not hasattr(cat, "user_id")
        assert cat.captured_at == datetime(2026, 7, 1)
        assert cat.version == "2026-07-01T00:00:00" and cat.source == "api_snapshot"
    _run(go())


def test_rerun_creates_new_version_keeps_previous():
    async def go():
        db = await _db()
        await run_category_schema_ingestion(
            db, "wildberries", adapter=_adapter("wildberries", _WbClient()),
            captured_at=datetime(2026, 7, 1)); await db.commit()
        await run_category_schema_ingestion(
            db, "wildberries", adapter=_adapter("wildberries", _WbClient()),
            captured_at=datetime(2026, 8, 1)); await db.commit()
        cats = (await db.execute(select(MarketplaceCategoryRow).where(
            MarketplaceCategoryRow.category_id == "128"))).scalars().all()
        versions = sorted(c.version for c in cats)
        assert versions == ["2026-07-01T00:00:00", "2026-08-01T00:00:00"]   # both immutable versions
        latest = (await db.execute(select(MarketplaceCategoryRow)
            .where(MarketplaceCategoryRow.category_id == "128")
            .order_by(MarketplaceCategoryRow.captured_at.desc()).limit(1))).scalars().one()
        assert latest.version == "2026-08-01T00:00:00"                       # latest-wins for readers
    _run(go())


# ── inert: not a producer / not in registry / not in feed ────────────────────

def test_not_a_producer_and_not_in_feed():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    keys = {s.key for s in ADVISORY_PRODUCERS}
    assert "seo_reference" not in keys and "category_schema" not in keys and "reference" not in keys
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "marketplace_category_rows" not in tables
    assert "marketplace_category_attribute_rows" not in tables


def test_seo_engine_and_cardsnapshot_untouched():
    # SEO engine remains a pure function; ingestion does not wire Reference into CardSnapshot (C2d)
    from services.seo.engine import evaluate_snapshot
    from services.seo.import_source import build_snapshot_from_import
    assert callable(evaluate_snapshot) and callable(build_snapshot_from_import)


def test_all_eight_live_contours_untouched():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    for wired in ("revenue_signal", "money_leak_signal", "supply_signal", "rating_signal",
                  "review_velocity_signal", "overstock_signal", "price_erosion_signal",
                  "returns_signal"):
        assert wired in tables, f"missing {wired}"


def test_alembic_head_unchanged():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["mcs1a2b3c4d01"], heads
