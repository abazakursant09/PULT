"""
Decision Feed Phase 4.3a — surface SupplySignal (reader-only).

supply_signal is now a canonical feed engine (mirrors Money Leak 3.3a / Revenue 2.3a).
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
from models.supply_signal import SupplySignal
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


async def _supply(db, uid, *, sku="SKU1", priority_level="critical", status="active"):
    db.add(SupplySignal(
        user_id=uid, audit_id=str(uuid.uuid4()),
        signal_key="supply_stockout_risk",
        insight_key=f"supply_stockout_risk:ozon:{sku}", problem_type="supply_stockout_risk",
        category="supply", marketplace="ozon", sku=sku,
        what="Запаса хватает примерно на 2 дн.", why="остаток 20 шт., темп 10 шт./дн",
        meaning="риск обнуления остатка", what_to_do="Проверьте план пополнения (диагноз)",
        expected_effect="своевременное пополнение может предотвратить обнуление",
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

def test_supply_registered_in_engines():
    assert ("supply", SupplySignal, "supply_signal") in _ENGINES


# ── active supply surfaces with contour + doctrine fields ────────────────────

def test_active_supply_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _supply(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        sp = [i for i in feed if i.contour == "supply"]
        assert len(sp) == 1
        it = sp[0]
        assert it.item_key == "supply_stockout_risk:ozon:SKU1"
        assert it.what_happened == "Запаса хватает примерно на 2 дн."
        assert it.why_it_matters == "остаток 20 шт., темп 10 шт./дн"
        assert it.meaning == "риск обнуления остатка"
        assert it.recommended_action == "Проверьте план пополнения (диагноз)"
        assert it.expected_effect == "своевременное пополнение может предотвратить обнуление"
    _run(go())


# ── priority: critical supply sorts above a lower-priority active signal ─────

def test_critical_supply_sorts_first():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                                   # active, priority None
        await _supply(db, uid, sku="C1", priority_level="critical")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        sp_i = keys.index("supply_stockout_risk:ozon:C1")
        gr_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert sp_i < gr_i
    _run(go())


# ── supply flows into Today / top_action ─────────────────────────────────────

def test_supply_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _supply(db, uid, sku="C1", priority_level="critical")
        await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "supply" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "supply_stockout_risk:ozon:C1"
    _run(go())


# ── resolved / dismissed supply hidden by default ────────────────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _supply(db, uid, sku="R1", status="resolved")
        await _supply(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "supply"] == []
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "supply"]) == 2
    _run(go())
