"""
SEO advisory producer adapter (Phase C3a; ENABLED Phase C3c).

Proves the SEO producer is registered and ENABLED (scheduled, 9th live contour); a run
over a seeded uploaded card + Reference produces seo_signal through the EXISTING audit_and_persist
path; honest skips (no card) and honest degradation (absent reference → constraint rules
not_evaluated → no fabricated constraint signals); the reference_version pin is visible in
snapshot_hash behavior; the prior live contours stay untouched; alembic head unchanged.
"""
import asyncio
import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_card_content import ImportedCardContentRow
from models.marketplace_category import MarketplaceCategoryRow
from models.marketplace_category_attribute import MarketplaceCategoryAttributeRow
from models.seo_signal import SeoSignal

from services.advisory_runtime.registry import ADVISORY_PRODUCERS
from services.advisory_runtime.producers import run_seo_producer
from services.advisory_runtime.runtime import RuntimeContext
from services.seo.import_source import build_snapshot_from_import
from services.seo.audit_persist import snapshot_hash
from services.seo.card_snapshot import CardSnapshot

NOW = datetime(2026, 7, 15)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _card(db, uid, *, marketplace="wildberries", sku="SKU1", category="Красота",
                description="Крем", image_count=0, day=10):
    db.add(ImportedCardContentRow(
        import_id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace, sku=sku,
        title="Крем", description=description, brand="AquaCare", category=category,
        characteristics_json=json.dumps({"Объём": "75 мл"}, ensure_ascii=False),
        image_count=image_count, image_urls_json=None, created_at=datetime(2026, 7, day)))
    await db.flush()


async def _reference(db, *, marketplace="wildberries", category_id="128", name="Красота",
                     path="Красота>Уход", captured=datetime(2026, 7, 1), version="v1"):
    db.add(MarketplaceCategoryRow(
        marketplace=marketplace, category_id=category_id, name=name, path=path,
        captured_at=captured, version=version, source="api_snapshot"))
    db.add(MarketplaceCategoryAttributeRow(
        marketplace=marketplace, category_id=category_id, attribute_id="1", name="Состав",
        type="text", is_required=True, is_filterable=False, is_variant=False,
        captured_at=captured, version=version, source="api_snapshot"))
    await db.flush()


def _ctx(db, uid):
    return RuntimeContext(db=db, user_id=uid, now=NOW, run_id="r1", logger=None,
                          triggered_by="shadow")


# ── registry: registered and ENABLED, scheduled (Phase C3c) ──────────────────

def test_seo_registered_and_enabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "seo" in by_key                      # present
    assert by_key["seo"].enabled is True        # ENABLED (Phase C3c — 9th live contour)


def test_seo_in_enabled_set():
    enabled = {s.key for s in ADVISORY_PRODUCERS if s.enabled}
    assert "seo" in enabled                     # now scheduled
    # the prior live Business-Diagnosis + support contours remain enabled, untouched
    for live in ("growth", "legal", "review", "advertising", "revenue_diagnosis",
                 "money_leak", "supply", "rating", "review_velocity", "overstock",
                 "price_erosion", "returns"):
        assert live in enabled


# ── shadow run produces seo_signal via existing path ─────────────────────────

def test_shadow_run_produces_seo_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, image_count=0); await _reference(db); await db.commit()
        res = await run_seo_producer(_ctx(db, uid))
        await db.commit()
        assert res.ok and res.stats["cards_seen"] == 1 and res.stats["audits_created"] == 1
        sigs = (await db.execute(select(SeoSignal).where(SeoSignal.user_id == uid))).scalars().all()
        assert sigs, "expected at least one seo_signal"
        # required attribute Состав unfilled + reference present → a schema/constraint problem fired
        assert any(s.status in ("active", "reopened") for s in sigs)
    _run(go())


def test_no_card_content_honest_skip():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        res = await run_seo_producer(_ctx(db, uid))    # no uploaded cards
        await db.commit()
        assert res.ok and res.stats["cards_seen"] == 0 and res.stats["audits_created"] == 0
        assert (await db.execute(select(SeoSignal))).scalars().all() == []
    _run(go())


def test_absent_reference_degrades_no_constraint_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # card present, NO reference → constraint rules degrade to not_evaluated
        await _card(db, uid, description="", image_count=0); await db.commit()
        res = await run_seo_producer(_ctx(db, uid))
        await db.commit()
        assert res.stats["audits_created"] == 1
        sigs = (await db.execute(select(SeoSignal).where(SeoSignal.user_id == uid))).scalars().all()
        ptypes = {s.problem_type for s in sigs}
        # constraint-dependent problems must NOT be fabricated without reference
        for constraint_problem in ("title_too_short", "title_too_long", "description_too_short",
                                   "media_below_minimum", "content_completeness_low",
                                   "required_attributes_missing"):
            assert constraint_problem not in ptypes
    _run(go())


# ── R5: reference_version pin visible in snapshot_hash ────────────────────────

def test_reference_version_changes_snapshot_hash():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid); await _reference(db, version="v1"); await db.commit()
        snap_v1 = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                                   sku="SKU1", now=NOW)
        h_v1 = snapshot_hash(snap_v1)
        h_v1_again = snapshot_hash(snap_v1)
        assert h_v1 == h_v1_again                       # same card + same version → same hash
        # a different reference version (same category values) → different hash
        await _reference(db, version="v2", captured=datetime(2026, 7, 10)); await db.commit()
        snap_v2 = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                                   sku="SKU1", now=NOW)
        assert snap_v2.reference_version == "v2"
        assert snapshot_hash(snap_v2) != h_v1           # changed version → fresh audit
    _run(go())


# ── guardrails ────────────────────────────────────────────────────────────────

def test_all_eight_live_contours_untouched():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    for wired in ("revenue_signal", "money_leak_signal", "supply_signal", "rating_signal",
                  "review_velocity_signal", "overstock_signal", "price_erosion_signal",
                  "returns_signal"):
        assert wired in tables


def test_alembic_head_unchanged():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["atl1a2b3c4d01"], heads
