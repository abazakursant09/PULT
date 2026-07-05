"""
Decision Feed Phase 5.3a — surface RatingSignal (reader-only).

rating_signal is now a canonical feed engine (mirrors Supply 4.3a / Money Leak 3.3a).
Reader-only: rows are seeded directly (the producer is DISABLED and NOT run). Proves it
surfaces (live), carries contour + doctrine fields, sorts by priority_level, flows into
Today/top_action, and that resolved/dismissed rows stay hidden by default. Distinct from the
Review contour — review_signal remains its own engine.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.rating_signal import RatingSignal
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


async def _rating(db, uid, *, sku="SKU1", priority_level="high", status="active"):
    db.add(RatingSignal(
        user_id=uid, audit_id=str(uuid.uuid4()),
        signal_key="rating_decline",
        insight_key=f"rating_decline:ozon:{sku}", problem_type="rating_decline",
        category="rating", marketplace="ozon", sku=sku,
        what="Рейтинг снизился с 4.8 до 4.3", why="падение 0.5 по 3 снимкам",
        meaning="эрозия репутации — риск конверсии", what_to_do="Проверьте причины (диагноз)",
        expected_effect="раннее внимание может остановить эрозию репутации",
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

def test_rating_registered_in_engines():
    assert ("rating", RatingSignal, "rating_signal") in _ENGINES


# ── active rating surfaces with contour + doctrine fields ────────────────────

def test_active_rating_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _rating(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        rt = [i for i in feed if i.contour == "rating"]
        assert len(rt) == 1
        it = rt[0]
        assert it.item_key == "rating_decline:ozon:SKU1"
        assert it.what_happened == "Рейтинг снизился с 4.8 до 4.3"
        assert it.why_it_matters == "падение 0.5 по 3 снимкам"
        assert it.meaning == "эрозия репутации — риск конверсии"
        assert it.recommended_action == "Проверьте причины (диагноз)"
        assert it.expected_effect == "раннее внимание может остановить эрозию репутации"
    _run(go())


# ── priority: high rating sorts above a lower-priority active signal ─────────

def test_high_rating_sorts_above_lower():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                                   # active, priority None
        await _rating(db, uid, sku="H1", priority_level="high")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        rt_i = keys.index("rating_decline:ozon:H1")
        gr_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert rt_i < gr_i
    _run(go())


# ── rating flows into Today / top_action ─────────────────────────────────────

def test_rating_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _rating(db, uid, sku="H1", priority_level="high")
        await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "rating" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "rating_decline:ozon:H1"
    _run(go())


# ── resolved / dismissed rating hidden by default ────────────────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _rating(db, uid, sku="R1", status="resolved")
        await _rating(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "rating"] == []
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "rating"]) == 2
    _run(go())
