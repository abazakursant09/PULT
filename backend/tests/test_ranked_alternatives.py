"""
Sprint L2.4 — ranked alternatives read model.

get_ranked_alternatives merges ranked order + reasons into one read-only list.
Margin no-history → static order + fallback reasons; with history → ranked order
+ historical reasons; all candidates preserved; non-margin → fallback reason;
malformed → []; user/context isolated; deterministic; no writes/execution.
"""
import ast
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
from services import ranked_alternatives
from services.ranked_alternatives import get_ranked_alternatives

CG = "wb|unknown|unknown|unknown"
IKEY = "margin_crisis:wb:SKU1"
STATIC = ["set_price", "reduce_discount", "stop_auto_promotion"]
NON_MARGIN = "No ranking available for this problem type. Using default action order."


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


async def _alts(db, uid, ikey=IKEY, cg=CG):
    return await get_ranked_alternatives(db, user_id=uid, insight_key=ikey, context_group=cg)


# ── margin no history ────────────────────────────────────────────────────────

def test_margin_no_history_static_with_fallback_reasons():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        alts = await _alts(db, uid)
        assert [a["action_key"] for a in alts] == STATIC
        assert [a["rank"] for a in alts] == [1, 2, 3]
        assert all(a["fallback"] is True for a in alts)
        assert all(a["reason"] == "Not enough history. Using default action order." for a in alts)
        # full contract keys present
        assert set(alts[0].keys()) == {"action_key", "rank", "reason", "fallback",
                                       "confirmed", "refuted", "sample", "confirmed_rate"}
    _run(go())


# ── margin with history ──────────────────────────────────────────────────────

def test_margin_with_history_ranked_with_reasons():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await _mem(db, uid, "set_price", "confirmed", 1)
        await _mem(db, uid, "set_price", "refuted", 3)
        await db.commit()
        alts = await _alts(db, uid)
        assert [a["action_key"] for a in alts] == ["reduce_discount", "set_price", "stop_auto_promotion"]
        top = alts[0]
        assert top["fallback"] is False
        assert top["reason"] == "4 of 5 similar cases confirmed profit improvement"
        assert top["confirmed"] == 4 and top["refuted"] == 1 and top["sample"] == 5
        assert top["confirmed_rate"] == 0.8
        assert alts[2]["fallback"] is True  # stop_auto_promotion no history
    _run(go())


def test_all_candidates_preserved():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "set_price", "confirmed", 3)
        await db.commit()
        alts = await _alts(db, uid)
        assert {a["action_key"] for a in alts} == set(STATIC) and len(alts) == 3
    _run(go())


# ── non-margin ───────────────────────────────────────────────────────────────

def test_non_margin_fallback_reason():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        alts = await get_ranked_alternatives(db, user_id=uid,
                                             insight_key="seo_opportunity:wb:SKU1", context_group=CG)
        assert [a["action_key"] for a in alts] == ["update_card"]
        assert alts[0]["reason"] == NON_MARGIN and alts[0]["fallback"] is True
    _run(go())


def test_malformed_insight_empty():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert await get_ranked_alternatives(db, user_id=uid, insight_key="", context_group=CG) == []
        assert await get_ranked_alternatives(db, user_id=uid,
                                             insight_key="low_stock:wb:SKU1", context_group=CG) == []
    _run(go())


# ── isolation / determinism ──────────────────────────────────────────────────

def test_context_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "stop_auto_promotion", "confirmed", 3,
                   context_group="ozon|unknown|unknown|unknown")
        await db.commit()
        assert [a["action_key"] for a in await _alts(db, uid)] == STATIC
    _run(go())


def test_user_isolation():
    async def go():
        db = await _engine(); a = str(uuid.uuid4()); b = str(uuid.uuid4())
        await _mem(db, b, "stop_auto_promotion", "confirmed", 3)
        await db.commit()
        assert [x["action_key"] for x in await _alts(db, a)] == STATIC
    _run(go())


def test_deterministic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 3)
        await _mem(db, uid, "set_price", "refuted", 3)
        await db.commit()
        assert await _alts(db, uid) == await _alts(db, uid)
    _run(go())


# ── read-only / no execution guard ───────────────────────────────────────────

def test_read_only_no_execution():
    src = inspect.getsource(ranked_alternatives)
    for bad in ("db.add", "db.commit", "db.flush", "promote_insight", "execute(",
                "record_decision_memory"):
        assert bad not in src
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("executor", "measurement_close_bridge", "refuted_loop"):
        assert all(bad not in m for m in mods), f"read model must not import {bad}"
