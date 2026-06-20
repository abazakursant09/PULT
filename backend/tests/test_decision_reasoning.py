"""
Sprint L2.3 — decision reason engine.

explain() / explain_ranking() turn rank_actions stats into structured
DecisionReason dicts: eligible → confirmed reason, fallback=false; below
min_sample / no history → fallback=true with a default-order reason. Pure,
deterministic, no writes, no execution-layer dependency.
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
from services import decision_reasoning
from services.decision_reasoning import explain, explain_ranking
from services.outcome_memory_ranking import rank_actions

CG = "wb|unknown|unknown|unknown"
ACTIONS = ["set_price", "reduce_discount", "stop_auto_promotion"]


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _mem(db, uid, action, outcome, n=1):
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"margin_crisis:wb:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action,
                              context_group=CG, outcome=outcome))
    await db.flush()


# ── unit: explain() on stat dicts ────────────────────────────────────────────

def test_explain_with_history():
    r = explain({"action_key": "reduce_discount", "rank": 1, "confirmed": 4,
                 "refuted": 1, "sample": 5, "confirmed_rate": 0.8, "eligible": True})
    assert r == {
        "action_key": "reduce_discount", "rank": 1, "confirmed": 4, "refuted": 1,
        "sample": 5, "confirmed_rate": 0.8,
        "reason": "4 of 5 similar cases confirmed profit improvement", "fallback": False}


def test_explain_no_history_fallback():
    r = explain({"action_key": "stop_auto_promotion", "rank": 3, "confirmed": 0,
                 "refuted": 0, "sample": 0, "confirmed_rate": None, "eligible": False})
    assert r["fallback"] is True
    assert r["reason"] == "Not enough history. Using default action order."


def test_explain_insufficient_fallback():
    r = explain({"action_key": "set_price", "rank": 2, "confirmed": 1, "refuted": 1,
                 "sample": 2, "confirmed_rate": 0.5, "eligible": False})
    assert r["fallback"] is True
    assert "2 cases" in r["reason"] and "default action order" in r["reason"]


def test_deterministic():
    stat = {"action_key": "x", "rank": 1, "confirmed": 3, "refuted": 0,
            "sample": 3, "confirmed_rate": 1.0, "eligible": True}
    assert explain(stat) == explain(stat)


# ── integration with rank_actions ────────────────────────────────────────────

def test_explain_ranking_over_real_stats():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await _mem(db, uid, "set_price", "confirmed", 1)
        await _mem(db, uid, "set_price", "refuted", 3)
        await db.commit()
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        reasons = explain_ranking(ranked)
        assert len(reasons) == 3
        by = {r["action_key"]: r for r in reasons}
        assert by["reduce_discount"]["fallback"] is False
        assert by["reduce_discount"]["reason"] == "4 of 5 similar cases confirmed profit improvement"
        assert by["stop_auto_promotion"]["fallback"] is True  # no history
        # order preserved from ranking
        assert [r["action_key"] for r in reasons] == [r["action_key"] for r in ranked]
    _run(go())


def test_all_fallback_when_no_history():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        ranked = await rank_actions(db, user_id=uid, problem_type="margin_crisis",
                                    context_group=CG, available_actions=ACTIONS)
        reasons = explain_ranking(ranked)
        assert all(r["fallback"] is True for r in reasons)
        assert all(r["reason"] == "Not enough history. Using default action order." for r in reasons)
    _run(go())


# ── purity / decoupling guards ───────────────────────────────────────────────

def test_no_writes_no_execution_dependency():
    src = inspect.getsource(decision_reasoning)
    for bad in ("db.add", "db.commit", "db.flush", "AsyncSession", "random", "sklearn"):
        assert bad not in src
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("executor", "measurement_close_bridge", "insight_decision_bridge",
                "outcome_memory_ranking", "refuted_loop", "sqlalchemy", "numpy", "torch"):
        assert all(bad not in m for m in mods), f"reason engine must not import {bad}"
