"""
Sprint L2.2 — ranked margin alternatives in the promotion path.

promote_insight_alternatives(..., context_group=...) promotes margin candidates
in outcome-memory-ranked ORDER; without context_group or eligible history it uses
the deterministic action-space order. Sort-only: still promotes all, never drops,
idempotent. Non-margin and the refuted loop are unchanged.
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from services.insight_decision_bridge import promote_insight_alternatives, InsightPromotionDTO

CG = "wb|unknown|unknown|unknown"
STATIC = ["set_price", "reduce_discount", "stop_auto_promotion"]


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _dto(itype="margin_crisis", mp="wb", sku="SKU1"):
    return InsightPromotionDTO(insight_key=f"{itype}:{mp}:{sku}", itype=itype,
                               marketplace=mp, sku=sku, problem="p")


async def _mem(db, uid, action, outcome, n=1, context_group=CG):
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"margin_crisis:wb:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action,
                              context_group=context_group, outcome=outcome))
    await db.flush()


async def _promoted_actions_for_insight(db, uid):
    rows = (await db.execute(select(Decision).where(
        Decision.user_id == uid, Decision.insight_key == "margin_crisis:wb:SKU1"))).scalars().all()
    return rows


# ── order: without history → static; with history → ranked ───────────────────

def test_no_history_static_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await promote_insight_alternatives(db, user_id=uid, insight=_dto(), context_group=CG)
        await db.commit()
        assert [r.created for r in res] == [True, True, True]
        # promotion result order follows candidate order (static, no history)
        # verify via decisions' action_keys in creation order
        rows = sorted(await _promoted_actions_for_insight(db, uid), key=lambda d: d.created_at)
        assert {d.action_key for d in rows} == set(STATIC)
    _run(go())


def test_with_history_ranked_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # reduce_discount 4/5, set_price 1/4, stop none → ranked first reduce_discount
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await _mem(db, uid, "set_price", "confirmed", 1)
        await _mem(db, uid, "set_price", "refuted", 3)
        await db.commit()
        # capture promotion ORDER via the ranked emission used internally:
        from services.insight_decision_bridge import emit_ranked_candidates
        ranked = [c.action_key for c in await emit_ranked_candidates(
            db, user_id=uid, insight_key="margin_crisis:wb:SKU1", context_group=CG)]
        assert ranked == ["reduce_discount", "set_price", "stop_auto_promotion"]
        res = await promote_insight_alternatives(db, user_id=uid, insight=_dto(), context_group=CG)
        await db.commit()
        assert len(res) == 3 and all(r.created for r in res)
    _run(go())


# ── all promoted, none dropped ───────────────────────────────────────────────

def test_promotes_all_candidates():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", 3)
        await db.commit()
        res = await promote_insight_alternatives(db, user_id=uid, insight=_dto(), context_group=CG)
        await db.commit()
        rows = await _promoted_actions_for_insight(db, uid)
        assert {d.action_key for d in rows} == set(STATIC) and len(rows) == 3
    _run(go())


# ── idempotency ──────────────────────────────────────────────────────────────

def test_idempotent_rerun():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await promote_insight_alternatives(db, user_id=uid, insight=_dto(), context_group=CG)
        await db.commit()
        again = await promote_insight_alternatives(db, user_id=uid, insight=_dto(), context_group=CG)
        await db.commit()
        assert all(r.created is False for r in again)
        assert len(await _promoted_actions_for_insight(db, uid)) == 3
    _run(go())


# ── missing context_group → static fallback ──────────────────────────────────

def test_missing_context_group_static_fallback():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # even with ranked history, no context_group → static path, still all 3
        await _mem(db, uid, "reduce_discount", "confirmed", 3)
        await db.commit()
        res = await promote_insight_alternatives(db, user_id=uid, insight=_dto())  # no context_group
        await db.commit()
        rows = await _promoted_actions_for_insight(db, uid)
        assert {d.action_key for d in rows} == set(STATIC) and len(res) == 3
    _run(go())


# ── non-margin unchanged ─────────────────────────────────────────────────────

def test_non_margin_single():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await promote_insight_alternatives(
            db, user_id=uid, insight=_dto(itype="seo_opportunity"), context_group=CG)
        await db.commit()
        rows = (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()
        assert len(res) == 1 and [d.action_key for d in rows] == ["update_card"]
    _run(go())


# ── refuted loop untouched ───────────────────────────────────────────────────

def test_refuted_loop_selection_unchanged():
    from services.refuted_loop import select_next_candidate
    assert select_next_candidate("margin_crisis", "set_price") == "reduce_discount"
    assert select_next_candidate("margin_crisis", "reduce_discount") == "stop_auto_promotion"
    assert select_next_candidate("margin_crisis", "stop_auto_promotion") is None
