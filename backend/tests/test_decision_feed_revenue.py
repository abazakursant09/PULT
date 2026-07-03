"""
Decision Feed Phase 2.3a — surface RevenueSignal (reader-only).

revenue_signal is now a canonical feed engine (mirrors the pricing 1.6 / operations A21
pattern). Reader-only: rows are seeded directly (the producer is DISABLED and NOT run).
Proves it surfaces (live), carries contour + doctrine fields, sorts by priority_level,
flows into Today/top_action, and that resolved/dismissed rows stay hidden by default.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.revenue_signal import RevenueSignal
from models.growth_signal import GrowthSignal

from services.decision_feed.builder import build_feed, _ENGINES
from services.decision_feed.today import build_today, top_action

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _revenue(db, uid, *, sku="SKU1", problem_type="sustained_decline",
                   priority_level="high", status="active"):
    db.add(RevenueSignal(
        user_id=uid, audit_id=str(uuid.uuid4()),
        signal_key=f"revenue_{problem_type}",
        insight_key=f"revenue_{problem_type}:ozon:{sku}", problem_type=problem_type,
        category="revenue", marketplace="ozon", sku=sku,
        what="Выручка падает", why="3 окна подряд вниз",
        meaning="с товара уходит выручка", what_to_do="Проверьте причины (диагноз)",
        expected_effect="раннее вмешательство может остановить потерю",
        priority_level=priority_level, status=status, created_at=T0))
    await db.flush()


async def _growth(db, uid, *, sku="G1"):
    db.add(GrowthSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="growth_margin_expansion_candidate",
           problem_type="margin_expansion_candidate",
           insight_key=f"growth_margin_expansion_candidate:ozon:{sku}", marketplace="ozon", sku=sku,
           status="active", what="можно поднять цену", why="маржа", meaning="x",
           what_to_do="проверить", expected_effect="маржа", created_at=T0))
    await db.flush()


# ── registry membership ──────────────────────────────────────────────────────

def test_revenue_registered_in_engines():
    assert ("revenue", RevenueSignal, "revenue_signal") in _ENGINES


# ── active revenue signal surfaces with contour + doctrine fields ────────────

def test_active_revenue_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _revenue(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        rv = [i for i in feed if i.contour == "revenue"]
        assert len(rv) == 1
        it = rv[0]
        assert it.item_key == "revenue_sustained_decline:ozon:SKU1"
        assert it.what_happened == "Выручка падает"
        assert it.why_it_matters == "3 окна подряд вниз"
        assert it.meaning == "с товара уходит выручка"
        assert it.recommended_action == "Проверьте причины (диагноз)"
        assert it.expected_effect == "раннее вмешательство может остановить потерю"
    _run(go())


# ── priority: critical revenue collapse sorts above a lower-priority signal ──

def test_critical_collapse_sorts_first():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                                   # active, priority None
        await _revenue(db, uid, sku="C1", problem_type="collapse", priority_level="critical")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        rv_i = keys.index("revenue_collapse:ozon:C1")
        gr_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert rv_i < gr_i                                       # critical revenue ranks first
    _run(go())


# ── revenue flows into Today / top_action ────────────────────────────────────

def test_revenue_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _revenue(db, uid, sku="C1", problem_type="collapse", priority_level="critical")
        await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "revenue" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "revenue_collapse:ozon:C1"
    _run(go())


# ── resolved / dismissed revenue hidden by default ───────────────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _revenue(db, uid, sku="R1", status="resolved")
        await _revenue(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "revenue"] == []
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "revenue"]) == 2
    _run(go())
