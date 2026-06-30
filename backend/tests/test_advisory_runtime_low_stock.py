"""
Advisory Runtime A15 — canonical low-stock producer (DISABLED + shadow validation).

First canonical producer for a legacy _compute_insights signal (low_stock), built
ALONGSIDE the legacy on-read logic (legacy untouched). NARROW producer name
`operations_low_stock`. Shipped DISABLED — exercised only through run_one(), never the
scheduler. Proves: threshold boundary (5 yes / 6 no), idempotent reconcile (one live
signal), AdvisoryRun ok, advisory-only (0 Decision, 0 EngineSignalDecisionLink),
producer disabled, scheduler does not run it.
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
from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow
from models.operations_signal import OperationsSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun

from services.operations.low_stock_source import (
    build_low_stock_signal, SIGNAL_KEY, LOW_STOCK_UNITS,
)
from services.advisory_runtime.runtime import AdvisoryRuntime, RuntimeContext
from services.advisory_runtime.producers import run_operations_low_stock_producer
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
                          logger=logging.getLogger("test.low_stock"), triggered_by="manual")


async def _product(db, uid, *, sku, stock, mp="ozon"):
    db.add(ImportedProductRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              sku=sku, title="t", stock=stock))
    await db.flush()


async def _active(db, uid, mp="ozon"):
    # active-user anchor for the scheduler (run_due_producers enumerates finance users)
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date="2026-06-20", sku="SKU1", revenue=100.0, net_profit=10.0))
    await db.flush()


async def _live(db, uid):
    return (await db.execute(select(OperationsSignal).where(
        OperationsSignal.user_id == uid,
        OperationsSignal.signal_key == SIGNAL_KEY,
        OperationsSignal.status.in_(("active", "promoted_to_decision", "reopened"))))).scalars().all()


# ── (1) stock == 5 → signal exists (boundary inclusive) ──────────────────────

def test_stock_5_produces_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _product(db, uid, sku="LOW", stock=5); await db.commit()
        await run_operations_low_stock_producer(_ctx(db, uid)); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1
        assert sigs[0].problem_type == "low_stock"
        assert sigs[0].sku == "LOW"
        assert sigs[0].recommended_action_key is None      # advisory-only
    _run(go())


# ── (2) stock == 6 → no signal (above the low-stock definition) ──────────────

def test_stock_6_produces_no_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _product(db, uid, sku="OK", stock=6); await db.commit()
        await run_operations_low_stock_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


# ── (3) reconcile — repeated runs keep ONE live signal per insight_key ───────

def test_reconcile_one_live_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _product(db, uid, sku="LOW", stock=3); await db.commit()
        await run_operations_low_stock_producer(_ctx(db, uid)); await db.commit()
        await run_operations_low_stock_producer(_ctx(db, uid)); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == 1 and len(set(keys)) == 1
    _run(go())


# ── (4)(5)(6) run_one shadow — AdvisoryRun ok, advisory-only ─────────────────

def test_run_one_shadow_advisory_only():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _product(db, uid, sku="LOW", stock=2); await db.commit()
        row = await AdvisoryRuntime().run_one(
            db, user_id=uid, producer_key="operations_low_stock", now=NOW)
        assert isinstance(row, AdvisoryRun) and row.status == "ok"      # (4)
        assert isinstance(json.loads(row.stats), dict)                  # opaque stats
        assert len(await _live(db, uid)) >= 1                           # signal created
        assert (await db.execute(select(Decision).where(               # (5)
            Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(  # (6)
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
    _run(go())


# ── (7) producer disabled ────────────────────────────────────────────────────

def test_producer_disabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["operations_low_stock"].enabled is False


# ── (8) scheduler does NOT run it (disabled → never scheduled) ───────────────

def test_scheduler_does_not_run_low_stock():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _active(db, uid)                       # active user (finance)
        await _product(db, uid, sku="LOW", stock=1)  # a real low-stock candidate
        await db.commit()
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "operations_low_stock" not in keys    # disabled → never scheduled
        assert await _live(db, uid) == []            # and so no low-stock signal made
    _run(go())
