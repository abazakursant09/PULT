"""
SEO Reference merge into CardSnapshot (Phase C2d) — reader-only, closes the constraints gap.

Merges UPLOAD Evidence (imported_card_content_rows) + GLOBAL Reference Data
(marketplace_category_rows / marketplace_category_attribute_rows) into the EXISTING CardSnapshot at
build time, per the Reference Data Doctrine. Proves: category_schema / expected_category_path /
constraints become available and constraint-dependent SEO rules now evaluate for WB/Ozon/Yandex;
honest degradation when the category is unresolved, the reference is absent, the reference is stale,
or the marketplace is unsupported (MegaMarket); the LATEST reference version is used and PINNED on
the snapshot; and the SEO engine / rules are untouched (diagnosis receives one enriched snapshot).
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
from models.marketplace_category import MarketplaceCategoryRow
from models.marketplace_category_attribute import MarketplaceCategoryAttributeRow

from services.seo.import_source import build_snapshot_from_import
from services.seo.card_snapshot import CardSnapshot
from services.seo.engine import evaluate_snapshot
from services.seo.evaluation import RuleResult
from services.seo.reference_source import (
    REFERENCE_STALENESS_DAYS, MARKETPLACE_TITLE_MAX_LEN, PULT_TITLE_MIN_LEN)

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
                description="Крем", image_count=5, day=10):
    db.add(ImportedCardContentRow(
        import_id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace, sku=sku,
        title="Крем для рук увлажняющий 75 мл", description=description, brand="AquaCare",
        category=category, characteristics_json=json.dumps({"Объём": "75 мл"}, ensure_ascii=False),
        image_count=image_count, image_urls_json=None, created_at=datetime(2026, 7, day)))
    await db.flush()


async def _reference(db, *, marketplace="wildberries", category_id="128", name="Красота",
                     path="Красота>Уход", captured=datetime(2026, 7, 1), version="v1"):
    db.add(MarketplaceCategoryRow(
        marketplace=marketplace, category_id=category_id, name=name, path=path,
        captured_at=captured, version=version, source="api_snapshot"))
    for aid, aname, req, filt, var in [
        ("1", "Состав", True, False, False),
        ("2", "Цвет", False, True, False),
        ("3", "Объём", False, False, True),
    ]:
        db.add(MarketplaceCategoryAttributeRow(
            marketplace=marketplace, category_id=category_id, attribute_id=aid, name=aname,
            type="text", is_required=req, is_filterable=filt, is_variant=var,
            captured_at=captured, version=version, source="api_snapshot"))
    await db.flush()


# ── A. snapshot enrichment ────────────────────────────────────────────────────

def test_snapshot_enriched_with_reference():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid); await _reference(db); await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                                sku="SKU1", now=NOW)
        assert isinstance(snap, CardSnapshot)
        fa = snap.field_availability
        assert fa["category_schema"] is True and fa["constraints"] is True
        assert fa["variants"] is True and fa["expected_category_path"] is True
        assert snap.category_schema.required_attributes == ("Состав",)
        assert snap.category_schema.filterable_attributes == ("Цвет",)
        assert snap.category_schema.variant_attributes == ("Объём",)
        assert snap.expected_category_path == ("Красота", "Уход")
        assert snap.variants == ("Объём",)                      # card's filled variant attr
        # SeoConstraints: marketplace title_max + PULT policy for the rest
        assert snap.constraints.title_max_len == MARKETPLACE_TITLE_MAX_LEN["wildberries"] == 60
        assert snap.constraints.title_min_len == PULT_TITLE_MIN_LEN
        assert snap.reference_version == "v1"                    # version pinned
    _run(go())


# ── B. constraint-dependent rules now evaluate ───────────────────────────────

def test_constraint_rules_now_evaluate():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid); await _reference(db); await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                                sku="SKU1", now=NOW)
        by_type = {r.problem_type: r.result for r in evaluate_snapshot(snap)}
        # previously not_evaluated (constraints/schema) — now they run
        for rule in ("required_attributes_missing", "filter_attributes_missing",
                     "variant_attributes_missing", "wrong_category_placement",
                     "title_too_short", "title_too_long", "description_too_short",
                     "content_completeness_low", "media_below_minimum", "attributes_incomplete"):
            assert by_type[rule] != RuleResult.NOT_EVALUATED, rule
        # required attribute "Состав" not filled → triggered; short description → triggered
        assert by_type["required_attributes_missing"] == RuleResult.TRIGGERED
        assert by_type["description_too_short"] == RuleResult.TRIGGERED
    _run(go())


# ── C. honest degradation ─────────────────────────────────────────────────────

def _constraint_rules_not_evaluated(snap):
    by_type = {r.problem_type: r.result for r in evaluate_snapshot(snap)}
    for rule in ("title_too_short", "description_too_short", "content_completeness_low",
                 "media_below_minimum", "required_attributes_missing"):
        assert by_type[rule] == RuleResult.NOT_EVALUATED, rule


def test_degrade_reference_absent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid); await db.commit()                 # no reference rows
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                                sku="SKU1", now=NOW)
        assert snap.constraints is None and snap.reference_version is None
        _constraint_rules_not_evaluated(snap)
    _run(go())


def test_degrade_category_unresolved():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, category="Неизвестная категория"); await _reference(db); await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                                sku="SKU1", now=NOW)
        assert snap.constraints is None
        _constraint_rules_not_evaluated(snap)
    _run(go())


def test_degrade_stale_reference():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # reference captured well beyond the staleness window
        await _card(db, uid); await _reference(db, captured=datetime(2026, 5, 1)); await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                                sku="SKU1", now=NOW)
        assert snap.constraints is None                          # stale → degrade
        _constraint_rules_not_evaluated(snap)
    _run(go())


def test_degrade_unsupported_marketplace():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, marketplace="megamarket")
        await _reference(db, marketplace="megamarket"); await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="megamarket",
                                                sku="SKU1", now=NOW)
        assert snap.constraints is None                          # no title limit for megamarket
        _constraint_rules_not_evaluated(snap)
    _run(go())


# ── D. version pinning: latest wins, pinned on snapshot ──────────────────────

def test_latest_reference_version_used_and_pinned():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid)
        await _reference(db, captured=datetime(2026, 7, 1), version="v1")
        await _reference(db, captured=datetime(2026, 7, 10), version="v2")   # newer
        await db.commit()
        snap = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                                sku="SKU1", now=NOW)
        assert snap.reference_version == "v2"                    # latest, old version ignored
    _run(go())


# ── F. doctrine guardrails ────────────────────────────────────────────────────

def test_no_producer_not_in_feed_and_head_unchanged():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    keys = {s.key for s in ADVISORY_PRODUCERS}
    # SEO is registered as a producer and ENABLED (Phase C3c — 9th live contour).
    assert "category_schema" not in keys
    seo_spec = next((s for s in ADVISORY_PRODUCERS if s.key == "seo"), None)
    assert seo_spec is not None and seo_spec.enabled is True
    tables = {t for (_c, _m, t) in _ENGINES}
    for missing in ("marketplace_category_rows", "marketplace_category_attribute_rows",
                    "imported_card_content_rows"):
        assert missing not in tables
    for wired in ("revenue_signal", "money_leak_signal", "supply_signal", "rating_signal",
                  "review_velocity_signal", "overstock_signal", "price_erosion_signal",
                  "returns_signal"):
        assert wired in tables
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["mts1a2b3c4d01"], heads


def test_staleness_constant_is_fixed():
    assert REFERENCE_STALENESS_DAYS == 30
