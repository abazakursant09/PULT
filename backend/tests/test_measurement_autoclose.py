"""
Decision Outcome — automatic (scheduler-driven) measurement close + feed surfacing.

The existing scheduler loop calls run_measurement_close every tick. It closes only
observations whose window has ELAPSED (only_expired), reusing the same close path as
the manual endpoint — read the observed after value, classify the band, flip the
link to measured. Idempotent, honest (insufficient data → not_evaluated), no
forecast. The proven band then surfaces in the Daily Decision Feed automatically.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
import models  # registers tables
from models.advertising_signal import AdvertisingSignal
from models.imported_finance import ImportedFinanceRow
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.decision_outcome.effect_measurement import (
    open_effect_measurement, close_effect_measurement,
)
import tasks.measurement_close as mc
from routers import decision_feed as feed_router

MP = "wildberries"
SKU = "SKU1"
NOW = datetime(2026, 6, 23, 12, 0, 0)
PAST = NOW - timedelta(days=20)          # window (14d) elapsed by NOW
RECENT = NOW - timedelta(days=2)         # window NOT elapsed by NOW

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _feed_client(db, uid):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(feed_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


async def _set_profit(db, uid, value, when):
    await db.execute(delete(ImportedFinanceRow).where(ImportedFinanceRow.user_id == uid))
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=MP, sku=SKU,
                              net_profit=value, revenue=0.0, date=when.date().isoformat()))
    await db.commit()


async def _seed_open_at(db, uid, *, baseline, captured_at):
    """Promote an advertising signal → link/decision, capture a baseline observation
    AT captured_at (controls window expiry)."""
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="adv_ad_on_low_stock", problem_type="ad_on_low_stock",
           insight_key=f"adv_ad_on_low_stock:{MP}:{SKU}", marketplace=MP, sku=SKU,
           status="active", what="x", why="y", expected_effect="z", what_to_do="w",
           priority_level="critical"))
    await db.commit()
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()
    await _set_profit(db, uid, baseline, captured_at)
    await open_effect_measurement(db, user_id=uid, window_days=14, now=captured_at)
    await db.commit()


async def _obs(db, uid):
    return (await db.execute(select(EngineEffectObservation).where(
        EngineEffectObservation.user_id == uid))).scalars().one()


async def _link(db, uid):
    return (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.user_id == uid))).scalars().one()


def _patch_runner_session(monkeypatch, db):
    """Make run_measurement_close use the test session (own its lifecycle, no close)."""
    class _Ctx:
        async def __aenter__(self): return db
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(mc, "AsyncSessionLocal", lambda: _Ctx())


# ── 1. expired observation closes automatically via the runner ───────────────

def test_expired_observation_closes_auto(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_seed_open_at(db, uid, baseline=100.0, captured_at=PAST))
    _run(_set_profit(db, uid, 250.0, NOW))                 # after > before → improved
    _patch_runner_session(monkeypatch, db)
    n = _run(mc.run_measurement_close(now=NOW))
    assert n == 1
    obs = _run(_obs(db, uid))
    assert obs.measured_at is not None and obs.effect_band == "improved"
    assert _run(_link(db, uid)).link_status == "measured"


# ── 2. non-expired observation stays open (no premature read) ────────────────

def test_non_expired_remains_open(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_seed_open_at(db, uid, baseline=100.0, captured_at=RECENT))   # window not elapsed
    _run(_set_profit(db, uid, 250.0, NOW))
    _patch_runner_session(monkeypatch, db)
    n = _run(mc.run_measurement_close(now=NOW))
    assert n == 0
    obs = _run(_obs(db, uid))
    assert obs.measured_at is None and obs.effect_band == "not_evaluated"  # still open
    assert _run(_link(db, uid)).link_status == "promoted"


# ── 3. auto-close idempotent (re-run touches no measured rows) ───────────────

def test_autoclose_idempotent(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_seed_open_at(db, uid, baseline=100.0, captured_at=PAST))
    _run(_set_profit(db, uid, 250.0, NOW))
    _patch_runner_session(monkeypatch, db)
    first = _run(mc.run_measurement_close(now=NOW))
    second = _run(mc.run_measurement_close(now=NOW))
    assert first == 1 and second == 0


# ── 4. proven_improved surfaces in the Daily Decision Feed ───────────────────

def test_proven_improved_in_feed():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_seed_open_at(db, uid, baseline=100.0, captured_at=PAST))
    _run(_set_profit(db, uid, 250.0, NOW))
    _run(close_effect_measurement(db, user_id=uid, now=NOW, only_expired=True)); _run(db.commit())
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    one = next(x for x in items if x.get("effect_band") == "improved")
    assert one["effect_status"] == "proven_improved"


# ── 5. proven_worsened surfaces in the feed ──────────────────────────────────

def test_proven_worsened_in_feed():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_seed_open_at(db, uid, baseline=300.0, captured_at=PAST))
    _run(_set_profit(db, uid, 100.0, NOW))                 # after < before → worsened
    _run(close_effect_measurement(db, user_id=uid, now=NOW, only_expired=True)); _run(db.commit())
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    one = next(x for x in items if x.get("effect_band") == "worsened")
    assert one["effect_status"] == "proven_worsened"


# ── 6. missing after-value → not_evaluated, surfaced honestly ────────────────

def test_not_evaluated_in_feed():
    db = _run(_new_db()); uid = str(uuid.uuid4())
    _run(_seed_open_at(db, uid, baseline=150.0, captured_at=PAST))
    _run(db.execute(delete(ImportedFinanceRow).where(ImportedFinanceRow.user_id == uid)))
    _run(db.commit())                                      # no after value
    _run(close_effect_measurement(db, user_id=uid, now=NOW, only_expired=True)); _run(db.commit())
    obs = _run(_obs(db, uid))
    assert obs.measured_at is not None and obs.effect_band == "not_evaluated"
    ev = json.loads(obs.evidence)
    assert ev.get("reason") == "insufficient_data"
    items = _feed_client(db, uid).get("/api/decision-feed").json()["items"]
    one = next(x for x in items if x.get("effect_band") == "not_evaluated")
    assert one["effect_status"] == "not_evaluated"      # surfaced honestly, not hidden
