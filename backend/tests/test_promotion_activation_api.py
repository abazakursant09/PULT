"""
Promotion Activation A3 — API tests.

POST /promotion-activation/run runs the existing promotion/bridge for the caller
(owner-scoped); after it, the promoted signal appears in the feed with a decision_id
(apply prerequisite). No executor/apply, no measurement, no marketplace. Advice-only
contours and Yandex are not promoted.
"""
import asyncio
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.advertising_signal import AdvertisingSignal
from models.seo_signal import SeoSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation
from models.execution_log import ExecutionLog

from routers import promotion_activation as pa
from routers.promotion_activation import promotion_activation_run, RunRequest, RunResponse
from services.decision_feed.builder import build_feed


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _User:
    def __init__(self, uid):
        self.id = uid


async def _adv(db, uid, *, mp="wildberries", sku="SKU1"):
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="adv_ad_destroying_profit", problem_type="ad_destroying_profit",
           insight_key=f"adv_ad_destroying_profit:{mp}:{sku}", marketplace=mp, sku=sku,
           status="active", what="x", why="y", expected_effect="z", what_to_do="w", priority_level="high"))
    await db.commit()


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


# ── 5. POST run creates a Decision for actionable adv signal ─────────────────

def test_api_run_creates_decision():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid)
        r = await promotion_activation_run(RunRequest(contour="advertising"),
                                           current_user=_User(uid), db=db)
        assert isinstance(r, RunResponse) and r.ok and r.decisions_created == 1 and r.run_id
        assert await _count(db, Decision) == 1
    _run(go())


# ── 6. after API run, feed has promoted item with decision_id ────────────────

def test_api_run_then_feed_has_decision_id():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid)
        await promotion_activation_run(RunRequest(), current_user=_User(uid), db=db)
        feed = await build_feed(db, user_id=uid)
        adv = next(i for i in feed if i.contour == "advertising")
        assert adv.source_status == "promoted_to_decision"
        d = (await db.execute(select(Decision))).scalars().one()
        assert adv.source_context.get("decision_id") == d.id
    _run(go())


# ── 7/8. API does not execute / open measurement ─────────────────────────────

def test_api_no_execution_no_measurement():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid)
        await promotion_activation_run(RunRequest(), current_user=_User(uid), db=db)
        assert await _count(db, ExecutionLog) == 0 and await _count(db, EngineEffectObservation) == 0
    _run(go())


# ── 9. advice-only contour via API → no Decision ─────────────────────────────

def test_api_advice_only_no_decision():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(SeoSignal(audit_id=str(uuid.uuid4()), user_id=uid, signal_key="seo_title_too_short",
               problem_type="title_too_short", insight_key="seo_title_too_short:wb:SKU1",
               marketplace="wb", sku="SKU1", status="active"))
        await db.commit()
        r = await promotion_activation_run(RunRequest(contour="seo"), current_user=_User(uid), db=db)
        assert r.decisions_created == 0 and await _count(db, Decision) == 0
    _run(go())


# ── 10. Yandex actionable → capability skip ──────────────────────────────────

def test_api_yandex_capability_skip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _adv(db, uid, mp="yandex", sku="SKU9")
        r = await promotion_activation_run(RunRequest(), current_user=_User(uid), db=db)
        assert r.links_created == 1 and r.decisions_created == 0
        link = (await db.execute(select(EngineSignalDecisionLink))).scalars().one()
        assert link.decision_id is None and link.link_status == "proposed"
    _run(go())


# ── owner scope: run is scoped to the caller ─────────────────────────────────

def test_api_owner_scoped():
    async def go():
        db = await _engine(); owner = str(uuid.uuid4()); other = str(uuid.uuid4())
        await _adv(db, owner)
        r = await promotion_activation_run(RunRequest(), current_user=_User(other), db=db)
        assert r.candidates_seen == 0 and r.decisions_created == 0   # sees nothing of owner's
        assert await _count(db, Decision) == 0
    _run(go())


# ── 11. no score/forecast/priority fields in API ─────────────────────────────

def test_api_no_score_forecast_priority():
    for bad in ("score", "forecast", "priority", "rank", "roi", "money", "pnl"):
        assert bad not in RunResponse.model_fields


# ── routes mounted ───────────────────────────────────────────────────────────

def test_routes_mounted():
    paths = {getattr(r, "path", None) for r in pa.router.routes}
    assert "/promotion-activation/run" in paths
    import main
    app_paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/promotion-activation/run" in app_paths
