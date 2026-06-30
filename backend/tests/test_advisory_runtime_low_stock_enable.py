"""
Advisory Runtime A22 — enable the operations_low_stock producer (live regression).

Flips operations_low_stock to enabled=True. The producer was built (A15) and shadow-
proven, the Decision Feed reads operations_signal (A21), and Today reads the feed.
This proves the full live path through the REAL registry / scheduler:

  * all four advisory producers enabled (legal, review, growth, operations_low_stock),
  * run_due_producers runs operations_low_stock too,
  * a low-stock listing (stock=5) yields an operations_signal; stock=6 does not,
  * the operations_signal surfaces in build_feed AND build_today / top_action,
  * advisory-only — 0 Decision, 0 EngineSignalDecisionLink (and DB-headless, so
    0 Apply / 0 executor / 0 marketplace write by construction).

Producer logic, operations_signal schema, Decision Feed and Today are unchanged.
"""
import asyncio
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
from models.operations_signal import OperationsSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.advisory_runtime.registry import ADVISORY_PRODUCERS
from services.decision_feed.builder import build_feed
from services.decision_feed.today import build_today, top_action

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, stock, sku="SKU1", mp="ozon"):
    # active-user anchor (finance) + a listing with the given stock
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date="2026-06-20", sku=sku, revenue=100.0, net_profit=10.0))
    db.add(ImportedProductRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              sku=sku, title="t", stock=stock))
    await db.commit()


async def _ops_live(db, uid):
    return (await db.execute(select(OperationsSignal).where(
        OperationsSignal.user_id == uid,
        OperationsSignal.signal_key == "operations_low_stock",
        OperationsSignal.status.in_(("active", "promoted_to_decision", "reopened"))))).scalars().all()


# ── (1) registry: legal + review + growth + operations_low_stock all enabled ─

def test_registry_all_enabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["legal"].enabled is True
    assert by_key["review"].enabled is True
    assert by_key["growth"].enabled is True
    assert by_key["operations_low_stock"].enabled is True   # A22 flip


# ── (2) scheduler runs operations_low_stock alongside the others ─────────────

def test_scheduler_runs_operations():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=5)
        res = await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        assert res.errors == 0
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert {"legal", "review", "growth", "operations_low_stock"}.issubset(keys)
    _run(go())


# ── (3) stock=5 → signal; stock=6 → no signal (through the scheduler) ────────

def test_stock_5_creates_stock_6_does_not():
    async def go():
        db = await _db(); uid5 = str(uuid.uuid4()); uid6 = str(uuid.uuid4())
        await _seed(db, uid5, stock=5)
        await _seed(db, uid6, stock=6)
        await AdvisoryRuntime().run_due_producers(db, now=NOW)
        assert len(await _ops_live(db, uid5)) == 1
        assert await _ops_live(db, uid6) == []
    _run(go())


# ── (4)(5) operations_signal reaches the Decision Feed AND Today/top_action ──

def test_operations_reaches_feed_and_today():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=5)
        await AdvisoryRuntime().run_due_producers(db, now=NOW)

        feed = await build_feed(db, user_id=uid)
        assert any(i.contour == "operations" for i in feed)               # (4) feed

        today = await build_today(db, user_id=uid)
        assert any(t.contour == "operations" for t in today)              # (5) today
        top = await top_action(db, user_id=uid)
        assert top is not None and top.contour == "operations"           # critical → first
        assert top.item_key == "operations_low_stock:ozon:SKU1"
    _run(go())


# ── (6) advisory-only: nothing executable downstream ─────────────────────────

def test_advisory_only_no_executable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=5)
        await AdvisoryRuntime().run_due_producers(db, now=NOW)
        assert len(await _ops_live(db, uid)) >= 1
        # 0 Decision, 0 EngineSignalDecisionLink (→ 0 Apply / executor / marketplace
        # write downstream, which all require a Decision that is never created)
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
    _run(go())
