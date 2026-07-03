"""
Decision Feed Phase 1.6 — surface PricingSignal.

Reader-only: pricing_signal is now one of the canonical feed engines (mirrors the
operations A21 pattern). Proves it surfaces (live), carries contour + doctrine fields,
sorts by observed priority_level, flows into Today/top_action, and that
resolved/dismissed rows stay hidden by default.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.pricing_signal import PricingSignal
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


async def _pricing(db, uid, *, sku="LOSS", status="active", priority_level="critical"):
    db.add(PricingSignal(
        user_id=uid, signal_key="pricing_negative_margin",
        insight_key=f"pricing_negative_margin:ozon:{sku}", problem_type="negative_margin",
        category="pricing", marketplace="ozon", sku=sku,
        what="Товар продаётся в убыток", why="net_profit < 0",
        meaning="каждая продажа теряет деньги", what_to_do="Пересмотреть цену",
        expected_effect="маржа может перестать быть отрицательной",
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

def test_pricing_registered_in_engines():
    assert ("pricing", PricingSignal, "pricing_signal") in _ENGINES


# ── active pricing surfaces with contour + doctrine fields ───────────────────

def test_active_pricing_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _pricing(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        pr = [i for i in feed if i.contour == "pricing"]
        assert len(pr) == 1
        it = pr[0]
        assert it.item_key == "pricing_negative_margin:ozon:LOSS"
        assert it.what_happened == "Товар продаётся в убыток"
        assert it.why_it_matters == "net_profit < 0"
        assert it.meaning == "каждая продажа теряет деньги"
        assert it.recommended_action == "Пересмотреть цену"
        assert it.expected_effect == "маржа может перестать быть отрицательной"
    _run(go())


# ── priority: critical pricing sorts above a lower-priority active signal ─────

def test_critical_sorts_above_lower_priority():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                          # active, priority None
        await _pricing(db, uid, priority_level="critical")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        pr_i = keys.index("pricing_negative_margin:ozon:LOSS")
        gr_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert pr_i < gr_i                              # critical pricing ranks first
    _run(go())


# ── pricing flows into Today / top_action ────────────────────────────────────

def test_pricing_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _pricing(db, uid); await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "pricing" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "pricing_negative_margin:ozon:LOSS"
        assert top.recommended_action == "Пересмотреть цену"
    _run(go())


# ── resolved / dismissed pricing hidden by default ───────────────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _pricing(db, uid, sku="R1", status="resolved")
        await _pricing(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "pricing"] == []
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "pricing"]) == 2
    _run(go())
