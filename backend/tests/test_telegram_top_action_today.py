"""
One Morning Truth A18 — Telegram daily 'Главное сейчас' repoint.

The first surface repoint: scheduler._get_top_action now reads the canonical Today
service (top_action over build_feed), NOT legacy _compute_insights. Proves: it shows
the canonical action, honest empty state when nothing is live, and _compute_insights
is NEVER called in the top-action path. Telegram digest / intelligence_loop and
Copilot are untouched (their _compute_insights wiring is unchanged).
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.growth_signal import GrowthSignal

import tasks.scheduler as sched
import routers.action_engine as action_engine

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _factory():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)


async def _seed_growth(factory, uid, *, what_to_do="Поднять цену", sku="SKU1"):
    async with factory() as db:
        db.add(GrowthSignal(audit_id=str(uuid.uuid4()), user_id=uid,
               signal_key="growth_margin_expansion_candidate",
               problem_type="margin_expansion_candidate",
               insight_key=f"growth_margin_expansion_candidate:ozon:{sku}",
               marketplace="ozon", sku=sku, status="active", what="можно поднять цену",
               why="маржа", meaning="смысл", what_to_do=what_to_do, expected_effect="маржа",
               created_at=T0))
        await db.commit()


# ── (1)(2) Telegram top action reads Today and shows the canonical action ─────

def test_top_action_shows_canonical_action(monkeypatch):
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4())
        await _seed_growth(factory, uid, what_to_do="Поднять цену", sku="ABC")
        monkeypatch.setattr(sched, "AsyncSessionLocal", factory)
        txt = await sched._get_top_action(uid)
        assert "Поднять цену" in txt          # canonical recommended_action verbatim
        assert "ABC" in txt                   # product context appended
        assert txt != sched._NO_TOP_ACTION
    _run(go())


# ── (3) empty Today → honest empty state ─────────────────────────────────────

def test_top_action_empty_state(monkeypatch):
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4())   # no signals
        monkeypatch.setattr(sched, "AsyncSessionLocal", factory)
        txt = await sched._get_top_action(uid)
        assert txt == sched._NO_TOP_ACTION
    _run(go())


# ── (4) _compute_insights is NOT called in the top-action path ───────────────

def test_compute_insights_not_called(monkeypatch):
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4())
        await _seed_growth(factory, uid)
        monkeypatch.setattr(sched, "AsyncSessionLocal", factory)

        called = {"v": False}
        async def _boom(*a, **k):
            called["v"] = True
            return []
        monkeypatch.setattr(action_engine, "_compute_insights", _boom)

        txt = await sched._get_top_action(uid)
        assert called["v"] is False           # legacy engine never touched
        assert txt and txt != sched._NO_TOP_ACTION
    _run(go())


# ── (5) intelligence_loop still wired to legacy (digest untouched) ───────────

def test_intelligence_loop_still_uses_compute_insights():
    import inspect
    import tasks.intelligence_loop as il
    src = inspect.getsource(il)
    assert "_compute_insights" in src         # per-product digest unchanged


# ── (6) legacy /api/insights endpoint still present (Copilot untouched) ──────

def test_legacy_insights_endpoint_present():
    # Copilot reads GET /api/insights — its backend path must remain intact.
    assert hasattr(action_engine, "get_insights")
    assert hasattr(action_engine, "_compute_insights")
