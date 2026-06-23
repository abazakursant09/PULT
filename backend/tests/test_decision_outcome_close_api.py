"""
Decision Outcome — close trigger + effect surfacing (integration).

Completes the visible loop: open measurement (captured on apply) →
POST /api/decision-outcome/close reads the OBSERVED after value, classifies a
qualitative band from observed net_profit only, flips the link to measured, and
the proven effect then surfaces through GET /api/decision-outcome/effects +
/summary. No forecast, no ROI, no fabricated success — insufficient data closes
honestly as not_evaluated. link_status becomes measured only after close.

The net_profit reader is the real finance-backed reader over ImportedFinanceRow;
baseline is the profit at open time, after is the profit at close time.
"""
import asyncio
import uuid
from datetime import datetime
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
from services.decision_outcome.effect_measurement import open_effect_measurement
from routers import decision_outcome as do_router

NOW = datetime.utcnow()
DATE = NOW.date().isoformat()
MP = "wildberries"
SKU = "SKU1"


# Module-owned loop: never depend on the global asyncio policy loop (Py3.13).
_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _client(db, uid):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(do_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


async def _set_profit(db, uid, value):
    await db.execute(delete(ImportedFinanceRow).where(ImportedFinanceRow.user_id == uid))
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace=MP, sku=SKU,
                              net_profit=value, revenue=0.0, date=DATE))
    await db.commit()


async def _seed_open(db, uid, baseline):
    """Promote an advertising signal → link/decision, capture a baseline observation."""
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="adv_ad_destroying_profit", problem_type="ad_destroying_profit",
           insight_key=f"adv_ad_destroying_profit:{MP}:{SKU}", marketplace=MP, sku=SKU,
           status="active", what="x", why="y", expected_effect="z", what_to_do="w",
           priority_level="critical"))
    await db.commit()
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()
    await _set_profit(db, uid, baseline)
    await open_effect_measurement(db, user_id=uid, window_days=14, now=NOW); await db.commit()


async def _link(db, uid):
    return (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.user_id == uid))).scalars().one()


def _setup(baseline):
    uid = str(uuid.uuid4())
    db = _run(_new_db())
    _run(_seed_open(db, uid, baseline))
    return db, uid, _client(db, uid)


# ── 1. successful close → improved band surfaces in the API ──────────────────

def test_close_improved_surfaces_in_api():
    db, uid, c = _setup(baseline=100.0)
    _run(_set_profit(db, uid, 250.0))                      # after > before → improved
    r = c.post("/api/decision-outcome/close")
    assert r.status_code == 200
    body = r.json()
    assert body["closed"] == 1 and body["proven_improved"] == 1
    eff = c.get("/api/decision-outcome/effects").json()["items"]
    one = next(x for x in eff if x["sku"] == SKU)
    assert one["effect_band"] == "improved" and one["effect_status"] == "proven_improved"
    assert one["link_status"] == "measured"


# ── 2. successful close → worsened band ──────────────────────────────────────

def test_close_worsened():
    db, uid, c = _setup(baseline=300.0)
    _run(_set_profit(db, uid, 100.0))                      # after < before → worsened
    c.post("/api/decision-outcome/close")
    eff = c.get("/api/decision-outcome/effects").json()["items"]
    one = next(x for x in eff if x["sku"] == SKU)
    assert one["effect_band"] == "worsened" and one["effect_status"] == "proven_worsened"


# ── 3. unchanged band (within tolerance) ─────────────────────────────────────

def test_close_unchanged():
    db, uid, c = _setup(baseline=200.0)
    _run(_set_profit(db, uid, 200.0))                      # no real change → unchanged
    body = c.post("/api/decision-outcome/close").json()
    assert body["proven_unchanged"] == 1
    eff = c.get("/api/decision-outcome/effects").json()["items"]
    assert next(x for x in eff if x["sku"] == SKU)["effect_band"] == "unchanged"


# ── 4. missing after-value → not_evaluated (honest, not a failure) ───────────

def test_missing_after_not_evaluated():
    db, uid, c = _setup(baseline=150.0)
    _run(db.execute(delete(ImportedFinanceRow).where(ImportedFinanceRow.user_id == uid)))
    _run(db.commit())                                      # no finance rows → no after value
    body = c.post("/api/decision-outcome/close").json()
    assert body["closed"] == 1 and body["not_evaluated"] == 1
    eff = c.get("/api/decision-outcome/effects").json()["items"]
    assert next(x for x in eff if x["sku"] == SKU)["effect_band"] == "not_evaluated"


# ── 5. link_status becomes measured only AFTER close ─────────────────────────

def test_link_measured_only_after_close():
    db, uid, c = _setup(baseline=100.0)
    _run(_set_profit(db, uid, 200.0))
    before = c.get("/api/decision-outcome/effects").json()["items"]
    one_before = next(x for x in before if x["sku"] == SKU)
    assert one_before["link_status"] == "promoted"             # not measured yet
    assert one_before["effect_status"] == "not_measured_yet"
    assert _run(_link(db, uid)).link_status == "promoted"
    c.post("/api/decision-outcome/close")
    assert _run(_link(db, uid)).link_status == "measured"      # measured only after close


# ── 6. close is idempotent (re-run touches no already-measured rows) ─────────

def test_close_idempotent():
    db, uid, c = _setup(baseline=100.0)
    _run(_set_profit(db, uid, 200.0))
    first = c.post("/api/decision-outcome/close").json()
    second = c.post("/api/decision-outcome/close").json()
    assert first["closed"] == 1 and second["closed"] == 0     # nothing left open
    assert second["proven_improved"] == 1                      # surfaced effect unchanged


# ── 7. summary endpoint reflects the proven effect after close ───────────────

def test_summary_reflects_after_close():
    db, uid, c = _setup(baseline=100.0)
    _run(_set_profit(db, uid, 250.0))
    pre = c.get("/api/decision-outcome/summary").json()
    assert pre["not_measured_yet"] == 1 and pre["proven_improved"] == 0
    c.post("/api/decision-outcome/close")
    post = c.get("/api/decision-outcome/summary").json()
    assert post["proven_improved"] == 1 and post["not_measured_yet"] == 0
