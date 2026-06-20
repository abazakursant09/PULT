"""
Memory OS Phase 1, Slice 2 — chain assignment on promotion.

A NEW promoted Decision starts a chain (decision_chain_id set, step_in_chain=0).
An EXISTING Decision (idempotent re-promotion) is returned unchanged — chain
fields never overwritten. Blocked promotions still create nothing. No memory
writes, no refuted/similarity/learning here.
"""
import ast
import asyncio
import inspect
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from services import insight_decision_bridge as bridge
from services.insight_decision_bridge import InsightPromotionDTO, promote_insight_to_decision


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _dto(**kw):
    return InsightPromotionDTO(
        insight_key=kw.pop("insight_key", "margin_crisis:wb:SKU1"),
        itype=kw.pop("itype", "margin_crisis"),
        marketplace=kw.pop("marketplace", "wb"),
        sku=kw.pop("sku", "SKU1"),
        problem=kw.pop("problem", "p"),
        is_demo=kw.pop("is_demo", False),
    )


async def _load(db, decision_id):
    return (await db.execute(select(Decision).where(Decision.id == decision_id))).scalar_one()


# ── new promotion starts a chain ─────────────────────────────────────────────

def test_new_promotion_sets_chain_id():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        row = await _load(db, r.decision_id)
        assert row.decision_chain_id is not None
        # valid uuid
        uuid.UUID(row.decision_chain_id)
    _run(go())


def test_new_promotion_step_zero():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        row = await _load(db, r.decision_id)
        assert row.step_in_chain == 0
    _run(go())


# ── idempotency: existing chain untouched ────────────────────────────────────

def test_second_promotion_returns_same_and_preserves_chain():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r1 = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        chain1 = (await _load(db, r1.decision_id)).decision_chain_id
        r2 = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        assert r2.created is False and r1.decision_id == r2.decision_id
        row = await _load(db, r1.decision_id)
        assert row.decision_chain_id == chain1   # not overwritten
        assert row.step_in_chain == 0
        n = len((await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all())
        assert n == 1
    _run(go())


def test_existing_nonzero_step_not_reset():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r1 = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        # simulate a later chain state (as a future slice would set it)
        row = await _load(db, r1.decision_id)
        row.decision_chain_id = "fixed-chain"
        row.step_in_chain = 2
        await db.commit()
        # re-promote same insight_key → must return existing untouched
        r2 = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        assert r2.decision_id == r1.decision_id and r2.created is False
        row2 = await _load(db, r1.decision_id)
        assert row2.decision_chain_id == "fixed-chain"
        assert row2.step_in_chain == 2
    _run(go())


# ── blocked promotions unchanged ─────────────────────────────────────────────

def test_blocked_promotions_create_nothing():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for dto, reason in [
            (_dto(is_demo=True), "demo"),
            (_dto(sku=None, insight_key="margin_crisis:wb:unknown"), "non_promotable_sku"),
        ]:
            r = await promote_insight_to_decision(db, user_id=uid, insight=dto)
            assert r.decision_id is None and r.reason == reason
        r = await promote_insight_to_decision(db, user_id="", insight=_dto())
        assert r.reason == "no_scope"
        assert (await db.execute(select(Decision))).scalars().first() is None
    _run(go())


# ── guard: no decision_memory coupling in this slice ─────────────────────────

def test_bridge_does_not_touch_decision_memory():
    tree = ast.parse(inspect.getsource(bridge))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert all("decision_memory" not in m for m in mods)
    assert "decision_memory" not in inspect.getsource(bridge)
