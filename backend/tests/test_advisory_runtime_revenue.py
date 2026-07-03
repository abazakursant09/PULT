"""
Advisory Runtime Phase 2.1 — Revenue Diagnosis producer (DISABLED + shadow).

First Business-Diagnosis contour: WHERE is revenue disappearing? Pure diagnosis over the
seller's OWN observed ImportedFinanceRow series (spike-rejected, history-gated). Shipped
DISABLED; exercised only via run_one(). Proves: confirmed decline/collapse emit a
revenue_signal, honest absence emits nothing (thin/flat/spike/sub-floor), advisory-only
(0 Decision / 0 link / 0 executor), idempotent reconcile, registry disabled, scheduler
skips it.
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
from models.revenue_signal import RevenueSignal
from models.revenue_audit import RevenueAudit
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime, RuntimeContext
from services.advisory_runtime.producers import run_revenue_diagnosis_producer
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
                          logger=logging.getLogger("test.revenue"), triggered_by="manual")


async def _series(db, uid, values, *, marketplace="wildberries", sku="SKU1"):
    """Seed one ImportedFinanceRow per day. `values` = list of daily revenue, oldest first,
    dates 2026-06-01 .. (distinct dates)."""
    for i, rev in enumerate(values):
        day = f"2026-06-{i + 1:02d}"
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=day, revenue=float(rev), net_profit=0.0))
    await db.flush()


async def _live(db, uid):
    return (await db.execute(select(RevenueSignal).where(
        RevenueSignal.user_id == uid,
        RevenueSignal.status.in_(("active", "reopened"))))).scalars().all()


# ── (1) manual run + (2) confirmed sustained decline emits one signal ────────

def test_sustained_decline_emits_via_run_one():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # 6 days: w1=600 (200×3), w2=420 (140×3) → ratio 0.70 → sustained_decline
        await _series(db, uid, [200, 200, 200, 140, 140, 140]); await db.commit()
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="revenue_diagnosis", now=NOW)
        assert isinstance(row, AdvisoryRun) and row.status == "ok"
        assert isinstance(json.loads(row.stats), dict)
        sigs = await _live(db, uid)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.signal_key == "revenue_sustained_decline"
        assert s.insight_key == "revenue_sustained_decline:wildberries:SKU1"
        assert s.priority_level == "high" and s.category == "revenue"
        assert s.recommended_action_key is None          # pure diagnosis, no executor
        assert s.what and s.why and s.meaning and s.what_to_do and s.expected_effect
    _run(go())


# ── (3) confirmed collapse emits ─────────────────────────────────────────────

def test_collapse_emits():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # w1=600, w2=300 (100×3) → ratio 0.50 → collapse
        await _series(db, uid, [200, 200, 200, 100, 100, 100]); await db.commit()
        await run_revenue_diagnosis_producer(_ctx(db, uid)); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].signal_key == "revenue_collapse"
        assert sigs[0].priority_level == "critical"
    _run(go())


# ── (4) honest absence: thin / flat / spike / sub-floor ──────────────────────

def test_honest_absence_thin_history():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, [300, 250, 200, 150, 100]); await db.commit()   # 5 days < 6
        await run_revenue_diagnosis_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_honest_absence_flat():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, [200, 200, 200, 200, 200, 200]); await db.commit()  # flat → not monotone
        await run_revenue_diagnosis_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_honest_absence_spike_only():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # falling window sums but recent window spikey (0,0,420) → CV>0.6 → rejected
        await _series(db, uid, [200, 200, 200, 0, 0, 420]); await db.commit()
        await run_revenue_diagnosis_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_honest_absence_sub_floor():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # declining shape but oldest window sum 30 < FLOOR_2W(100) → sub-floor
        await _series(db, uid, [10, 10, 10, 6, 6, 6]); await db.commit()
        await run_revenue_diagnosis_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


# ── (5) advisory-only: 0 Decision / 0 link / 0 ExecutionLog ──────────────────

def test_advisory_only_no_executable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, [200, 200, 200, 140, 140, 140]); await db.commit()
        await run_revenue_diagnosis_producer(_ctx(db, uid)); await db.commit()
        assert len(await _live(db, uid)) == 1
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


# ── (6) idempotent — one live signal per insight_key across repeated runs ─────

def test_idempotent_reconcile():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, [200, 200, 200, 140, 140, 140]); await db.commit()
        await run_revenue_diagnosis_producer(_ctx(db, uid)); await db.commit()
        await run_revenue_diagnosis_producer(_ctx(db, uid)); await db.commit()
        sigs = await _live(db, uid)
        keys = [s.insight_key for s in sigs]
        assert len(keys) == len(set(keys)) == 1
        # exactly one live signal, two audits (append-only)
        auds = (await db.execute(select(RevenueAudit).where(RevenueAudit.user_id == uid))).scalars().all()
        assert len(auds) == 2
    _run(go())


# ── (7) registry disabled; (8) scheduler skips it ───────────────────────────

def test_registry_revenue_disabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "revenue_diagnosis" in by_key
    assert by_key["revenue_diagnosis"].enabled is False


def test_scheduler_does_not_run_revenue():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, [200, 200, 200, 140, 140, 140]); await db.commit()
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "revenue_diagnosis" not in keys                   # disabled → never scheduled
    _run(go())
