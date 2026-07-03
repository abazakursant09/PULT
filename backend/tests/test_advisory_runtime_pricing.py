"""
Advisory Runtime Phase 1.3 — pricing producer (DISABLED + shadow validation).

Adds a thin adapter over the EXISTING pricing generator (build_pricing_snapshot → rules
→ reconcile) with an adapter-owned threshold source (derive_pricing_thresholds) mirroring
growth A11 / advertising Phase 1.1. Pricing has no existing production writer (dormant
contour), so this producer will become the sole writer of pricing_signal — shipped
DISABLED, exercised only via run_one(). Proves: threshold derivation + honest-absent,
real pricing_signal through the runtime, advisory-only (0 Decision / 0 link / 0 executor),
DB-headless, idempotent reconcile, scheduler skips it while disabled.
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
from models.pricing_signal import PricingSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun
from models.execution_log import ExecutionLog

from services.pricing.threshold_source import derive_pricing_thresholds
from services.pricing.rules import PricingThresholds
from services.advisory_runtime.runtime import AdvisoryRuntime, RuntimeContext
from services.advisory_runtime.producers import run_pricing_producer
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


def _ctx(db, uid):
    import logging
    return RuntimeContext(db=db, user_id=uid, now=NOW, run_id=str(uuid.uuid4()),
                          logger=logging.getLogger("test.pricing"), triggered_by="manual")


async def _fin(db, uid, *, sku, revenue, net_profit, mp="ozon"):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date="2026-06-20", sku=sku, revenue=revenue, net_profit=net_profit,
                              ad_spend=0.0, quantity=10))
    await db.flush()


async def _live(db, uid):
    return (await db.execute(select(PricingSignal).where(
        PricingSignal.user_id == uid,
        PricingSignal.status.in_(("active", "reopened", "promoted_to_decision"))))).scalars().all()


# ── (1) threshold derivation with sufficient seller data ─────────────────────

def test_thresholds_derived_from_finance():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="A", revenue=10000.0, net_profit=-500.0)
        await _fin(db, uid, sku="B", revenue=2000.0, net_profit=100.0)
        await db.commit()
        th = await derive_pricing_thresholds(db, uid, now=NOW)
        assert isinstance(th, PricingThresholds)
        assert th.min_revenue_for_pricing_signal == 6000.0     # median(10000, 2000)
        assert th.target_margin_pct is None                    # no canonical source
    _run(go())


# ── (2) honest-absent with no finance ────────────────────────────────────────

def test_thresholds_none_without_finance():
    async def go():
        db = await _db()
        assert await derive_pricing_thresholds(db, str(uuid.uuid4()), now=NOW) is None
    _run(go())


# ── (3) producer emits pricing_signal through the runtime (run_one) ──────────

def test_producer_emits_signal_via_run_one():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="LOSS", revenue=10000.0, net_profit=-500.0)
        await db.commit()
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="pricing", now=NOW)
        assert isinstance(row, AdvisoryRun) and row.status == "ok"
        assert isinstance(json.loads(row.stats), dict)
        sigs = await _live(db, uid)
        assert len(sigs) >= 1
        assert any(s.signal_key == "pricing_negative_margin" for s in sigs)
    _run(go())


# ── (3b) honest-absent producer run → ok, no signals ─────────────────────────

def test_producer_no_thresholds_emits_nothing():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())   # no finance
        await run_pricing_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


# ── (4)(5)(6)(7) advisory-only: 0 Decision, 0 link, no executor/marketplace ──

def test_advisory_only_no_executable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="LOSS", revenue=10000.0, net_profit=-500.0)
        await db.commit()
        # no MarketplaceConnection seeded → any marketplace call would fail; producer
        # completing proves it is DB-headless.
        await run_pricing_producer(_ctx(db, uid)); await db.commit()
        assert len(await _live(db, uid)) >= 1
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


# ── (8) idempotent — repeated runs keep one live signal per insight_key ──────

def test_idempotent_reconcile():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="LOSS", revenue=10000.0, net_profit=-500.0)
        await db.commit()
        await run_pricing_producer(_ctx(db, uid)); await db.commit()
        await run_pricing_producer(_ctx(db, uid)); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == len(set(keys)), f"duplicate live pricing signals: {keys}"
    _run(go())


# ── (9) registry: pricing registered + ENABLED (Phase 1.5); scheduler runs it ─

def test_registry_pricing_enabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "pricing" in by_key
    assert by_key["pricing"].enabled is True


def test_scheduler_runs_pricing():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="LOSS", revenue=10000.0, net_profit=-500.0)
        await db.commit()
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "pricing" in keys                                # enabled → scheduled
    _run(go())
