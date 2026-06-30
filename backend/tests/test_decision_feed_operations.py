"""
Decision Feed A21 — surface OperationsSignal.

Reader-only: operations_signal is now one of the canonical feed engines. Proves it
surfaces (live), carries contour + doctrine fields, sorts by observed priority_level,
flows into Today/top_action, and that resolved/dismissed rows stay hidden by default.
No producer/scheduler/Today/API change is exercised here.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.operations_signal import OperationsSignal
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


async def _ops(db, uid, *, sku="LOW", status="active", priority_level="critical"):
    db.add(OperationsSignal(
        user_id=uid, signal_key="operations_low_stock",
        insight_key=f"operations_low_stock:ozon:{sku}", problem_type="low_stock",
        category="operations", marketplace="ozon", sku=sku,
        recommended_action_key=None,
        what="Остаток критически низкий", why="скоро закончится",
        meaning="теряются позиции", what_to_do="Пополнить остаток",
        expected_effect="риск out-of-stock снижается",
        priority_level=priority_level, status=status, created_at=T0))
    await db.flush()


async def _growth(db, uid, *, sku="G1"):
    # active, no priority_level → same lifecycle bucket as ops, lower secondary priority
    db.add(GrowthSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="growth_margin_expansion_candidate",
           problem_type="margin_expansion_candidate",
           insight_key=f"growth_margin_expansion_candidate:ozon:{sku}", marketplace="ozon", sku=sku,
           status="active", what="можно поднять цену", why="маржа", meaning="x",
           what_to_do="проверить", expected_effect="маржа", created_at=T0))
    await db.flush()


# ── registry membership ──────────────────────────────────────────────────────

def test_operations_registered_in_engines():
    assert ("operations", OperationsSignal, "operations_signal") in _ENGINES


# ── (1)(2)(3) active operations surfaces with contour + doctrine fields ───────

def test_active_operations_surfaces():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _ops(db, uid); await db.commit()
        feed = await build_feed(db, user_id=uid)
        ops = [i for i in feed if i.contour == "operations"]
        assert len(ops) == 1
        it = ops[0]
        assert it.item_key == "operations_low_stock:ozon:LOW"
        assert it.what_happened == "Остаток критически низкий"
        assert it.why_it_matters == "скоро закончится"
        assert it.meaning == "теряются позиции"
        assert it.recommended_action == "Пополнить остаток"
        assert it.expected_effect == "риск out-of-stock снижается"
    _run(go())


# ── (4) priority_level=critical sorts above a lower-priority active signal ────

def test_critical_sorts_above_lower_priority():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _growth(db, uid)                       # active, priority None
        await _ops(db, uid, priority_level="critical")  # active, critical
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        keys = [i.item_key for i in feed]
        ops_i = keys.index("operations_low_stock:ozon:LOW")
        gro_i = keys.index("growth_margin_expansion_candidate:ozon:G1")
        assert ops_i < gro_i        # critical operations ranks first within the active bucket
    _run(go())


# ── (5) operations flows into Today / top_action ─────────────────────────────

def test_operations_in_today_top_action():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _ops(db, uid); await db.commit()
        today = await build_today(db, user_id=uid)
        assert any(t.contour == "operations" for t in today)
        top = await top_action(db, user_id=uid)
        assert top is not None and top.item_key == "operations_low_stock:ozon:LOW"
        assert top.recommended_action == "Пополнить остаток"
    _run(go())


# ── (6) resolved / dismissed operations hidden by default ────────────────────

def test_resolved_dismissed_hidden():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _ops(db, uid, sku="R1", status="resolved")
        await _ops(db, uid, sku="D1", status="dismissed")
        await db.commit()
        feed = await build_feed(db, user_id=uid)
        assert [i for i in feed if i.contour == "operations"] == []
        # but included when explicitly requested
        feed_all = await build_feed(db, user_id=uid, include_resolved=True)
        assert len([i for i in feed_all if i.contour == "operations"]) == 2
    _run(go())
