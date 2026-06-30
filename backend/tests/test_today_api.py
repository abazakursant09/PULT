"""
Today API (One Morning Truth, A19) — endpoint tests.

GET /api/today is a read-only canonical surface over the Today service (build_feed
projection) — the same source Copilot now reads, replacing legacy /api/insights.
Proves: canonical items + top_action, empty state, no _compute_insights in the Today
path, and that the Decision Feed API / intelligence_loop are untouched.
"""
import asyncio
import inspect
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal

from routers import today as today_router
from routers.today import get_today, TodayResponse
from services.decision_feed.builder import build_feed

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _User:
    def __init__(self, uid):
        self.id = uid


async def _seed(db, uid):
    aid = str(uuid.uuid4())
    db.add(GrowthSignal(audit_id=aid, user_id=uid, signal_key="growth_margin_expansion_candidate",
           problem_type="margin_expansion_candidate",
           insight_key="growth_margin_expansion_candidate:ozon:SKU1", marketplace="ozon", sku="SKU1",
           status="active", what="можно поднять цену", why="маржа", meaning="x",
           what_to_do="Поднять цену", expected_effect="маржа", created_at=T0))
    db.add(LegalSignal(audit_id=aid, user_id=uid, signal_key="legal_content_claim_risk",
           requirement_type="content_claim_risk", insight_key="legal_content_claim_risk:wildberries:SKU1",
           marketplace="wildberries", sku="SKU1", status="active", what="формулировки", why="претензии",
           meaning="x", what_to_do="проверить", expected_effect="риск", created_at=T0))
    await db.commit()


# ── (1) endpoint returns canonical Today items + top_action ──────────────────

def test_today_returns_canonical_items():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed(db, uid)
        resp = await get_today(contour=None, limit=50, current_user=_User(uid), db=db)
        assert isinstance(resp, TodayResponse)
        feed = await build_feed(db, user_id=uid)
        # same items, same order as the canonical feed
        assert [i.item_key for i in resp.items] == [f.item_key for f in feed]
        assert resp.total == len(feed) >= 1
        # top_action is the first item; doctrine field passes through verbatim
        assert resp.top_action is not None
        assert resp.top_action.item_key == feed[0].item_key
        assert resp.top_action.recommended_action == feed[0].recommended_action
    _run(go())


# ── (4) empty state — no signals → no items, top_action null ──────────────────

def test_today_empty_state():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())   # nothing seeded
        resp = await get_today(contour=None, limit=50, current_user=_User(uid), db=db)
        assert resp.items == [] and resp.total == 0
        assert resp.top_action is None
    _run(go())


# ── (3) _compute_insights is NOT in the Today endpoint path ──────────────────

def test_today_path_has_no_compute_insights():
    # neither the endpoint module nor the Today service imports the legacy engine
    from services.decision_feed import today as today_service
    for mod in (today_router, today_service):
        src = inspect.getsource(mod)
        assert "_compute_insights" not in src, f"{mod.__name__} must not touch legacy engine"


# ── (5) Decision Feed API unchanged (Dashboard surface intact) ───────────────

def test_decision_feed_api_unchanged():
    from routers import decision_feed as df
    # the dashboard's endpoint + its mutations still exist, untouched by this slice
    assert hasattr(df, "get_decision_feed")
    for fn in ("feed_seen", "feed_snooze", "feed_dismiss", "feed_act"):
        assert hasattr(df, fn)


# ── (6) Telegram digest / intelligence_loop still on legacy (unchanged) ──────

def test_intelligence_loop_unchanged():
    import tasks.intelligence_loop as il
    assert "_compute_insights" in inspect.getsource(il)
