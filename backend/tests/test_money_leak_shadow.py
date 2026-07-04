"""
Money Leak Detection — SHADOW VALIDATION (Phase 3.2), read-only.

Exercises the DISABLED money_leak producer via run_one()/adapter on realistic multi-day
ImportedFinanceRow histories, raising confidence to the Revenue 2.2 level before any
enable/feed slice. Validation tests only — no production code touched. Covers the
classification matrix (commission/logistics/both), severity mapping, honest absence,
multi-SKU isolation, two-marketplace independence, evidence_hash determinism, stale/
lifecycle reconcile, advisory-only side-effect freedom, and disabled→no-scheduler/no-feed.
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
from models.money_leak_signal import MoneyLeakSignal
from models.money_leak_audit import MoneyLeakAudit
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime, RuntimeContext
from services.money_leak.diagnosis_source import build_cost_series, classify_cost_drifts
from services.money_leak.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _series(db, uid, rows, *, marketplace="wildberries", sku="SKU1"):
    for i, (comm, logi, rev) in enumerate(rows):
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=f"2026-06-{i + 1:02d}",
                                  commission=float(comm), logistics=float(logi),
                                  revenue=float(rev), net_profit=0.0))
    await db.flush()


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="money_leak", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(MoneyLeakSignal).where(
        MoneyLeakSignal.user_id == uid,
        MoneyLeakSignal.status.in_(("active", "reopened"))))).scalars().all()


# 9-day seeds (commission, logistics, revenue), oldest→newest. rev constant 200.
COMM_HIGH = [(20, 0, 200)] * 3 + [(30, 0, 200)] * 3 + [(45, 0, 200)] * 3   # 0.10→0.15→0.225 (+125%)
COMM_MED  = [(20, 0, 200)] * 3 + [(23, 0, 200)] * 3 + [(27, 0, 200)] * 3   # 0.10→0.115→0.135 (+35%)
LOGI_HIGH = [(0, 20, 200)] * 3 + [(0, 30, 200)] * 3 + [(0, 45, 200)] * 3
BOTH      = [(20, 20, 200)] * 3 + [(30, 30, 200)] * 3 + [(45, 45, 200)] * 3
FLAT      = [(20, 20, 200)] * 9


# ── classification matrix ────────────────────────────────────────────────────

def test_commission_high_severity():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_HIGH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].problem_type == "commission_drift"
        assert sigs[0].priority_level == "high" and sigs[0].effect_type == "margin_erosion"
    _run(go())


def test_commission_medium_severity():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_MED); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].priority_level == "medium"
    _run(go())


def test_logistics_drift():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, LOGI_HIGH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].problem_type == "logistics_drift"
    _run(go())


def test_both_drifts_one_sku_two_signals():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, BOTH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        keys = {s.insight_key for s in await _live(db, uid)}
        assert keys == {"money_leak_commission_drift:wildberries:SKU1",
                        "money_leak_logistics_drift:wildberries:SKU1"}
    _run(go())


# ── multi-SKU isolation + two-marketplace independence ───────────────────────

def test_multi_sku_only_drifting():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_HIGH, sku="DRIFT")
        await _series(db, uid, FLAT, sku="FLAT")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].sku == "DRIFT"
    _run(go())


def test_two_marketplaces_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_HIGH, marketplace="wildberries", sku="SKU1")
        await _series(db, uid, LOGI_HIGH, marketplace="ozon", sku="SKU1")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        keys = {s.insight_key for s in await _live(db, uid)}
        assert keys == {"money_leak_commission_drift:wildberries:SKU1",
                        "money_leak_logistics_drift:ozon:SKU1"}
    _run(go())


# ── evidence_hash determinism ────────────────────────────────────────────────

def test_evidence_hash_deterministic():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_HIGH); await db.commit()
        daily = await build_cost_series(db, uid, "wildberries", "SKU1")
        d1 = classify_cost_drifts(daily, marketplace="wildberries", sku="SKU1")[0]
        d2 = classify_cost_drifts(daily, marketplace="wildberries", sku="SKU1")[0]
        assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)   # deterministic
        # producer persists the same hash; re-run does not churn it
        await _diagnose(db, uid); await db.commit()
        h1 = (await _live(db, uid))[0].evidence_hash
        await _diagnose(db, uid); await db.commit()
        h2 = (await _live(db, uid))[0].evidence_hash
        assert h1 == h2 == evidence_hash(d1.evidence)
    _run(go())


# ── stale / lifecycle reconcile ──────────────────────────────────────────────

def test_resolved_signal_reopens_on_redetect():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_HIGH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "resolved"; await db.commit()
        await _diagnose(db, uid); await db.commit()          # same drift persists
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_dismissed_same_evidence_stays_dismissed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_HIGH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; await db.commit()
        await _diagnose(db, uid); await db.commit()          # identical evidence
        assert await _live(db, uid) == []                    # not resurfaced
        row = (await db.execute(select(MoneyLeakSignal).where(MoneyLeakSignal.user_id == uid))).scalars().one()
        assert row.status == "dismissed"
    _run(go())


def test_dismissed_changed_evidence_reopens():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_HIGH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; sig.evidence_hash = "stale-hash"; await db.commit()
        await _diagnose(db, uid); await db.commit()          # evidence differs → reopen
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_absence_does_not_resolve_live_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # a pre-existing live signal, and finance that yields NO confirmed drift (flat)
        await _series(db, uid, FLAT); await db.commit()
        db.add(MoneyLeakSignal(
            user_id=uid, audit_id=str(uuid.uuid4()),
            signal_key="money_leak_commission_drift",
            insight_key="money_leak_commission_drift:wildberries:SKU1",
            problem_type="commission_drift", marketplace="wildberries", sku="SKU1",
            status="active", created_at=NOW))
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "active"   # absence never auto-resolves
    _run(go())


# ── advisory-only side-effect freedom on realistic multi-drift data ──────────

def test_no_decision_link_execution_side_effects():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, BOTH, sku="A")
        await _series(db, uid, LOGI_HIGH, marketplace="ozon", sku="B")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid)) >= 3                # 2 (A) + 1 (B)
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


# ── disabled → cannot leak into scheduler or feed ────────────────────────────

def test_disabled_absent_from_scheduler_and_feed():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    from services.decision_feed.builder import _ENGINES
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["money_leak"].enabled is False
    assert "money_leak_signal" not in {t for (_c, _m, t) in _ENGINES}
    assert "money_leak" not in {c for (c, _m, _t) in _ENGINES}
