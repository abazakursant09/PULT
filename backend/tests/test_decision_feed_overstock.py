"""
Decision Feed Phase 7.3a — surface OverstockSignal (reader-only).

overstock_signal is now a canonical feed engine (mirrors Supply 4.3a / Rating 5.3a).
Reader-only: rows are seeded directly (the producer is DISABLED and NOT run). Proves it
surfaces (live), carries contour + doctrine fields, sorts by priority_level, flows into
Today/top_action, and that resolved/dismissed rows stay hidden by default. Distinct from Supply
(the stock-out mirror) — supply_signal remains its own engine; overstock reuses neither.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.overstock_signal import OverstockSignal
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


async def _ov(db, uid, *, sku="SKU1", priority_level="high", status="active"):
    db.add(OverstockSignal(
        user_id=uid, audit_id=str(uuid.uuid4()),
        signal_key="overstock_excess_stock",
        insight_key=f"overstock_excess_stock:ozon:{sku}", problem_type="excess_stock",
        category="overstock", marketplace="ozon", sku=sku,
        what="Запаса хватит на ~200 дн. при текущем темпе", why="остаток 200 шт., темп 1 шт./дн",
        meaning="избыточный запас — деньги заморожены в излишке",
        what_to_do="Проверьте объём закупки относительно спроса (диагноз)",
        expected_effect="раннее внимание может высвободить замороженные средства",
        priority_level=priority_level, effect_type="frozen_capital", status=status, created_at=T0))
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

def test_overstock_registered_in_engines():
    assert ("overstock", OverstockSignal, "overstock_signal") in _ENGINES


# ── active overstock surfaces with contour + doctrine fields ─────────────────

def test_active_overstock_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _ov(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        ov = [i for i in feed if i.contour == "overstock"]
        assert len(ov) == 1
        it = ov[0]
        assert it.item_key == "overstock_excess_stock:ozon:SKU1"
        assert it.what_happened == "Запаса хватит на ~200 дн. при текущем темпе"
        assert it.why_it_matters == "остаток 200 шт., темп 1 шт./дн"
        assert it.meaning == "избыточный запас — деньги заморожены в излишке"
        assert it.recommended_action == "Проверьте объём закупки относительно спроса (диагноз)"
        assert it.expected_effect == "раннее внимание может высвободить замороженные средства"
    _run(go())


# ── priority: high overstock sorts above a lower-priority active signal ──────

def test_high_overstock_sorts_above_lower():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                                   # active, priority None
        await _ov(db, uid, sku="H1", priority_level="high")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        ov_i = keys.index("overstock_excess_stock:ozon:H1")
        gr_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert ov_i < gr_i
    _run(go())


# ── overstock flows into Today / top_action ──────────────────────────────────

def test_overstock_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _ov(db, uid, sku="H1", priority_level="high")
        await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "overstock" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "overstock_excess_stock:ozon:H1"
    _run(go())


# ── resolved / dismissed overstock hidden by default ─────────────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _ov(db, uid, sku="R1", status="resolved")
        await _ov(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "overstock"] == []
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "overstock"]) == 2
    _run(go())
