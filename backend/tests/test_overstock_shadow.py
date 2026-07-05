"""
Overstock / Dead Stock Diagnosis — SHADOW VALIDATION (Phase 7.1), read-only.

Exercises the overstock producer via run_one() on realistic latest-stock snapshots + finance
quantity rows. Validation tests only — no production code touched. Covers the two problem types
(dead_stock / excess_stock), the excess days-of-cover band matrix (90/120/180 + exact
boundaries), honest absence (no/depleted stock, thin observed window, cover below band),
idempotence, evidence determinism, stale/lifecycle reconcile, advisory-only side-effect freedom,
enabled→scheduler-runs (Phase 7.3b), in-feed, and INDEPENDENCE from Supply (never touches
supply_signal — Overstock is the MIRROR of Supply, not a reuse).

Observed-only: days_of_cover = stock ÷ (total_units / distinct_days). No forecast, no benchmark,
no competitor compare, no discount/liquidation.
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
from models.overstock_signal import OverstockSignal
from models.overstock_audit import OverstockAudit
from models.supply_signal import SupplySignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.overstock.diagnosis_source import classify_overstock
from services.overstock.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, stock, sales, *, marketplace="wildberries", sku="SKU1"):
    """stock = latest on-hand (one ImportedProductRow snapshot on day 20).
    sales = [(day, qty)] → ImportedFinanceRow (date="2026-06-day"); also provides candidacy."""
    if stock is not None:
        db.add(ImportedProductRow(import_id=f"p{marketplace}{sku}", user_id=uid,
                                  marketplace=marketplace, sku=sku, stock=stock,
                                  created_at=datetime(2026, 6, 20)))
    for day, qty in sales:
        db.add(ImportedFinanceRow(import_id=f"f{marketplace}{sku}{day}", user_id=uid,
                                  marketplace=marketplace, sku=sku, date=f"2026-06-{day:02d}",
                                  quantity=qty, revenue=100.0, net_profit=0.0))
    await db.flush()


# 3 distinct observed days; total units controls velocity (units/3)
def _sales(total_units):
    # spread total across days 5/6/7 → 3 distinct observed days
    return [(5, total_units), (6, 0), (7, 0)]


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="overstock", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(OverstockSignal).where(
        OverstockSignal.user_id == uid,
        OverstockSignal.status.in_(("active", "reopened"))))).scalars().all()


def _one(stock, sales, **kw):
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, stock, sales, **kw); await db.commit()
        await _diagnose(db, uid); await db.commit()
        return await _live(db, uid)
    return _run(go())


# ── dead stock: stock present, zero observed sales ───────────────────────────

def test_dead_stock():
    sigs = _one(100, [(5, 0), (6, 0), (7, 0)])          # velocity 0 → dead_stock high
    assert len(sigs) == 1
    assert sigs[0].problem_type == "dead_stock" and sigs[0].priority_level == "high"
    assert sigs[0].signal_key == "overstock_dead_stock"


# ── excess stock band matrix (velocity = 1.0/day over 3 observed days) ────────

def test_excess_high_band():
    sigs = _one(200, _sales(3))                          # cover 200 → high
    assert len(sigs) == 1
    assert sigs[0].problem_type == "excess_stock" and sigs[0].priority_level == "high"


def test_excess_medium_band():
    sigs = _one(150, _sales(3))                          # cover 150 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_excess_low_band():
    sigs = _one(100, _sales(3))                          # cover 100 → low
    assert len(sigs) == 1 and sigs[0].priority_level == "low"


# ── exact boundaries (>=) ─────────────────────────────────────────────────────

def test_boundary_090_is_low():
    sigs = _one(90, _sales(3))                           # cover 90.0 → low
    assert len(sigs) == 1 and sigs[0].priority_level == "low"


def test_boundary_120_is_medium():
    sigs = _one(120, _sales(3))                          # cover 120.0 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_boundary_180_is_high():
    sigs = _one(180, _sales(3))                          # cover 180.0 → high
    assert len(sigs) == 1 and sigs[0].priority_level == "high"


# ── honest absence ────────────────────────────────────────────────────────────

def test_absence_cover_below_band():
    assert _one(50, _sales(3)) == []                     # cover 50 (<90) → nothing


def test_absence_no_stock_snapshot():
    assert _one(None, _sales(3)) == []                   # no ImportedProductRow → nothing


def test_absence_zero_stock():
    assert _one(0, _sales(3)) == []                      # depleted stock → nothing


def test_absence_thin_observed_window():
    assert _one(500, [(5, 0), (6, 0)]) == []             # only 2 observed days → nothing


# ── independence + advisory-only: writes overstock_signal only ───────────────

def test_writes_overstock_only_supply_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, 100, [(5, 0), (6, 0), (7, 0)]); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid)) == 1
        # never writes Supply's table
        assert (await db.execute(select(SupplySignal))).scalars().all() == []
        # no downstream executable / decision artifacts
        for M in (Decision, EngineSignalDecisionLink, ExecutionLog):
            assert (await db.execute(select(M))).scalars().all() == []
        sig = (await _live(db, uid))[0]
        assert sig.recommended_action_key is None
        assert sig.effect_type == "frozen_capital" and sig.category == "overstock"
    _run(go())


# ── idempotence + evidence determinism ───────────────────────────────────────

def test_idempotent_rerun_no_duplicate():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, 200, _sales(3)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        await _diagnose(db, uid); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == len(set(keys)) == 1
        auds = (await db.execute(select(OverstockAudit).where(
            OverstockAudit.user_id == uid))).scalars().all()
        assert len(auds) == 2                            # append-only audit per run
    _run(go())


def test_evidence_hash_deterministic():
    d1 = classify_overstock(200, 3, 3, marketplace="wb", sku="S")
    d2 = classify_overstock(200, 3, 3, marketplace="wb", sku="S")
    assert d1 is not None
    assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)
    assert d1.evidence["days_of_cover"] == 200.0
    assert d1.evidence["velocity"] == 1.0
    # dead stock carries days_of_cover None (velocity 0)
    dead = classify_overstock(100, 3, 0, marketplace="wb", sku="S")
    assert dead.problem_type == "dead_stock" and dead.evidence["days_of_cover"] is None


# ── lifecycle: resolved reopens; dismissed changed-evidence reopens ──────────

def test_resolved_reopens_on_redetect():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, 200, _sales(3)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "resolved"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_dismissed_changed_evidence_reopens():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, 200, _sales(3)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; sig.evidence_hash = "stale"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_dismissed_same_evidence_stays_dismissed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, 200, _sales(3)); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []                # unchanged — same evidence
    _run(go())


# ── disabled: registered but never scheduled ─────────────────────────────────

def test_registry_overstock_enabled():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "overstock" in by_key
    assert by_key["overstock"].enabled is True


def test_scheduler_runs_overstock():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, 200, _sales(3)); await db.commit()
        # default slot_budget (20) covers all 12 enabled producers for this active user
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry, default budget
        from models.advisory_run import AdvisoryRun
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "overstock" in keys                       # enabled → scheduled
    _run(go())


# ── in the Decision Feed (reader wired Phase 7.3a; producer still disabled) ───

def test_in_decision_feed():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "overstock_signal" in tables                  # reader wired (7.3a); INERT until 7.3b
    assert "supply_signal" in tables                     # Supply still wired, independent
