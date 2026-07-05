"""
Decision Feed Phase 8.3a — surface PriceErosionSignal (reader-only).

price_erosion_signal is now a canonical feed engine (mirrors Overstock 7.3a / Rating 5.3a).
Reader-only: rows are seeded directly (the producer is DISABLED and NOT run). Proves it
surfaces (live), carries contour + doctrine fields, sorts by priority_level, flows into
Today/top_action, and that resolved/dismissed rows stay hidden by default. Distinct from the
executable Pricing contour — pricing_signal remains its own engine; price_erosion reuses neither
(no price-write).
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.price_erosion_signal import PriceErosionSignal
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


async def _pe(db, uid, *, sku="SKU1", priority_level="high", status="active"):
    db.add(PriceErosionSignal(
        user_id=uid, audit_id=str(uuid.uuid4()),
        signal_key="price_erosion_discount_creep",
        insight_key=f"price_erosion_discount_creep:ozon:{sku}", problem_type="discount_creep",
        category="price_erosion", marketplace="ozon", sku=sku,
        what="Цена снизилась с 100 до 65 (−35% относительно собственной базы)",
        why="наблюдаемое снижение на 35% по 3 датированным снимкам",
        meaning="эрозия цены сжимает маржу",
        what_to_do="Проверьте причины снижения цены (диагноз)",
        expected_effect="раннее внимание может остановить сжатие маржи",
        priority_level=priority_level, effect_type="margin_compression", status=status, created_at=T0))
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

def test_price_erosion_registered_in_engines():
    assert ("price_erosion", PriceErosionSignal, "price_erosion_signal") in _ENGINES


# ── active price_erosion surfaces with contour + doctrine fields ─────────────

def test_active_price_erosion_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _pe(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        pe = [i for i in feed if i.contour == "price_erosion"]
        assert len(pe) == 1
        it = pe[0]
        assert it.item_key == "price_erosion_discount_creep:ozon:SKU1"
        assert it.what_happened == "Цена снизилась с 100 до 65 (−35% относительно собственной базы)"
        assert it.why_it_matters == "наблюдаемое снижение на 35% по 3 датированным снимкам"
        assert it.meaning == "эрозия цены сжимает маржу"
        assert it.recommended_action == "Проверьте причины снижения цены (диагноз)"
        assert it.expected_effect == "раннее внимание может остановить сжатие маржи"
    _run(go())


# ── priority: high price_erosion sorts above a lower-priority active signal ──

def test_high_price_erosion_sorts_above_lower():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                                   # active, priority None
        await _pe(db, uid, sku="H1", priority_level="high")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        pe_i = keys.index("price_erosion_discount_creep:ozon:H1")
        gr_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert pe_i < gr_i
    _run(go())


# ── price_erosion flows into Today / top_action ──────────────────────────────

def test_price_erosion_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _pe(db, uid, sku="H1", priority_level="high")
        await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "price_erosion" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "price_erosion_discount_creep:ozon:H1"
    _run(go())


# ── resolved / dismissed price_erosion hidden by default ─────────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _pe(db, uid, sku="R1", status="resolved")
        await _pe(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "price_erosion"] == []
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "price_erosion"]) == 2
    _run(go())
