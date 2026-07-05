"""
Returns Diagnosis — SHADOW VALIDATION (Phase R1b), read-only.

Exercises the DISABLED returns producer via run_one() on realistic dated sales
(ImportedFinanceRow) + returns (ImportedReturnRow) rows. Validation tests only — no production
code touched. Covers the self-referential return-rate-rise band matrix (25%/50%/100% + exact
boundaries), honest absence (no returns, no/thin sales, insufficient volume, earlier_rate 0,
flat/falling, sub-band), idempotence, evidence determinism, stale/lifecycle reconcile,
advisory-only side-effect freedom, disabled→no-scheduler, not-in-feed, and the DOUBLE-COUNT GUARD
(evidence is return-frequency only — never net_profit / return_amount).

Observed-only: return_rate = returns_qty / units_sold per window; relative_rise = (recent −
earlier) / earlier. No forecast, no benchmark, no competitor, no marketplace API.
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.imported_return import ImportedReturnRow
from models.returns_signal import ReturnsSignal
from models.returns_audit import ReturnsAudit
from models.revenue_signal import RevenueSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.execution_log import ExecutionLog

from datetime import datetime

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.returns.diagnosis_source import classify_returns_rise
from services.returns.persist import evidence_hash

NOW = datetime(2026, 6, 30, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, sales, returns, *, marketplace="wildberries", sku="SKU1"):
    """sales = [(day, units)] → ImportedFinanceRow; returns = [(day, qty)] → ImportedReturnRow.
    Finance rows also provide (marketplace, sku) candidacy."""
    for day, units in sales:
        db.add(ImportedFinanceRow(import_id=f"f{day}", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=f"2026-06-{day:02d}", quantity=units,
                                  revenue=100.0, net_profit=50.0))
    for day, qty in returns:
        db.add(ImportedReturnRow(import_id=f"r{day}", user_id=uid, marketplace=marketplace,
                                 sku=sku, date=f"2026-06-{day:02d}", returns_qty=qty,
                                 return_amount=999.0, reason="x"))   # return_amount must be IGNORED
    await db.flush()


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="returns", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(ReturnsSignal).where(
        ReturnsSignal.user_id == uid,
        ReturnsSignal.status.in_(("active", "reopened"))))).scalars().all()


# sales fixture: 4 dates → earlier window (days 01+02) = 20 units, recent window (days 10+20) =
# 20 units. Symmetric 20/20 gives a 0.05 rate grid on both sides so band scenarios sit COMFORTABLY
# inside their bands, not on the IEEE-fuzzy boundary. Returns on day 05 fall in the earlier window
# [01,10); returns on day 15 fall in the recent window [10,20].
def _sales():
    return [(1, 10), (2, 10), (10, 10), (20, 10)]


def _one(returns, sales=None, **kw):
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, sales or _sales(), returns, **kw); await db.commit()
        await _diagnose(db, uid); await db.commit()
        return await _live(db, uid)
    return _run(go())


# ── band matrix (earlier window units=20, recent units=20) ───────────────────

def test_high_band():
    sigs = _one([(5, 2), (15, 6)])          # earlier 0.10, recent 0.30 → rise 2.0 → high
    assert len(sigs) == 1 and sigs[0].priority_level == "high"
    assert sigs[0].problem_type == "return_rate_rise"
    assert sigs[0].signal_key == "returns_return_rate_rise"


def test_medium_band():
    sigs = _one([(5, 4), (15, 7)])          # earlier 0.20, recent 0.35 → rise 0.75 → medium
    assert len(sigs) == 1 and sigs[0].priority_level == "medium"


def test_low_band():
    sigs = _one([(5, 8), (15, 11)])         # earlier 0.40, recent 0.55 → rise 0.375 → low
    assert len(sigs) == 1 and sigs[0].priority_level == "low"


# ── honest absence ────────────────────────────────────────────────────────────

def test_absence_sub_band():
    assert _one([(5, 8), (15, 9)]) == []    # earlier 0.40, recent 0.45 → rise 0.125 → nothing


def test_absence_flat():
    assert _one([(5, 4), (15, 4)]) == []    # earlier 0.20, recent 0.20 → nothing


def test_absence_falling():
    assert _one([(5, 10), (15, 4)]) == []   # earlier 0.50, recent 0.20 → nothing


def test_absence_earlier_rate_zero():
    assert _one([(15, 6)]) == []            # no returns in earlier window → earlier_rate 0


def test_absence_no_returns_data():
    assert _one([]) == []                   # no returns rows at all


def test_absence_thin_sales():
    assert _one([(5, 2), (15, 6)], sales=[(1, 10), (10, 10)]) == []   # only 2 sale dates


def test_absence_insufficient_volume():
    # earlier window units = 3 (< MIN_WINDOW_UNITS)
    assert _one([(5, 2), (15, 6)], sales=[(1, 3), (10, 10), (20, 10)]) == []


# ── double-count guard: evidence is frequency-only ───────────────────────────

def test_evidence_is_frequency_only_no_money():
    d = classify_returns_rise(
        {"2026-06-01": 10, "2026-06-10": 10, "2026-06-20": 10},
        {"2026-06-05": 1, "2026-06-15": 6},
        marketplace="wb", sku="S")
    assert d is not None
    ev = d.evidence
    # returns frequency fields present; NO net_profit / return_amount / money keys
    assert set(ev) == {
        "earlier_returns", "recent_returns", "earlier_units", "recent_units",
        "earlier_return_rate", "recent_return_rate", "relative_rise",
        "earlier_window_start", "earlier_window_end", "recent_window_start",
        "recent_window_end", "distinct_days",
    }
    for forbidden in ("net_profit", "return_amount", "money_loss", "amount", "revenue"):
        assert forbidden not in ev
    assert ev["relative_rise"] == 2.0


def test_doctrine_makes_no_money_claim():
    sigs = _one([(5, 1), (15, 6)])
    sig = sigs[0]
    # doctrine speaks of frequency/rate, never a ruble money loss from returns
    assert "частот" in sig.what.lower()          # «частота возвратов … выросла»
    assert sig.effect_type == "return_rate_rise" and sig.category == "returns"
    assert sig.recommended_action_key is None


# ── advisory-only: writes returns_signal only ────────────────────────────────

def test_advisory_only_writes_returns_only():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _sales(), [(5, 1), (15, 6)]); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert len(await _live(db, uid)) == 1
        for M in (Decision, EngineSignalDecisionLink, ExecutionLog, RevenueSignal):
            assert (await db.execute(select(M))).scalars().all() == []
    _run(go())


# ── idempotence + evidence determinism ───────────────────────────────────────

def test_idempotent_rerun_no_duplicate():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _sales(), [(5, 1), (15, 6)]); await db.commit()
        await _diagnose(db, uid); await db.commit()
        await _diagnose(db, uid); await db.commit()
        keys = [s.insight_key for s in await _live(db, uid)]
        assert len(keys) == len(set(keys)) == 1
        auds = (await db.execute(select(ReturnsAudit).where(
            ReturnsAudit.user_id == uid))).scalars().all()
        assert len(auds) == 2
    _run(go())


def test_evidence_hash_deterministic():
    args = ({"2026-06-01": 10, "2026-06-10": 10, "2026-06-20": 10},
            {"2026-06-05": 1, "2026-06-15": 6})
    d1 = classify_returns_rise(*args, marketplace="wb", sku="S")
    d2 = classify_returns_rise(*args, marketplace="wb", sku="S")
    assert evidence_hash(d1.evidence) == evidence_hash(d2.evidence)


# ── lifecycle ────────────────────────────────────────────────────────────────

def test_resolved_reopens_on_redetect():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _sales(), [(5, 1), (15, 6)]); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "resolved"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        live = await _live(db, uid)
        assert len(live) == 1 and live[0].status == "reopened"
    _run(go())


def test_dismissed_same_evidence_stays_dismissed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _sales(), [(5, 1), (15, 6)]); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sig = (await _live(db, uid))[0]
        sig.status = "dismissed"; await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


# ── disabled: registered but never scheduled ─────────────────────────────────

def test_registry_returns_disabled():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert "returns" in by_key and by_key["returns"].enabled is False


def test_scheduler_does_not_run_returns():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid, _sales(), [(5, 1), (15, 6)]); await db.commit()
        await AdvisoryRuntime().run_due_producers(db, now=NOW)   # REAL registry, default budget
        from models.advisory_run import AdvisoryRun
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert "returns" not in keys               # disabled → never scheduled
    _run(go())


# ── not in the Decision Feed (reader is a later slice) ───────────────────────

def test_not_in_decision_feed():
    from services.decision_feed.builder import _ENGINES
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "returns_signal" not in tables
