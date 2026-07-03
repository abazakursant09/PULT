"""
Revenue Diagnosis — SHADOW VALIDATION (Phase 2.2), read-only.

Exercises the DISABLED revenue_diagnosis producer via run_one() on realistic multi-day
ImportedFinanceRow histories, proving the full classification matrix + advisory-only +
idempotency on realistic data. No production code touched — validation tests only.

Window math (for the seeds below): 9 days → three 3-day windows w1|w2|w3 (each a sum);
rising = every window > prior×1.05; falling = every window < prior×0.95; recent-window
CV>0.6 rejected; oldest-window floor 50. ratio=w3/w1 → ≤0.5 collapse, ≤0.8
sustained_decline, <1.0 slowing; rising → acceleration.
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
from models.revenue_signal import RevenueSignal
from models.revenue_audit import RevenueAudit
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _series(db, uid, values, *, marketplace="wildberries", sku="SKU1"):
    for i, rev in enumerate(values):
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=f"2026-06-{i + 1:02d}", revenue=float(rev),
                                  net_profit=0.0))
    await db.flush()


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="revenue_diagnosis", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(RevenueSignal).where(
        RevenueSignal.user_id == uid,
        RevenueSignal.status.in_(("active", "reopened"))))).scalars().all()


# 9-day seeds (oldest→newest). windows: [d1..d3] | [d4..d6] | [d7..d9]
DECLINE   = [300, 300, 300, 240, 240, 240, 200, 200, 200]   # w 900|720|600 ratio .667
COLLAPSE  = [300, 300, 300, 200, 200, 200, 100, 100, 100]   # w 900|600|300 ratio .333
RISE      = [200, 200, 200, 260, 260, 260, 340, 340, 340]   # w 600|780|1020 rising
SLOWING   = [300, 300, 300, 280, 280, 280, 260, 260, 260]   # w 900|840|780 ratio .867
NOISY     = [300, 300, 300, 200, 200, 200,   0,   0, 540]   # w3 CV≈1.41 → reject
FLAT_DIP  = [200, 200, 200, 200,  50, 200, 200, 200, 200]   # non-monotone → reject
FLAT      = [200, 200, 200, 200, 200, 200, 200, 200, 200]   # flat → reject


def _one(seed, *, mp="wildberries", sku="SKU1"):
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, seed, marketplace=mp, sku=sku); await db.commit()
        await _diagnose(db, uid); await db.commit()
        return db, uid, await _live(db, uid)
    return _run(go())


# ── 1-4: confirmed trajectories ──────────────────────────────────────────────

def test_sustained_decline():
    _, _, sigs = _one(DECLINE)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.problem_type == "sustained_decline"
    assert s.signal_key == "revenue_sustained_decline"
    assert s.insight_key == "revenue_sustained_decline:wildberries:SKU1"
    assert s.priority_level == "high"
    assert all([s.what, s.why, s.meaning, s.what_to_do, s.expected_effect])
    assert s.recommended_action_key is None and s.evidence_hash


def test_collapse():
    _, _, sigs = _one(COLLAPSE)
    assert len(sigs) == 1 and sigs[0].problem_type == "collapse"
    assert sigs[0].priority_level == "critical"
    assert sigs[0].insight_key == "revenue_collapse:wildberries:SKU1"
    assert sigs[0].recommended_action_key is None


def test_acceleration():
    _, _, sigs = _one(RISE)
    assert len(sigs) == 1 and sigs[0].problem_type == "acceleration"
    assert sigs[0].priority_level == "low"
    assert sigs[0].recommended_action_key is None


def test_slowing():
    _, _, sigs = _one(SLOWING)
    assert len(sigs) == 1 and sigs[0].problem_type == "slowing"
    assert sigs[0].priority_level == "medium"


# ── 5-6: honest absence ──────────────────────────────────────────────────────

def test_noisy_spike_rejected():
    _, _, sigs = _one(NOISY)
    assert sigs == []                         # recent-window CV>0.6 → no signal


def test_flat_single_dip_rejected():
    _, _, sigs = _one(FLAT_DIP)
    assert sigs == []                         # non-monotone → no signal


# ── 7: multi-SKU — only the declining sku surfaces ───────────────────────────

def test_multi_sku_only_declining():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, DECLINE, sku="DOWN")
        await _series(db, uid, FLAT, sku="FLAT")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1
        assert sigs[0].sku == "DOWN" and sigs[0].problem_type == "sustained_decline"
    _run(go())


# ── 8: same sku, two marketplaces — independent per (marketplace, sku) ────────

def test_two_marketplaces_independent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, DECLINE, marketplace="wildberries", sku="SKU1")
        await _series(db, uid, COLLAPSE, marketplace="ozon", sku="SKU1")
        await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        keys = {s.insight_key for s in sigs}
        assert keys == {"revenue_sustained_decline:wildberries:SKU1",
                        "revenue_collapse:ozon:SKU1"}
    _run(go())


# ── advisory-only + idempotency on realistic data ────────────────────────────

def test_advisory_only_and_idempotent():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, DECLINE); await db.commit()
        await _diagnose(db, uid); await db.commit()
        await _diagnose(db, uid); await db.commit()      # second run
        sigs = await _live(db, uid)
        keys = [s.insight_key for s in sigs]
        assert len(keys) == len(set(keys)) == 1          # one live signal per insight_key
        auds = (await db.execute(select(RevenueAudit).where(RevenueAudit.user_id == uid))).scalars().all()
        assert len(auds) == 2                            # audits append
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())
