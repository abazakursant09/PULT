"""
Advisory Runtime Phase 3.1 — Money Leak Detection producer (DISABLED + shadow).

Business-Diagnosis contour: WHY money leaks — silent commission/logistics cost-share drift.
Pure diagnosis over the seller's OWN observed finance (spike-rejected, history-gated).
Shipped DISABLED; exercised only via run_one(). Proves: confirmed commission/logistics
drift emit a money_leak_signal, honest absence (thin/flat/spike/sub-floor/zero-revenue),
advisory-only (0 Decision / 0 link / 0 executor), idempotent, registry disabled, scheduler
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
from models.money_leak_signal import MoneyLeakSignal
from models.money_leak_audit import MoneyLeakAudit
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.advisory_runtime.producers import run_money_leak_producer
from services.advisory_runtime.registry import ADVISORY_PRODUCERS
from services.advisory_runtime.runtime import RuntimeContext

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
                          logger=logging.getLogger("test.money_leak"), triggered_by="manual")


async def _series(db, uid, rows, *, marketplace="wildberries", sku="SKU1"):
    """rows = list of (commission, logistics, revenue) per day, oldest first."""
    for i, (comm, logi, rev) in enumerate(rows):
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=f"2026-06-{i + 1:02d}",
                                  commission=float(comm), logistics=float(logi),
                                  revenue=float(rev), net_profit=0.0))
    await db.flush()


async def _live(db, uid):
    return (await db.execute(select(MoneyLeakSignal).where(
        MoneyLeakSignal.user_id == uid,
        MoneyLeakSignal.status.in_(("active", "reopened"))))).scalars().all()


# 9-day seeds: (commission, logistics, revenue). revenue constant 200.
# commission share 0.10 → 0.15 → 0.225 (rising) ; logistics 0.
COMMISSION = [(20, 0, 200)] * 3 + [(30, 0, 200)] * 3 + [(45, 0, 200)] * 3
LOGISTICS  = [(0, 20, 200)] * 3 + [(0, 30, 200)] * 3 + [(0, 45, 200)] * 3
FLAT       = [(20, 20, 200)] * 9                                  # shares flat → no drift
SPIKE      = [(20, 0, 200)] * 3 + [(30, 0, 200)] * 3 + [(0, 0, 200), (0, 0, 200), (135, 0, 200)]
SUBFLOOR   = [(2, 0, 5)] * 3 + [(3, 0, 5)] * 3 + [(4, 0, 5)] * 3   # oldest rev window 15 < 50


# ── (1)(2) commission drift emits via run_one ────────────────────────────────

def test_commission_drift_emits_via_run_one():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMMISSION); await db.commit()
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="money_leak", now=NOW)
        assert isinstance(row, AdvisoryRun) and row.status == "ok"
        assert isinstance(json.loads(row.stats), dict)
        sigs = await _live(db, uid)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.signal_key == "money_leak_commission_drift"
        assert s.insight_key == "money_leak_commission_drift:wildberries:SKU1"
        assert s.priority_level == "high" and s.category == "money_leak"
        assert s.recommended_action_key is None
        assert all([s.what, s.why, s.meaning, s.what_to_do, s.expected_effect])
    _run(go())


# ── (3) logistics drift emits ────────────────────────────────────────────────

def test_logistics_drift_emits():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, LOGISTICS); await db.commit()
        await run_money_leak_producer(_ctx(db, uid)); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].signal_key == "money_leak_logistics_drift"
    _run(go())


# ── (4) honest absence ───────────────────────────────────────────────────────

def test_absence_thin_history():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, [(20, 0, 200)] * 5); await db.commit()   # 5 days < 6
        await run_money_leak_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_absence_flat_shares():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, FLAT); await db.commit()
        await run_money_leak_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_absence_spike_only():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, SPIKE); await db.commit()
        await run_money_leak_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_absence_sub_floor():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, SUBFLOOR); await db.commit()
        await run_money_leak_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_zero_revenue_rows_ignored_no_crash():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # interleave revenue=0 rows (ratio undefined) with a valid commission drift
        rows = [(20, 0, 200), (0, 0, 0), (20, 0, 200), (20, 0, 200),
                (30, 0, 200), (30, 0, 200), (30, 0, 200),
                (45, 0, 200), (45, 0, 200), (45, 0, 200)]
        await _series(db, uid, rows); await db.commit()
        await run_money_leak_producer(_ctx(db, uid)); await db.commit()   # must not crash
        sigs = await _live(db, uid)
        assert all(s.signal_key.startswith("money_leak_") for s in sigs)
    _run(go())


# ── (5) advisory-only ────────────────────────────────────────────────────────

def test_advisory_only_no_executable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMMISSION); await db.commit()
        await run_money_leak_producer(_ctx(db, uid)); await db.commit()
        assert len(await _live(db, uid)) == 1
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


# ── (6) idempotent — one live signal per insight_key ─────────────────────────

def test_idempotent_reconcile():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMMISSION); await db.commit()
        await run_money_leak_producer(_ctx(db, uid)); await db.commit()
        await run_money_leak_producer(_ctx(db, uid)); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == len(set(keys)) == 1
        auds = (await db.execute(select(MoneyLeakAudit).where(MoneyLeakAudit.user_id == uid))).scalars().all()
        assert len(auds) == 2
    _run(go())


# ── (7) registry disabled; (8) scheduler skips it ───────────────────────────

def test_registry_money_leak_disabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "money_leak" in by_key
    assert by_key["money_leak"].enabled is False


def test_scheduler_does_not_run_money_leak():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMMISSION); await db.commit()
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "money_leak" not in keys                          # disabled → never scheduled
    _run(go())
