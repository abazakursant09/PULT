"""
Advisory Runtime Phase 5.1 — Rating / Reputation Health Diagnosis producer (DISABLED + shadow).

Diagnoses aggregate rating DECLINE (baseline snapshot → latest) per (marketplace, sku) from
the seller's OWN ImportedProductRow.rating across dated import snapshots. DISTINCT from the
Review contour. Shipped DISABLED; exercised only via run_one(). Proves: confirmed decline
emits a rating_signal with the right severity band, honest absence (thin/flat/rising/below-
band/no-rating/unconfirmed-blip), advisory-only (0 Decision / 0 link / 0 executor),
idempotent, registry disabled, scheduler skips it.
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
from models.rating_signal import RatingSignal
from models.rating_audit import RatingAudit
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime, RuntimeContext
from services.advisory_runtime.producers import run_rating_producer
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
                          logger=logging.getLogger("test.rating"), triggered_by="manual")


async def _seed(db, uid, ratings, *, marketplace="wildberries", sku="SKU1"):
    """One finance row (candidacy) + one dated ImportedProductRow snapshot per rating."""
    db.add(ImportedFinanceRow(import_id="fin0", user_id=uid, marketplace=marketplace,
                              sku=sku, date="2026-06-01", quantity=1, revenue=100.0, net_profit=0.0))
    for i, r in enumerate(ratings):
        db.add(ImportedProductRow(import_id=f"imp{i}", user_id=uid, marketplace=marketplace,
                                  sku=sku, rating=r, reviews_count=100 + i,
                                  created_at=datetime(2026, 6, i + 1)))
    await db.flush()


async def _live(db, uid):
    return (await db.execute(select(RatingSignal).where(
        RatingSignal.user_id == uid,
        RatingSignal.status.in_(("active", "reopened"))))).scalars().all()


def _one(ratings, **kw):
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, ratings, **kw); await db.commit()
        await run_rating_producer(_ctx(db, uid)); await db.commit()
        return await _live(db, uid)
    return _run(go())


# ── severity bands ───────────────────────────────────────────────────────────

def test_severe_decline_emits_via_run_one():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, [4.8, 4.5, 4.3]); await db.commit()   # drop 0.5 → high
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="rating", now=NOW)
        assert isinstance(row, AdvisoryRun) and row.status == "ok"
        assert isinstance(json.loads(row.stats), dict)
        sigs = await _live(db, uid)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.signal_key == "rating_decline"
        assert s.insight_key == "rating_decline:wildberries:SKU1"
        assert s.priority_level == "high" and s.category == "rating"
        assert s.recommended_action_key is None
        assert all([s.what, s.why, s.meaning, s.what_to_do, s.expected_effect])
    _run(go())


def test_medium_decline():
    sigs = _one([4.8, 4.7, 4.55])          # drop 0.25 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_low_decline():
    sigs = _one([4.8, 4.75, 4.68])         # drop 0.12 → low
    assert len(sigs) == 1 and sigs[0].priority_level == "low"


# ── honest absence ───────────────────────────────────────────────────────────

def test_absence_thin_history():
    assert _one([4.8, 4.3]) == []                    # 2 snapshots < 3


def test_absence_flat():
    assert _one([4.8, 4.8, 4.8]) == []


def test_absence_rising():
    assert _one([4.3, 4.5, 4.8]) == []


def test_absence_below_band():
    assert _one([4.8, 4.78, 4.75]) == []             # drop 0.05 < 0.10


def test_absence_no_rating_values():
    assert _one([None, None, None]) == []            # no rating-bearing snapshots


def test_absence_unconfirmed_single_blip():
    assert _one([4.8, 4.8, 4.3]) == []               # prev == baseline → single blip, not confirmed


# ── advisory-only + idempotent ───────────────────────────────────────────────

def test_advisory_only_no_executable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, [4.8, 4.5, 4.3]); await db.commit()
        await run_rating_producer(_ctx(db, uid)); await db.commit()
        assert len(await _live(db, uid)) == 1
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


def test_idempotent_reconcile():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, [4.8, 4.5, 4.3]); await db.commit()
        await run_rating_producer(_ctx(db, uid)); await db.commit()
        await run_rating_producer(_ctx(db, uid)); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == len(set(keys)) == 1
        auds = (await db.execute(select(RatingAudit).where(RatingAudit.user_id == uid))).scalars().all()
        assert len(auds) == 2
    _run(go())


# ── registry disabled; scheduler skips it ────────────────────────────────────

def test_registry_rating_disabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "rating" in by_key
    assert by_key["rating"].enabled is False


def test_scheduler_does_not_run_rating():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, [4.8, 4.5, 4.3]); await db.commit()
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "rating" not in keys                             # disabled → never scheduled
    _run(go())
