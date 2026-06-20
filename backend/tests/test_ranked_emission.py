"""
Sprint L2.1 — outcome ranking wired into margin alternatives emission.

emit_ranked_candidates sorts margin_crisis candidates by outcome memory (L2):
no/insufficient history → deterministic emit_candidates order; eligible history →
ranked order; never drops a candidate; non-margin unchanged; user/context isolated.
The pure emit_candidates is untouched.
"""
import asyncio
import inspect
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from services.insight_decision_bridge import emit_candidates, emit_ranked_candidates

CG = "wb|unknown|unknown|unknown"
IKEY = "margin_crisis:wb:SKU1"
STATIC = ["set_price", "reduce_discount", "stop_auto_promotion"]


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _mem(db, uid, action, outcome, n=1, context_group=CG):
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"margin_crisis:wb:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action,
                              context_group=context_group, outcome=outcome))
    await db.flush()


async def _order(db, uid, ikey=IKEY, cg=CG):
    cands = await emit_ranked_candidates(db, user_id=uid, insight_key=ikey, context_group=cg)
    return [c.action_key for c in cands]


# ── fallback (no / insufficient history) ─────────────────────────────────────

def test_no_history_static_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert await _order(db, uid) == STATIC
    _run(go())


def test_insufficient_history_static_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 2)  # < min_sample
        await db.commit()
        assert await _order(db, uid) == STATIC
    _run(go())


# ── eligible history → ranked order ──────────────────────────────────────────

def test_eligible_history_ranked_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # reduce_discount 4/5 (0.8), set_price 1/4 (0.25), stop none
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await _mem(db, uid, "set_price", "confirmed", 1)
        await _mem(db, uid, "set_price", "refuted", 3)
        await db.commit()
        assert await _order(db, uid) == ["reduce_discount", "set_price", "stop_auto_promotion"]
    _run(go())


# ── never drops candidates ───────────────────────────────────────────────────

def test_ranking_never_drops():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", 3)
        await db.commit()
        cands = await emit_ranked_candidates(db, user_id=uid, insight_key=IKEY, context_group=CG)
        assert {c.action_key for c in cands} == set(STATIC) and len(cands) == 3
    _run(go())


# ── non-margin unchanged ─────────────────────────────────────────────────────

def test_non_margin_unchanged():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        cands = await emit_ranked_candidates(db, user_id=uid,
                                             insight_key="seo_opportunity:wb:SKU1", context_group=CG)
        assert [c.action_key for c in cands] == ["update_card"]  # emit_candidates order, ranking skipped
    _run(go())


def test_empty_problem_no_candidates():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert await emit_ranked_candidates(db, user_id=uid,
                                            insight_key="low_stock:wb:SKU1", context_group=CG) == []
    _run(go())


# ── isolation ────────────────────────────────────────────────────────────────

def test_context_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "stop_auto_promotion", "confirmed", 3,
                   context_group="ozon|unknown|unknown|unknown")
        await db.commit()
        assert await _order(db, uid) == STATIC  # other context doesn't reorder
    _run(go())


def test_user_isolation():
    async def go():
        db = await _engine(); a = str(uuid.uuid4()); b = str(uuid.uuid4())
        await _mem(db, b, "stop_auto_promotion", "confirmed", 3)  # other user
        await db.commit()
        assert await _order(db, a) == STATIC
    _run(go())


# ── determinism ──────────────────────────────────────────────────────────────

def test_deterministic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 3)
        await _mem(db, uid, "set_price", "refuted", 3)
        await db.commit()
        assert await _order(db, uid) == await _order(db, uid)
    _run(go())


# ── emit_candidates purity unchanged ─────────────────────────────────────────

def test_emit_candidates_still_pure():
    sig = inspect.signature(emit_candidates)
    assert list(sig.parameters) == ["insight_key"]          # no db param
    assert not inspect.iscoroutinefunction(emit_candidates)  # still sync/pure
    # still returns the static order
    assert [c.action_key for c in emit_candidates(IKEY)] == STATIC
