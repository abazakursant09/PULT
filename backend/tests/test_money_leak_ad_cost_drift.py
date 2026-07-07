"""
Money Leak — ad_cost_drift problem_type (micro-slice).

ad_cost_drift = the seller's OWN observed advertising-cost share of revenue
(DRR = ad_spend/revenue) confirmed RISING across time windows of uploaded
ImportedFinanceRow — a Money Leak DIAGNOSIS (advisory-only, no lever), NOT the
executable Advertising contour's point-in-time state.

Proves:
  * rising DRR over windows → ad_cost_drift (severity from observed growth),
  * onset guard — starting to advertise (0 → positive) is NOT erosion; ad absent in
    the oldest window, ad only in recent windows, and tiny-base ad are all rejected,
  * the existing gates still hold for ad (spike rejection, revenue floor),
  * ad_cost_drift is independent — coexists with commission_drift / logistics_drift,
  * it persists into money_leak_signal (advisory copy, recommended_action_key=None),
  * it surfaces through the EXISTING Money Leak Decision-Feed reader,
  * advisory-only — NO Decision / EngineSignalDecisionLink / ExecutionLog.

Reuses the disabled/enabled money_leak producer via run_one() — no new producer,
no registry/scheduler/feed/migration change.
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
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.execution_log import ExecutionLog

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.money_leak.diagnosis_source import build_cost_series, classify_cost_drifts
from services.decision_feed.builder import build_feed

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
    """rows: (commission, logistics, ad_spend, revenue) per day, oldest→newest."""
    for i, (comm, logi, ad, rev) in enumerate(rows):
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=marketplace,
                                  sku=sku, date=f"2026-06-{i + 1:02d}",
                                  commission=float(comm), logistics=float(logi),
                                  ad_spend=float(ad), revenue=float(rev), net_profit=0.0))
    await db.flush()


async def _diagnose(db, uid):
    await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="money_leak", now=NOW)


async def _live(db, uid):
    return (await db.execute(select(MoneyLeakSignal).where(
        MoneyLeakSignal.user_id == uid,
        MoneyLeakSignal.status.in_(("active", "reopened"))))).scalars().all()


def _classify(db_rows):
    return classify_cost_drifts(db_rows, marketplace="wildberries", sku="SKU1")


# 9-day seeds (commission, logistics, ad_spend, revenue), oldest→newest. rev constant 200.
AD_HIGH        = [(0, 0, 20, 200)] * 3 + [(0, 0, 30, 200)] * 3 + [(0, 0, 45, 200)] * 3   # DRR .10→.15→.225 (+125%)
AD_MED         = [(0, 0, 20, 200)] * 3 + [(0, 0, 23, 200)] * 3 + [(0, 0, 27, 200)] * 3   # +35% → medium
AD_ONSET       = [(0, 0, 0, 200)] * 6 + [(0, 0, 45, 200)] * 3                            # ads only recent
AD_MISSING_OLD = [(0, 0, 0, 200)] * 3 + [(0, 0, 30, 200)] * 3 + [(0, 0, 45, 200)] * 3    # oldest window ad = 0
AD_TINY_BASE   = [(0, 0, 1, 200)] * 3 + [(0, 0, 5, 200)] * 3 + [(0, 0, 20, 200)] * 3     # oldest sum 3 < MIN_AD_SPEND
AD_SPIKE       = ([(0, 0, 20, 200)] * 3 + [(0, 0, 30, 200)] * 3
                  + [(0, 0, 90, 200), (0, 0, 90, 200), (0, 0, 0, 200)])                  # recent window spikey
AD_SUBFLOOR    = [(0, 0, 4, 15)] * 3 + [(0, 0, 6, 15)] * 3 + [(0, 0, 10, 15)] * 3        # oldest rev 45 < FLOOR_3W
COMM_AND_AD    = [(20, 0, 20, 200)] * 3 + [(30, 0, 30, 200)] * 3 + [(45, 0, 45, 200)] * 3
LOGI_AND_AD    = [(0, 20, 20, 200)] * 3 + [(0, 30, 30, 200)] * 3 + [(0, 45, 45, 200)] * 3


# ── detection: rising DRR fires; severity from observed growth ────────────────

def test_rising_drr_produces_ad_cost_drift_high():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_HIGH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.problem_type == "ad_cost_drift"
        assert s.insight_key == "money_leak_ad_cost_drift:wildberries:SKU1"
        assert s.priority_level == "high" and s.effect_type == "margin_erosion"
    _run(go())


def test_rising_drr_medium_severity():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_MED); await db.commit()
        await _diagnose(db, uid); await db.commit()
        sigs = await _live(db, uid)
        assert len(sigs) == 1 and sigs[0].priority_level == "medium"
    _run(go())


# ── onset guard: advertising ONSET is not erosion ────────────────────────────

def test_ad_onset_no_diagnosis():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_ONSET); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_ad_missing_in_oldest_window_no_diagnosis():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_MISSING_OLD); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_tiny_ad_base_no_false_diagnosis():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_TINY_BASE); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


# ── existing gates still hold for ad ─────────────────────────────────────────

def test_spike_rejection_still_applies_to_ad():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_SPIKE); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


def test_revenue_floor_still_applies_to_ad():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_SUBFLOOR); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid) == []
    _run(go())


# ── independence: coexists with commission / logistics drift ─────────────────

def test_commission_and_ad_coexist():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_AND_AD); await db.commit()
        await _diagnose(db, uid); await db.commit()
        keys = {s.insight_key for s in await _live(db, uid)}
        assert keys == {"money_leak_commission_drift:wildberries:SKU1",
                        "money_leak_ad_cost_drift:wildberries:SKU1"}
    _run(go())


def test_logistics_and_ad_coexist():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, LOGI_AND_AD); await db.commit()
        await _diagnose(db, uid); await db.commit()
        keys = {s.insight_key for s in await _live(db, uid)}
        assert keys == {"money_leak_logistics_drift:wildberries:SKU1",
                        "money_leak_ad_cost_drift:wildberries:SKU1"}
    _run(go())


# ── persistence: advisory copy, no bound lever ───────────────────────────────

def test_ad_cost_drift_persists_advisory_only():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_HIGH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        s = (await _live(db, uid))[0]
        assert s.recommended_action_key is None      # pure diagnosis — no executor bound
        assert s.alternative_action_keys is None
        for field in (s.what, s.why, s.meaning, s.what_to_do, s.expected_effect):
            assert field and field.strip()
            assert "{" not in field and "}" not in field
        assert "ДРР" in s.what or "Реклама" in s.what  # ad-specific label
    _run(go())


# ── surfaces through the EXISTING Money Leak Decision-Feed reader ─────────────

def test_ad_cost_drift_surfaces_in_feed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_HIGH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid, now=NOW)
        ad = [i for i in feed if i.contour == "money_leak"
              and i.item_key == "money_leak_ad_cost_drift:wildberries:SKU1"]
        assert len(ad) == 1
        assert ad[0].marketplace == "wildberries" and ad[0].sku == "SKU1"
        assert ad[0].title and ad[0].title.strip()
        assert ad[0].what_happened and ad[0].why_it_matters and ad[0].recommended_action
    _run(go())


# ── advisory-only: nothing executable downstream ─────────────────────────────

def test_ad_cost_drift_no_executable_side_effects():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, AD_HIGH); await db.commit()
        await _diagnose(db, uid); await db.commit()
        assert await _live(db, uid)                    # signal exists
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(ExecutionLog))).scalars().all() == []
    _run(go())


# ── unit: classify returns ad_cost_drift alongside others, deterministically ──

def test_classify_ad_branch_unit():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _series(db, uid, COMM_AND_AD); await db.commit()
        daily = await build_cost_series(db, uid, "wildberries", "SKU1")
        out = _classify(daily)
        pts = {d.problem_type for d in out}
        assert pts == {"commission_drift", "ad_cost_drift"}
    _run(go())
