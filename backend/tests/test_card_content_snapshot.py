"""
Card-Content CardSnapshot source (Phase C1) — reader-only, no new diagnosis.

Proves build_snapshot_from_import turns UPLOAD Evidence (imported_card_content_rows, C0) into the
EXISTING CardSnapshot: content fields (title/description/brand/category_path/attributes/media)
become available, latest-by-date wins, marketplace+sku isolate. Critically — the honest gap holds:
category schema / required attributes / marketplace constraints stay unavailable (constraints=None,
field_availability False), so constraint-dependent SEO rules remain not_evaluated while
content-presence rules now evaluate for real.

Reader-only: no producer enable, no registry/scheduler/feed change, no marketplace API. Runs the
existing SEO engine (evaluate_snapshot) unchanged.
"""
import asyncio
import json
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_card_content import ImportedCardContentRow

from services.seo.import_source import build_snapshot_from_import
from services.seo.card_snapshot import CardSnapshot
from services.seo.adapter import SnapshotUnavailable
from services.seo.engine import evaluate_snapshot
from services.seo.evaluation import RuleResult


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _card(db, uid, *, marketplace="wildberries", sku="SKU1", title="Крем для рук",
                description="Увлажняющий крем 75 мл", brand="AquaCare", category="Красота>Уход",
                characteristics=None, image_count=5, day=20):
    chars = json.dumps(characteristics, ensure_ascii=False) if characteristics is not None else None
    db.add(ImportedCardContentRow(
        import_id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace, sku=sku,
        title=title, description=description, brand=brand, category=category,
        characteristics_json=chars, image_count=image_count, image_urls_json=None,
        created_at=datetime(2026, 6, day)))
    await db.flush()


# ── snapshot built from uploaded card row ────────────────────────────────────

def test_snapshot_built_from_upload():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, characteristics={"Объём": "75 мл", "Тип": "крем"}); await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries", sku="SKU1")
        assert isinstance(snap, CardSnapshot)
        assert snap.source == "import"
        assert snap.title == "Крем для рук" and snap.description == "Увлажняющий крем 75 мл"
        assert snap.brand == "AquaCare"
        assert snap.category_path == ("Красота", "Уход")
        assert snap.media.image_count == 5
        keys = {a.key: a for a in snap.attributes}
        assert keys["Объём"].value == "75 мл" and keys["Объём"].is_filled is True
    _run(go())


def test_no_card_row_returns_unavailable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        res = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries", sku="NOPE")
        assert isinstance(res, SnapshotUnavailable) and res.reason == "card_not_found"
    _run(go())


# ── latest-by-date wins ──────────────────────────────────────────────────────

def test_latest_by_date_wins():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, description="старое описание", image_count=2, day=1)
        await _card(db, uid, description="новое описание", image_count=7, day=20)
        await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries", sku="SKU1")
        assert snap.description == "новое описание" and snap.media.image_count == 7
    _run(go())


# ── marketplace + sku isolation ──────────────────────────────────────────────

def test_marketplace_and_sku_isolation():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, marketplace="wildberries", sku="A", brand="WB-Brand")
        await _card(db, uid, marketplace="ozon", sku="A", brand="OZ-Brand")
        await _card(db, uid, marketplace="wildberries", sku="B", brand="B-Brand")
        await db.commit()
        wb = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries", sku="A")
        oz = await build_snapshot_from_import(db, user_id=uid, marketplace="ozon", sku="A")
        assert wb.brand == "WB-Brand" and oz.brand == "OZ-Brand"
    _run(go())


# ── field availability: true for provided, false for missing ─────────────────

def test_field_availability_reflects_upload():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # description + attributes omitted; title/brand/category/image present
        await _card(db, uid, description=None, characteristics=None, image_count=None); await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries", sku="SKU1")
        fa = snap.field_availability
        assert fa["title"] is True and fa["brand"] is True and fa["category_path"] is True
        assert fa["description"] is False        # omitted → unavailable
        assert fa["attributes"] is False         # no characteristics → unavailable
        assert fa["media"] is False              # image_count None → unavailable
    _run(go())


# ── honest gap: constraints / category schema stay unavailable ───────────────

def test_constraints_and_schema_gap_remain_unavailable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, characteristics={"Объём": "75 мл"}); await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries", sku="SKU1")
        assert snap.constraints is None                       # never invented (API-Snapshot gap)
        fa = snap.field_availability
        assert fa["constraints"] is False
        assert fa["category_schema"] is False
        assert fa["expected_category_path"] is False
        assert fa["variants"] is False
    _run(go())


# ── SEO engine: content rules evaluate, constraint rules stay not_evaluated ──

def test_seo_engine_content_rules_run_constraint_rules_not_evaluated():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, description="Увлажняющий крем 75 мл",
                    characteristics={"Объём": "75 мл"}, image_count=5); await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries", sku="SKU1")
        by_type = {r.problem_type: r.result for r in evaluate_snapshot(snap)}

        # content-only rule (needs "description") RUNS now that upload provides it
        assert by_type["description_missing"] != RuleResult.NOT_EVALUATED

        # constraint-dependent rules stay NOT_EVALUATED (constraints=None, the API-Snapshot gap)
        for constraint_rule in ("title_too_short", "title_too_long", "description_too_short",
                                "content_completeness_low", "media_below_minimum"):
            assert by_type[constraint_rule] == RuleResult.NOT_EVALUATED

        # some rule ran AND some stayed not_evaluated — honest partial unlock
        results = set(by_type.values())
        assert RuleResult.NOT_EVALUATED in results
        assert results - {RuleResult.NOT_EVALUATED}
    _run(go())


# ── reader-only: no producer / not in registry / not in feed ─────────────────

def test_no_seo_producer_and_not_in_feed():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    keys = {s.key for s in ADVISORY_PRODUCERS}
    assert "card_content" not in keys  # (seo producer added disabled in C3a)
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "imported_card_content_rows" not in tables
    for wired in ("revenue_signal", "money_leak_signal", "supply_signal", "rating_signal",
                  "review_velocity_signal", "overstock_signal", "price_erosion_signal",
                  "returns_signal"):
        assert wired in tables, f"missing {wired}"


def test_alembic_head_unchanged():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["rwn1a2b3c4d01"], heads   # head advanced by marketplace category reference (C2b)
