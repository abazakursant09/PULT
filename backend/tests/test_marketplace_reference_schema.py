"""
Marketplace category schema Reference tables (Phase C2b) — INERT GLOBAL Reference Data.

Proves the two new Reference tables (marketplace_category_rows, marketplace_category_attribute_rows)
create/register/roundtrip, are GLOBAL (Reference Data Doctrine — NO user_id column), are
versioned current-state (captured_at + version + source), support multiple immutable versions per
(marketplace, category_id) with deterministic latest-selection, and — critically — are wired to
NOTHING: no producer, not in the Advisory Runtime registry, not in the Decision Feed, SEO engine
untouched. Reference is NOT Evidence and does NOT call a marketplace API. Merges into CardSnapshot
at build time in a later slice (C2d).
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.marketplace_category import MarketplaceCategoryRow
from models.marketplace_category_attribute import MarketplaceCategoryAttributeRow


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── registration + shape ──────────────────────────────────────────────────────

def test_tables_registered_on_metadata():
    assert "marketplace_category_rows" in Base.metadata.tables
    assert "marketplace_category_attribute_rows" in Base.metadata.tables


def test_global_no_user_id_column():
    # Reference Data is marketplace-owned, not per-seller → NO user_id column exists
    cat_cols = {c.name for c in MarketplaceCategoryRow.__table__.columns}
    attr_cols = {c.name for c in MarketplaceCategoryAttributeRow.__table__.columns}
    assert "user_id" not in cat_cols
    assert "user_id" not in attr_cols


def test_versioned_current_state_fields_present():
    for M in (MarketplaceCategoryRow, MarketplaceCategoryAttributeRow):
        cols = {c.name for c in M.__table__.columns}
        assert "captured_at" in cols      # freshness
        assert "version" in cols          # replay pin
        assert "source" in cols           # provenance


# ── insert / query roundtrip ─────────────────────────────────────────────────

def test_category_roundtrip():
    async def go():
        db = await _db()
        db.add(MarketplaceCategoryRow(
            marketplace="wildberries", category_id="128", parent_id="1",
            name="Кремы для рук", path="Красота>Уход>Кремы для рук",
            captured_at=datetime(2026, 7, 1), version="2026-07-01", source="api_snapshot"))
        await db.commit()
        row = (await db.execute(select(MarketplaceCategoryRow).where(
            MarketplaceCategoryRow.category_id == "128"))).scalars().one()
        assert row.marketplace == "wildberries" and row.name == "Кремы для рук"
        assert row.version == "2026-07-01" and row.source == "api_snapshot"
    _run(go())


def test_attribute_roundtrip():
    async def go():
        db = await _db()
        import json
        db.add(MarketplaceCategoryAttributeRow(
            marketplace="ozon", category_id="17028922", attribute_id="8229",
            name="Тип", type="dictionary", is_required=True, is_filterable=True, is_variant=False,
            max_length=None, allowed_values_json=json.dumps(["крем", "гель"], ensure_ascii=False),
            captured_at=datetime(2026, 7, 1), version="v1", source="api_snapshot"))
        await db.commit()
        row = (await db.execute(select(MarketplaceCategoryAttributeRow).where(
            MarketplaceCategoryAttributeRow.attribute_id == "8229"))).scalars().one()
        assert row.is_required is True and row.is_filterable is True and row.is_variant is False
        assert json.loads(row.allowed_values_json) == ["крем", "гель"]
    _run(go())


# ── multiple immutable versions + deterministic latest-wins ──────────────────

def test_multiple_versions_latest_wins():
    async def go():
        db = await _db()
        for day, ver in [(1, "v1"), (10, "v2"), (20, "v3")]:
            db.add(MarketplaceCategoryRow(
                marketplace="wildberries", category_id="128", name=f"cat-{ver}",
                captured_at=datetime(2026, 7, day), version=ver, source="api_snapshot"))
        await db.commit()
        # all three immutable versions coexist
        allv = (await db.execute(select(MarketplaceCategoryRow).where(
            MarketplaceCategoryRow.category_id == "128"))).scalars().all()
        assert len(allv) == 3
        # deterministic latest-by-captured_at
        latest = (await db.execute(select(MarketplaceCategoryRow).where(
            MarketplaceCategoryRow.category_id == "128")
            .order_by(MarketplaceCategoryRow.captured_at.desc()).limit(1))).scalars().one()
        assert latest.version == "v3"
    _run(go())


# ── inert: no producer, not in registry, not in feed, SEO engine untouched ───

def test_no_reference_producer_in_registry():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    keys = {s.key for s in ADVISORY_PRODUCERS}
    assert "marketplace_category" not in keys and "reference" not in keys  # (seo added disabled in C3a)


def test_reference_not_in_decision_feed():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "marketplace_category_rows" not in tables
    assert "marketplace_category_attribute_rows" not in tables


def test_seo_engine_untouched_still_importable():
    # SEO engine remains the pure function it was — Reference is not wired into it here
    from services.seo.engine import evaluate_snapshot
    assert callable(evaluate_snapshot)


def test_all_eight_live_contours_untouched():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    for wired in ("revenue_signal", "money_leak_signal", "supply_signal", "rating_signal",
                  "review_velocity_signal", "overstock_signal", "price_erosion_signal",
                  "returns_signal"):
        assert wired in tables, f"missing {wired}"


# ── alembic single head ──────────────────────────────────────────────────────

def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["rcv1a2b3c4d01"], heads
