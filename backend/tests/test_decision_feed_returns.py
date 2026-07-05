"""
Decision Feed Phase R3a — surface ReturnsSignal (reader-only).

returns_signal is now a canonical feed engine (mirrors Overstock 7.3a / Price Erosion 8.3a).
Reader-only: rows are seeded directly (the producer is DISABLED and NOT run). Proves it surfaces
(live), carries contour + doctrine fields, sorts by priority_level, flows into Today/top_action,
and that resolved/dismissed rows stay hidden by default. The 7 existing live contour engines stay
independent. Frequency-only doctrine intact (no money-loss text); ingestion table
(imported_return_rows) is never a feed engine.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.returns_signal import ReturnsSignal
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


async def _rt(db, uid, *, sku="SKU1", priority_level="high", status="active"):
    db.add(ReturnsSignal(
        user_id=uid, audit_id=str(uuid.uuid4()),
        signal_key="returns_return_rate_rise",
        insight_key=f"returns_return_rate_rise:ozon:{sku}", problem_type="return_rate_rise",
        category="returns", marketplace="ozon", sku=sku,
        what="Частота возвратов выросла на 200% относительно собственного прежнего темпа",
        why="доля возвратов на продажу выросла с 0.1 до 0.3",
        meaning="рост частоты возвратов — ранний сигнал проблем с качеством",
        what_to_do="Проверьте причины возвратов (диагноз)",
        expected_effect="раннее внимание может остановить эрозию маржи и репутации",
        priority_level=priority_level, effect_type="return_rate_rise", status=status, created_at=T0))
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

def test_returns_registered_in_engines():
    assert ("returns", ReturnsSignal, "returns_signal") in _ENGINES


# ── active returns surfaces with contour + doctrine fields ───────────────────

def test_active_returns_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _rt(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        rt = [i for i in feed if i.contour == "returns"]
        assert len(rt) == 1
        it = rt[0]
        assert it.item_key == "returns_return_rate_rise:ozon:SKU1"
        assert it.what_happened == "Частота возвратов выросла на 200% относительно собственного прежнего темпа"
        assert it.why_it_matters == "доля возвратов на продажу выросла с 0.1 до 0.3"
        assert it.recommended_action == "Проверьте причины возвратов (диагноз)"
        # frequency-only doctrine: no ruble money-loss claim
        assert "руб" not in it.what_happened.lower()
    _run(go())


# ── priority: high returns sorts above a lower-priority active signal ────────

def test_high_returns_sorts_above_lower():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                                   # active, priority None
        await _rt(db, uid, sku="H1", priority_level="high")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        rt_i = keys.index("returns_return_rate_rise:ozon:H1")
        gr_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert rt_i < gr_i
    _run(go())


# ── returns flows into Today / top_action ────────────────────────────────────

def test_returns_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _rt(db, uid, sku="H1", priority_level="high")
        await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "returns" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "returns_return_rate_rise:ozon:H1"
    _run(go())


# ── resolved / dismissed returns hidden by default ───────────────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _rt(db, uid, sku="R1", status="resolved")
        await _rt(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "returns"] == []
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "returns"]) == 2
    _run(go())


# ── ingestion table is never a feed engine (producer enabled in R3b) ─────────

def test_ingestion_table_not_a_feed_engine():
    from services.advisory_runtime.registry import ADVISORY_PRODUCERS
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["returns"].enabled is True         # producer live (R3b)
    tables = {t for (_c, _m, t) in _ENGINES}
    assert "imported_return_rows" not in tables      # ingestion table never surfaces directly
    assert "returns_signal" in tables                # only the diagnosis signal is wired


def test_seven_live_contours_still_independent():
    tables = {t for (_c, _m, t) in _ENGINES}
    for wired in ("revenue_signal", "money_leak_signal", "supply_signal", "rating_signal",
                  "review_velocity_signal", "overstock_signal", "price_erosion_signal"):
        assert wired in tables, f"missing {wired}"
