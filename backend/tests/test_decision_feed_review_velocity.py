"""
Decision Feed Phase 6.3a — surface ReviewVelocitySignal (reader-only).

review_velocity_signal is now a canonical feed engine (mirrors Rating 5.3a / Supply 4.3a).
Reader-only: rows are seeded directly (the producer is DISABLED and NOT run). Proves it
surfaces (live), carries contour + doctrine fields, sorts by priority_level, flows into
Today/top_action, and that resolved/dismissed rows stay hidden by default. Distinct from BOTH
the Rating contour and the Review contour — rating_signal and review_signal remain their own
engines; review_velocity reuses neither.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.review_velocity_signal import ReviewVelocitySignal
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


async def _rv(db, uid, *, sku="SKU1", priority_level="high", status="active"):
    db.add(ReviewVelocitySignal(
        user_id=uid, audit_id=str(uuid.uuid4()),
        signal_key="review_velocity_stall",
        insight_key=f"review_velocity_stall:ozon:{sku}", problem_type="review_velocity_stall",
        category="review_velocity", marketplace="ozon", sku=sku,
        what="Скорость набора отзывов снизилась на 80%", why="темп упал с 1.0 до 0.2 на продажу",
        meaning="ослабление социального доказательства — риск конверсии",
        what_to_do="Проверьте, почему покупатели реже оставляют отзывы (диагноз)",
        expected_effect="раннее внимание может сохранить силу социального доказательства",
        priority_level=priority_level, effect_type="social_proof_stall", status=status, created_at=T0))
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

def test_review_velocity_registered_in_engines():
    assert ("review_velocity", ReviewVelocitySignal, "review_velocity_signal") in _ENGINES


# ── active review_velocity surfaces with contour + doctrine fields ───────────

def test_active_review_velocity_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _rv(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        rv = [i for i in feed if i.contour == "review_velocity"]
        assert len(rv) == 1
        it = rv[0]
        assert it.item_key == "review_velocity_stall:ozon:SKU1"
        assert it.what_happened == "Скорость набора отзывов снизилась на 80%"
        assert it.why_it_matters == "темп упал с 1.0 до 0.2 на продажу"
        assert it.meaning == "ослабление социального доказательства — риск конверсии"
        assert it.recommended_action == "Проверьте, почему покупатели реже оставляют отзывы (диагноз)"
        assert it.expected_effect == "раннее внимание может сохранить силу социального доказательства"
    _run(go())


# ── priority: high review_velocity sorts above a lower-priority active signal ─

def test_high_review_velocity_sorts_above_lower():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                                   # active, priority None
        await _rv(db, uid, sku="H1", priority_level="high")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        rv_i = keys.index("review_velocity_stall:ozon:H1")
        gr_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert rv_i < gr_i
    _run(go())


# ── review_velocity flows into Today / top_action ────────────────────────────

def test_review_velocity_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _rv(db, uid, sku="H1", priority_level="high")
        await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "review_velocity" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "review_velocity_stall:ozon:H1"
    _run(go())


# ── resolved / dismissed review_velocity hidden by default ───────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _rv(db, uid, sku="R1", status="resolved")
        await _rv(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "review_velocity"] == []
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "review_velocity"]) == 2
    _run(go())
