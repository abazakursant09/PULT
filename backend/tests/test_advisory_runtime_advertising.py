"""
Advisory Runtime Phase 1.1 — advertising producer (DISABLED + shadow validation).

Moves advertising signal production behind the Advisory Runtime via a thin adapter over
the EXISTING advertising engine (build_snapshot_from_finance → audit_and_persist), with
an adapter-owned threshold source (derive_advertising_thresholds) mirroring growth A11.
Shipped DISABLED; exercised only via run_one(). Proves: threshold derivation +
honest-absent, real advertising_signal through the runtime, advisory-only (0 Decision /
0 link), DB-headless (no marketplace/connection needed), idempotent reconcile, and that
the scheduler does not run it while disabled.
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
from models.advertising_signal import AdvertisingSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun
from models.execution_log import ExecutionLog

from services.advertising.threshold_source import derive_advertising_thresholds
from services.advertising.snapshot import AdvertisingThresholds
from services.advisory_runtime.runtime import AdvisoryRuntime, RuntimeContext
from services.advisory_runtime.producers import run_advertising_producer
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
                          logger=logging.getLogger("test.adv"), triggered_by="manual")


async def _fin(db, uid, *, sku, revenue, net_profit, ad_spend, mp="ozon"):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date="2026-06-20", sku=sku, revenue=revenue, net_profit=net_profit,
                              ad_spend=ad_spend, quantity=10))
    await db.flush()


async def _live(db, uid):
    return (await db.execute(select(AdvertisingSignal).where(
        AdvertisingSignal.user_id == uid,
        AdvertisingSignal.status.in_(("active", "reopened"))))).scalars().all()


# ── (1) threshold derivation with sufficient seller data ─────────────────────

def test_thresholds_derived_from_ad_finance():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="A", revenue=10000.0, net_profit=-500.0, ad_spend=1000.0)
        await db.commit()
        th = await derive_advertising_thresholds(db, uid, now=NOW)
        assert isinstance(th, AdvertisingThresholds)
        assert th.min_revenue_for_signal == 10000.0
        assert th.min_ad_spend_for_signal == 1000.0
        assert th.max_drr == 10.0                     # 1000/10000*100
        assert th.low_margin_threshold == 0.0 and th.low_stock_units == 5
    _run(go())


# ── (2) honest-absent with insufficient data (no ad spend) ───────────────────

def test_thresholds_none_without_ad_spend():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="A", revenue=10000.0, net_profit=300.0, ad_spend=0.0)
        await db.commit()
        assert await derive_advertising_thresholds(db, uid, now=NOW) is None
        # and no finance at all → also None
        assert await derive_advertising_thresholds(db, str(uuid.uuid4()), now=NOW) is None
    _run(go())


# ── (3) producer emits advertising_signal through the runtime (run_one) ──────

def test_producer_emits_signal_via_run_one():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="LOSS", revenue=10000.0, net_profit=-500.0, ad_spend=1000.0)
        await db.commit()
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="advertising", now=NOW)
        assert isinstance(row, AdvisoryRun) and row.status == "ok"
        assert isinstance(json.loads(row.stats), dict)
        sigs = await _live(db, uid)
        assert len(sigs) >= 1
        assert any(s.signal_key == "adv_ad_destroying_profit" for s in sigs)   # same shape as router
    _run(go())


# ── (3b) honest-absent producer run → ok, no signals ─────────────────────────

def test_producer_no_thresholds_emits_nothing():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="A", revenue=10000.0, net_profit=300.0, ad_spend=0.0)
        await db.commit()
        await run_advertising_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


# ── (4)(5)(6)(7) advisory-only: 0 Decision, 0 link, no executor/marketplace ──

def test_advisory_only_no_executable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="LOSS", revenue=10000.0, net_profit=-500.0, ad_spend=1000.0)
        await db.commit()
        # no MarketplaceConnection / ApiCredential seeded → any marketplace call would fail;
        # the producer completing proves it is DB-headless.
        await run_advertising_producer(_ctx(db, uid)); await db.commit()
        assert len(await _live(db, uid)) >= 1
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []   # no executor
    _run(go())


# ── (8) idempotent — repeated runs keep one live signal per insight_key ──────

def test_idempotent_reconcile():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="LOSS", revenue=10000.0, net_profit=-500.0, ad_spend=1000.0)
        await db.commit()
        await run_advertising_producer(_ctx(db, uid)); await db.commit()
        await run_advertising_producer(_ctx(db, uid)); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == len(set(keys)), f"duplicate live advertising signals: {keys}"
    _run(go())


# ── (9) registry: advertising registered + DISABLED; scheduler skips it ──────

def test_registry_advertising_disabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "advertising" in by_key
    assert by_key["advertising"].enabled is False


def test_scheduler_does_not_run_advertising():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="LOSS", revenue=10000.0, net_profit=-500.0, ad_spend=1000.0)
        await db.commit()
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "advertising" not in keys                        # disabled → never scheduled
    _run(go())
