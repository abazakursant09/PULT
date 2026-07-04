"""
Advisory Runtime Phase 4.1 — Supply / Replenishment Diagnosis producer (DISABLED + shadow).

Diagnoses stock-out RUNWAY (days_to_oos = observed stock ÷ observed sell-through velocity)
per (marketplace, sku) from the seller's OWN ImportedProductRow.stock +
ImportedFinanceRow.quantity. Shipped DISABLED; exercised only via run_one(). Proves:
at-risk sku emits a supply_signal with the right severity band, honest absence
(no stock / depleted / zero velocity / thin history / ample runway), advisory-only
(0 Decision / 0 link / 0 executor), idempotent, registry disabled, scheduler skips it.
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
from models.imported_product import ImportedProductRow
from models.supply_signal import SupplySignal
from models.supply_audit import SupplyAudit
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime, RuntimeContext
from services.advisory_runtime.producers import run_supply_producer
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
                          logger=logging.getLogger("test.supply"), triggered_by="manual")


async def _seed(db, uid, *, marketplace="wildberries", sku="SKU1", stock=20,
                daily_qty=(10, 10, 10, 10, 10, 10), with_stock=True):
    """Seed finance (velocity + candidacy) and, optionally, the latest stock snapshot."""
    for i, q in enumerate(daily_qty):
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=f"2026-06-{i + 1:02d}", quantity=int(q),
                                  revenue=100.0, net_profit=0.0))
    if with_stock:
        db.add(ImportedProductRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, stock=stock, created_at=NOW))
    await db.flush()


async def _live(db, uid):
    return (await db.execute(select(SupplySignal).where(
        SupplySignal.user_id == uid,
        SupplySignal.status.in_(("active", "reopened"))))).scalars().all()


# ── severity bands: stock ÷ 10/day velocity ──────────────────────────────────

def test_critical_runway_emits_via_run_one():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20)          # 20/10 = 2 days < 7 → critical
        await db.commit()
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="supply", now=NOW)
        assert isinstance(row, AdvisoryRun) and row.status == "ok"
        assert isinstance(json.loads(row.stats), dict)
        sigs = await _live(db, uid)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.signal_key == "supply_stockout_risk"
        assert s.insight_key == "supply_stockout_risk:wildberries:SKU1"
        assert s.priority_level == "critical" and s.category == "supply"
        assert s.recommended_action_key is None
        assert all([s.what, s.why, s.meaning, s.what_to_do, s.expected_effect])
    _run(go())


def test_high_runway():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=100)         # 100/10 = 10 days < 14 → high
        await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].priority_level == "high"
    _run(go())


def test_medium_runway():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=250)         # 250/10 = 25 days < 30 → medium
        await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].priority_level == "medium"
    _run(go())


# ── honest absence ───────────────────────────────────────────────────────────

def test_absence_ample_runway():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=1000)        # 100 days >= 30 → not at risk
        await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_absence_no_stock_snapshot():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, with_stock=False)  # finance only, no ImportedProductRow
        await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_absence_depleted_stock():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=0)           # stock <= 0
        await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_absence_zero_velocity():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20, daily_qty=(0, 0, 0, 0, 0, 0))   # no sales → no runway
        await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_absence_thin_history():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20, daily_qty=(10, 10))             # 2 distinct days < 3
        await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


# ── advisory-only + idempotent ───────────────────────────────────────────────

def test_advisory_only_no_executable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20); await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        assert len(await _live(db, uid)) == 1
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


def test_idempotent_reconcile():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20); await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        await run_supply_producer(_ctx(db, uid)); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == len(set(keys)) == 1
        auds = (await db.execute(select(SupplyAudit).where(SupplyAudit.user_id == uid))).scalars().all()
        assert len(auds) == 2
    _run(go())


# ── registry disabled; scheduler skips it ────────────────────────────────────

def test_registry_supply_enabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "supply" in by_key
    assert by_key["supply"].enabled is True


def test_scheduler_runs_supply_when_due():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20); await db.commit()
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "supply" in keys                                 # enabled → scheduled
    _run(go())
