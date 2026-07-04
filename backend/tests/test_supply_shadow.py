"""
Supply / Replenishment Diagnosis — SHADOW VALIDATION (Phase 4.2), read-only.

Exercises the DISABLED supply producer via run_one() on realistic ImportedProductRow +
ImportedFinanceRow histories, raising confidence to the Money Leak 3.2 level before any
feed/enable slice. Validation tests only — no production code touched. Covers the severity-
band matrix (+ boundary behavior), honest absence, latest-stock-wins, multi-SKU isolation,
two-marketplace independence, evidence_hash determinism, stale/lifecycle reconcile,
advisory-only side-effect freedom, and disabled→no-scheduler/no-feed.

Bands (days_to_oos = stock / velocity, strict `<`): <7 critical, <14 high, <30 medium;
so exactly 7 → high, exactly 14 → medium, exactly 30 → not-at-risk (no signal).
"""
import asyncio
import uuid
from datetime import datetime, timedelta

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
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.supply.diagnosis_source import latest_stock, observed_velocity, classify_supply_risk
from services.supply.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, marketplace="wildberries", sku="SKU1", stock=20,
                daily_qty=(10, 10, 10, 10, 10, 10), with_stock=True, stock_created=NOW):
    for i, q in enumerate(daily_qty):
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=f"2026-06-{i + 1:02d}", quantity=int(q),
                                  revenue=100.0, net_profit=0.0))
    if with_stock:
        db.add(ImportedProductRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, stock=stock, created_at=stock_created))
    await db.flush()


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="supply", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(SupplySignal).where(
        SupplySignal.user_id == uid,
        SupplySignal.status.in_(("active", "reopened"))))).scalars().all()


def _one(stock, **kw):
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=stock, **kw); await db.commit()
        await _diagnose(db, uid); await db.commit()
        return await _live(db, uid)
    return _run(go())


# ── severity-band matrix (velocity 10/day) ───────────────────────────────────

def test_critical_band():
    sigs = _one(20)                 # 2 days < 7
    assert len(sigs) == 1 and sigs[0].priority_level == "critical"


def test_high_band():
    sigs = _one(100)                # 10 days < 14
    assert len(sigs) == 1 and sigs[0].priority_level == "high"


def test_medium_band():
    sigs = _one(250)                # 25 days < 30
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


# ── boundaries: exactly 7 / 14 / 30 days ─────────────────────────────────────

def test_boundary_exactly_7_days_is_high():
    sigs = _one(70)                 # 70/10 = 7.0 → not <7 → high
    assert len(sigs) == 1 and sigs[0].priority_level == "high"


def test_boundary_exactly_14_days_is_medium():
    sigs = _one(140)                # 14.0 → not <14 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_boundary_exactly_30_days_is_absent():
    sigs = _one(300)                # 30.0 → not <30 → not at risk
    assert sigs == []


# ── honest absence ───────────────────────────────────────────────────────────

def test_absence_ample_runway():
    assert _one(1000) == []                                    # 100 days


def test_absence_no_stock_snapshot():
    assert _one(20, with_stock=False) == []


def test_absence_depleted_stock():
    assert _one(0) == []


def test_absence_zero_velocity():
    assert _one(20, daily_qty=(0, 0, 0, 0, 0, 0)) == []


def test_absence_thin_history():
    assert _one(20, daily_qty=(10, 10)) == []                 # 2 sales days < 3


# ── latest-stock-wins ────────────────────────────────────────────────────────

def test_latest_stock_snapshot_wins():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # older ample snapshot, newer at-risk snapshot for same (mp, sku)
        await _seed(db, uid, stock=1000, stock_created=NOW - timedelta(days=5))
        db.add(ImportedProductRow(import_id="imp2", user_id=uid, marketplace="wildberries",
                                  sku="SKU1", stock=20, created_at=NOW))
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].priority_level == "critical"   # used newest stock=20
    _run(go())


# ── multi-SKU isolation + two-marketplace independence ───────────────────────

def test_multi_sku_only_at_risk():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, sku="RISK", stock=20)
        await _seed(db, uid, sku="AMPLE", stock=1000)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].sku == "RISK"
    _run(go())


def test_two_marketplaces_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, marketplace="wildberries", sku="SKU1", stock=20)    # critical
        await _seed(db, uid, marketplace="ozon", sku="SKU1", stock=250)          # medium
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        by_key = {s.insight_key: s for s in await _live(db, uid)}
        assert set(by_key) == {"supply_stockout_risk:wildberries:SKU1",
                               "supply_stockout_risk:ozon:SKU1"}
        assert by_key["supply_stockout_risk:wildberries:SKU1"].priority_level == "critical"
        assert by_key["supply_stockout_risk:ozon:SKU1"].priority_level == "medium"
    _run(go())


# ── evidence_hash determinism ────────────────────────────────────────────────

def test_evidence_hash_deterministic():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20); await db.commit()
        stock = await latest_stock(db, uid, "wildberries", "SKU1")
        dd, tot = await observed_velocity(db, uid, "wildberries", "SKU1")
        d1 = classify_supply_risk(stock, dd, tot, marketplace="wildberries", sku="SKU1")
        d2 = classify_supply_risk(stock, dd, tot, marketplace="wildberries", sku="SKU1")
        assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)
        await _diagnose(db, uid); await db.commit()
        h1 = (await _live(db, uid))[0].evidence_hash
        await _diagnose(db, uid); await db.commit()
        h2 = (await _live(db, uid))[0].evidence_hash
        assert h1 == h2 == evidence_hash(d1.evidence)
    _run(go())


# ── stale / lifecycle reconcile ──────────────────────────────────────────────

def test_resolved_reopens_on_redetect():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]; sig.status = "resolved"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_dismissed_same_evidence_stays_dismissed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]; sig.status = "dismissed"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
        row = (await db.execute(select(SupplySignal).where(SupplySignal.user_id == uid))).scalars().one()
        assert row.status == "dismissed"
    _run(go())


def test_dismissed_changed_evidence_reopens():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock=20); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]; sig.status = "dismissed"; sig.evidence_hash = "stale"
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_absence_does_not_resolve_live_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # finance yielding no risk (ample runway) + a pre-existing live signal
        await _seed(db, uid, stock=1000); await db.commit()
        db.add(SupplySignal(user_id=uid, audit_id=str(uuid.uuid4()),
               signal_key="supply_stockout_risk",
               insight_key="supply_stockout_risk:wildberries:SKU1",
               problem_type="supply_stockout_risk", marketplace="wildberries", sku="SKU1",
               status="active", created_at=NOW))
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "active"   # absence never auto-resolves
    _run(go())


# ── advisory-only + disabled cannot leak ─────────────────────────────────────

def test_no_decision_link_execution_side_effects():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, sku="A", stock=20)
        await _seed(db, uid, marketplace="ozon", sku="B", stock=100)
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid)) == 2
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


def test_disabled_absent_from_scheduler_and_feed():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["supply"].enabled is False
    assert "supply_signal" not in {t for (_c, _m, t) in _ENGINES}
    assert "supply" not in {c for (c, _m, _t) in _ENGINES}
