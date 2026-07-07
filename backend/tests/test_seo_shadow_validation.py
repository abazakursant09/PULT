"""
SEO shadow validation (Phase C3b; SEO ENABLED in C3c) — proves the SEO producer is advisory-safe
and correct. Advisory-only behavior holds identically whether scheduled or run directly.

Runs run_seo_producer directly over seeded data (shadow) and asserts:
  1. advisory-safe — writes seo_signal ONLY; creates NO Decision / EngineSignalDecisionLink /
     DecisionApplyIntent / ExecutionLog (no executor, no marketplace write).
  2. coverage/degradation matrix — fresh reference → constraint signals may fire; no / stale /
     unresolved reference and MegaMarket → constraint rules not_evaluated → NO fabricated
     constraint signals; no card → skip.
  3. reconciliation/idempotency — repeated runs keep one live seo_signal per insight_key.
  4. reference replay pin — changed reference_version changes snapshot_hash → re-audit.
  5. determinism — same fixture + same now → same signals.
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
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.decision_apply_intent import DecisionApplyIntent
from models.execution_log import ExecutionLog

from services.advisory_runtime.registry import ADVISORY_PRODUCERS
from services.advisory_runtime.producers import run_seo_producer
from services.advisory_runtime.runtime import RuntimeContext
from services.seo.import_source import build_snapshot_from_import
from services.seo.audit_persist import snapshot_hash

NOW = datetime(2026, 7, 15)
_CONSTRAINT_PROBLEMS = ("title_too_short", "title_too_long", "description_too_short",
                        "media_below_minimum", "content_completeness_low",
                        "required_attributes_missing", "filter_attributes_missing",
                        "variant_attributes_missing", "wrong_category_placement",
                        "attributes_incomplete")


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


async def _signals(db, uid):
    return (await db.execute(select(SeoSignal).where(SeoSignal.user_id == uid))).scalars().all()


async def _live(db, uid):
    return [s for s in await _signals(db, uid) if s.status in ("active", "reopened")]


# ── C3c: SEO ENABLED — registered, enabled, in the scheduled set ─────────────

def test_seo_enabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["seo"].enabled is True
    assert "seo" in {s.key for s in ADVISORY_PRODUCERS if s.enabled}


# ── 1. advisory-safe: seo_signal ONLY, nothing executable ────────────────────

def test_shadow_writes_signal_only_nothing_executable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, image_count=0); await _reference(db); await db.commit()
        await run_seo_producer(_ctx(db, uid)); await db.commit()
        assert await _signals(db, uid), "expected seo_signal rows"
        # NOTHING executable created
        assert (await db.execute(select(Decision))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink))).scalars().all() == []
        assert (await db.execute(select(DecisionApplyIntent))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


def test_shadow_signals_have_no_executor_binding():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid); await _reference(db); await db.commit()
        await run_seo_producer(_ctx(db, uid)); await db.commit()
        for s in await _signals(db, uid):
            assert s.decision_id is None            # never promoted/bound in a shadow run
    _run(go())


# ── 2. coverage/degradation matrix ───────────────────────────────────────────

def _assert_no_constraint_signals(sigs):
    ptypes = {s.problem_type for s in sigs}
    for cp in _CONSTRAINT_PROBLEMS:
        assert cp not in ptypes, cp


def test_card_plus_fresh_reference_may_produce_constraint_signals():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, image_count=0); await _reference(db); await db.commit()
        res = await run_seo_producer(_ctx(db, uid)); await db.commit()
        assert res.stats["audits_created"] == 1
        ptypes = {s.problem_type for s in await _signals(db, uid)}
        # required attr Состав unfilled + image_count 0 < media_min → constraint problems fired
        assert ptypes & set(_CONSTRAINT_PROBLEMS), "expected some constraint signal with fresh ref"
    _run(go())


def test_card_no_reference_no_constraint_signals():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, description="", image_count=0); await db.commit()  # no reference
        res = await run_seo_producer(_ctx(db, uid)); await db.commit()
        assert res.stats["audits_created"] == 1
        _assert_no_constraint_signals(await _signals(db, uid))
    _run(go())


def test_card_stale_reference_no_constraint_signals():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, description="", image_count=0)
        await _reference(db, captured=datetime(2026, 5, 1)); await db.commit()   # >30d stale
        await run_seo_producer(_ctx(db, uid)); await db.commit()
        _assert_no_constraint_signals(await _signals(db, uid))
    _run(go())


def test_card_unresolved_category_no_constraint_signals():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, category="Неизвестная", description="", image_count=0)
        await _reference(db); await db.commit()                                   # category mismatch
        await run_seo_producer(_ctx(db, uid)); await db.commit()
        _assert_no_constraint_signals(await _signals(db, uid))
    _run(go())


def test_no_card_honest_skip():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        res = await run_seo_producer(_ctx(db, uid)); await db.commit()
        assert res.stats["cards_seen"] == 0 and res.stats["audits_created"] == 0
        assert await _signals(db, uid) == []
    _run(go())


def test_megamarket_honest_degradation():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, marketplace="megamarket", description="", image_count=0)
        await _reference(db, marketplace="megamarket"); await db.commit()
        res = await run_seo_producer(_ctx(db, uid)); await db.commit()
        assert res.stats["audits_created"] == 1                 # audited, but no constraints
        _assert_no_constraint_signals(await _signals(db, uid))
    _run(go())


# ── 3. reconciliation / idempotency ──────────────────────────────────────────

def test_repeated_run_one_live_signal_per_insight_key():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, image_count=0); await _reference(db); await db.commit()
        await run_seo_producer(_ctx(db, uid)); await db.commit()
        await run_seo_producer(_ctx(db, uid)); await db.commit()   # re-run, same card+ref
        await run_seo_producer(_ctx(db, uid)); await db.commit()
        live = await _live(db, uid)
        keys = [s.insight_key for s in live]
        assert len(keys) == len(set(keys)), f"duplicate live signals: {keys}"
    _run(go())


# ── 4. reference replay pin ───────────────────────────────────────────────────

def test_reference_version_change_forces_reaudit():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid); await _reference(db, version="v1"); await db.commit()
        s1 = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                              sku="SKU1", now=NOW)
        h1 = snapshot_hash(s1)
        await _reference(db, version="v2", captured=datetime(2026, 7, 10)); await db.commit()
        s2 = await build_snapshot_from_import(db, user_id=uid, marketplace="wildberries",
                                              sku="SKU1", now=NOW)
        assert s2.reference_version == "v2"
        assert snapshot_hash(s2) != h1        # changed version → different hash → re-audit
    _run(go())


# ── 5. determinism ────────────────────────────────────────────────────────────

def test_determinism_same_fixture_same_signals():
    async def build_and_run():
        db = await _db(); uid = str(uuid.uuid4())
        await _card(db, uid, image_count=0); await _reference(db); await db.commit()
        await run_seo_producer(_ctx(db, uid)); await db.commit()
        sigs = await _signals(db, uid)
        return sorted((s.problem_type, s.priority_level, s.status) for s in sigs)
    a = _run(build_and_run())
    b = _run(build_and_run())
    assert a == b and a, "shadow run must be deterministic and non-empty"
