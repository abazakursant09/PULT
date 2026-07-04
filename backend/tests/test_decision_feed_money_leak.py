"""
Decision Feed Phase 3.3a — surface MoneyLeakSignal (reader-only).

money_leak_signal is now a canonical feed engine (mirrors Revenue 2.3a / pricing 1.6).
Reader-only: rows are seeded directly (the producer is DISABLED and NOT run). Proves it
surfaces (live), carries contour + doctrine fields, sorts by priority_level, flows into
Today/top_action, and that resolved/dismissed rows stay hidden by default.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.money_leak_signal import MoneyLeakSignal
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


async def _money_leak(db, uid, *, sku="SKU1", problem_type="commission_drift",
                      priority_level="high", status="active"):
    db.add(MoneyLeakSignal(
        user_id=uid, audit_id=str(uuid.uuid4()),
        signal_key=f"money_leak_{problem_type}",
        insight_key=f"money_leak_{problem_type}:ozon:{sku}", problem_type=problem_type,
        category="money_leak", marketplace="ozon", sku=sku,
        what="Комиссия съедает всё большую долю выручки", why="доля растёт 3 окна подряд",
        meaning="тихая утечка маржи", what_to_do="Проверьте тариф (диагноз)",
        expected_effect="раннее внимание может остановить размывание маржи",
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

def test_money_leak_registered_in_engines():
    assert ("money_leak", MoneyLeakSignal, "money_leak_signal") in _ENGINES


# ── active money_leak surfaces with contour + doctrine fields ────────────────

def test_active_money_leak_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _money_leak(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        ml = [i for i in feed if i.contour == "money_leak"]
        assert len(ml) == 1
        it = ml[0]
        assert it.item_key == "money_leak_commission_drift:ozon:SKU1"
        assert it.what_happened == "Комиссия съедает всё большую долю выручки"
        assert it.why_it_matters == "доля растёт 3 окна подряд"
        assert it.meaning == "тихая утечка маржи"
        assert it.recommended_action == "Проверьте тариф (диагноз)"
        assert it.expected_effect == "раннее внимание может остановить размывание маржи"
    _run(go())


# ── priority: high money_leak sorts above a lower-priority active signal ──────

def test_high_money_leak_sorts_above_lower():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                                   # active, priority None
        await _money_leak(db, uid, sku="H1", priority_level="high")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        ml_i = keys.index("money_leak_commission_drift:ozon:H1")
        gr_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert ml_i < gr_i
    _run(go())


# ── money_leak flows into Today / top_action ─────────────────────────────────

def test_money_leak_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _money_leak(db, uid, sku="H1", priority_level="high")
        await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "money_leak" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "money_leak_commission_drift:ozon:H1"
    _run(go())


# ── resolved / dismissed money_leak hidden by default ────────────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _money_leak(db, uid, sku="R1", status="resolved")
        await _money_leak(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "money_leak"] == []
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "money_leak"]) == 2
    _run(go())
