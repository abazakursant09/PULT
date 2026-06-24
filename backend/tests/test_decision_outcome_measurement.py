"""
Decision Outcome A7 — effect measurement (open/close → engine_effect_observation).

Observed result only: baseline + after observed values → qualitative effect_band.
No forecast, no ROI, no fabricated money. Insufficient data → not_evaluated
(honest, not a failure). link_status becomes measured only after close. Idempotent.
No execution log.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.advertising_signal import AdvertisingSignal
from models.imported_finance import ImportedFinanceRow
from models.execution_log import ExecutionLog
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.decision_outcome.effect_measurement import (
    open_effect_measurement, close_effect_measurement, classify_band,
    NOT_EVALUATED, IMPROVED, UNCHANGED, WORSENED,
)

T0 = datetime(2026, 6, 21)
DATE = "2026-06-21"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _promote_adv(db, uid, *, mp="wildberries", sku="SKU1"):
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="adv_ad_on_low_stock", problem_type="ad_on_low_stock",
           insight_key=f"adv_ad_on_low_stock:{mp}:{sku}", marketplace=mp, sku=sku,
           status="active", what="x", why="y", expected_effect="z", what_to_do="w",
           priority_level="critical"))
    await db.commit()
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()


async def _set_profit(db, uid, value, *, mp="wildberries", sku="SKU1"):
    await db.execute(delete(ImportedFinanceRow).where(ImportedFinanceRow.user_id == uid))
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=mp, sku=sku,
                              net_profit=value, revenue=0.0, date=DATE))
    await db.commit()


async def _link(db, uid):
    return (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.user_id == uid))).scalars().one()


async def _obs(db, uid):
    return (await db.execute(select(EngineEffectObservation).where(
        EngineEffectObservation.user_id == uid))).scalars().all()


# ── classify_band unit (observed values only) ────────────────────────────────

def test_classify_band():
    assert classify_band(-500.0, 1000.0, +1) == IMPROVED
    assert classify_band(1000.0, -500.0, +1) == WORSENED
    assert classify_band(1000.0, 1010.0, +1) == UNCHANGED   # within 5%
    assert classify_band(None, 1000.0, +1) == NOT_EVALUATED
    assert classify_band(1000.0, None, +1) == NOT_EVALUATED


# ── 1. open creates baseline observation; link stays promoted ────────────────

def test_open_creates_baseline():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _promote_adv(db, uid)
        await _set_profit(db, uid, -500.0)
        r = await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        assert r.opened == 1
        obs = (await _obs(db, uid))[0]
        assert obs.baseline_captured_at == T0 and obs.measured_at is None
        assert obs.effect_band == NOT_EVALUATED and obs.metric_key == "ad_profit_impact"
        assert (await _link(db, uid)).link_status == "promoted"   # not measured until close
    _run(go())


# ── 2/5. close completes observation, classifies improved, link measured ─────

def test_close_measures_improved():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _promote_adv(db, uid)
        await _set_profit(db, uid, -500.0)
        await open_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        await _set_profit(db, uid, 1000.0)                       # after improves
        r = await close_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        assert r.closed == 1
        obs = (await _obs(db, uid))[0]
        assert obs.effect_band == IMPROVED and obs.measured_at == T0
        assert (await _link(db, uid)).link_status == "measured"
        assert len(await _obs(db, uid)) == 1                     # single observation
    _run(go())


# ── 3. insufficient data → not_evaluated (honest, not failure) ───────────────

def test_insufficient_data_not_evaluated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _promote_adv(db, uid)
        # no finance rows at all → baseline unavailable
        await open_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        r = await close_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        obs = (await _obs(db, uid))[0]
        assert obs.effect_band == NOT_EVALUATED
        assert "insufficient_data" in (obs.evidence or "")
        assert r.items[0].reason == "insufficient_data"
    _run(go())


# ── 4. worsened classified on observed values ────────────────────────────────

def test_close_measures_worsened():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _promote_adv(db, uid)
        await _set_profit(db, uid, 1000.0)
        await open_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        await _set_profit(db, uid, -500.0)
        await close_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        assert (await _obs(db, uid))[0].effect_band == WORSENED
    _run(go())


# ── 6. idempotent open + close ───────────────────────────────────────────────

def test_idempotent():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _promote_adv(db, uid)
        await _set_profit(db, uid, -500.0)
        await open_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        r2 = await open_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        assert r2.opened == 0 and r2.skipped == 1
        await _set_profit(db, uid, 1000.0)
        await close_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        r4 = await close_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        assert r4.closed == 0                                    # nothing left open
        assert len(await _obs(db, uid)) == 1
    _run(go())


# ── 7/8. no execution log; evidence carries no forecast/roi/money ────────────

def test_no_execution_no_forecast_fields():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _promote_adv(db, uid)
        await _set_profit(db, uid, -500.0)
        await open_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        await _set_profit(db, uid, 1000.0)
        await close_effect_measurement(db, user_id=uid, now=T0); await db.commit()
        assert (await db.execute(select(func.count()).select_from(ExecutionLog))).scalar() == 0
        ev = (await _obs(db, uid))[0].evidence.lower()
        for bad in ("forecast", "roi", "projection", "expected_revenue", "predicted"):
            assert bad not in ev
    _run(go())
