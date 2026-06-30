"""
Advisory Runtime A12 — growth shadow validation.

Growth stays DISABLED. This validates the producer through the manual runner
(run_one) with the A11 data-derived thresholds: it creates growth_signal only, never
anything executable, and the scheduler still does NOT run it.
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
from models.imported_finance import ImportedFinanceRow
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.growth_signal import GrowthSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.advisory_runtime.registry import ADVISORY_PRODUCERS

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_growth_candidate(db, uid, *, sku="BIG", mp="ozon"):
    phys = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys, user_id=uid, title="t", cogs=10.0, cogs_source="manual"))
    db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace=mp, external_id=sku))
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date="2026-06-20", sku=sku, revenue=1000.0, net_profit=300.0,
                              ad_spend=0.0, quantity=10))
    await db.commit()


async def _growth_sigs(db, uid):
    return (await db.execute(select(GrowthSignal).where(
        GrowthSignal.user_id == uid, GrowthSignal.status.in_(("active", "reopened"))))).scalars().all()


# ── (1)(2)(3)(4)(5) run_one shadow — growth_signal only, nothing executable ───

def test_growth_shadow_run_one_advisory_only():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_growth_candidate(db, uid)
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="growth", now=NOW)
        assert isinstance(row, AdvisoryRun) and row.status == "ok"      # (1)(2)
        assert isinstance(json.loads(row.stats), dict)                  # opaque stats
        assert len(await _growth_sigs(db, uid)) >= 1                    # (3) growth_signal
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []  # (4)
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []  # (5)
    _run(go())


# ── (6) idempotent — reconcile keeps one live signal per insight_key ─────────

def test_growth_shadow_idempotent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_growth_candidate(db, uid)
        await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="growth", now=NOW)
        await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="growth", now=NOW)
        keys = [s.insight_key for s in await _growth_sigs(db, uid)]
        assert len(keys) == len(set(keys)), f"duplicate live growth signals: {keys}"
    _run(go())


# ── (7) growth still disabled ────────────────────────────────────────────────

def test_growth_still_disabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["growth"].enabled is False


# ── (8) scheduler does NOT run growth (only enabled producers) ───────────────

def test_scheduler_does_not_run_growth():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_growth_candidate(db, uid)   # active user (finance)
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "growth" not in keys             # growth disabled → never scheduled
    _run(go())


# ── (9) legal + review unchanged (still enabled) ─────────────────────────────

def test_legal_review_unchanged():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["legal"].enabled is True
    assert by_key["review"].enabled is True
