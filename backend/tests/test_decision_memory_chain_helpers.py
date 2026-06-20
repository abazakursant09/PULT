"""
Memory OS Phase 1, Slice 5 — read-only chain helpers.

get_used_actions / get_current_step / get_chain_status compute over
decision_memory only. Pure reads: no insert/update/delete/flush/commit. No
refuted-loop, no candidate creation, no learning, no similarity.
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
from models.decision_memory import DecisionMemory
from services import decision_memory as dm
from services.decision_memory import get_used_actions, get_current_step, get_chain_status


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _row(db, chain, step, action, outcome):
    db.add(DecisionMemory(decision_id=str(uuid.uuid4()), decision_chain_id=chain,
                          step_in_chain=step, action_type=action, outcome=outcome))
    await db.flush()


# ── used_actions ─────────────────────────────────────────────────────────────

def test_used_actions_unique():
    async def go():
        db = await _engine()
        await _row(db, "c1", 0, "set_price", "refuted")
        await _row(db, "c1", 1, "reduce_discount", "refuted")
        await _row(db, "c1", 1, "set_price", "confirmed")  # dup action_type
        assert await get_used_actions(db, "c1") == {"set_price", "reduce_discount"}
    _run(go())


def test_used_actions_ignores_null_empty():
    async def go():
        db = await _engine()
        await _row(db, "c1", 0, None, "insufficient")
        await _row(db, "c1", 0, "", "insufficient")
        await _row(db, "c1", 1, "set_price", "refuted")
        assert await get_used_actions(db, "c1") == {"set_price"}
    _run(go())


def test_used_actions_empty_chain():
    async def go():
        db = await _engine()
        assert await get_used_actions(db, "nope") == set()
        assert await get_used_actions(db, None) == set()
    _run(go())


# ── current_step ─────────────────────────────────────────────────────────────

def test_current_step_max():
    async def go():
        db = await _engine()
        await _row(db, "c1", 0, "a", "refuted")
        await _row(db, "c1", 2, "b", "refuted")
        await _row(db, "c1", 1, "c", "refuted")
        assert await get_current_step(db, "c1") == 2
    _run(go())


def test_current_step_empty_zero():
    async def go():
        db = await _engine()
        assert await get_current_step(db, "nope") == 0
        assert await get_current_step(db, None) == 0
    _run(go())


# ── chain_status ─────────────────────────────────────────────────────────────

def test_status_confirmed():
    async def go():
        db = await _engine()
        await _row(db, "c1", 0, "set_price", "refuted")
        await _row(db, "c1", 1, "reduce_discount", "confirmed")
        assert await get_chain_status(db, "c1") == "confirmed"
    _run(go())


def test_status_stopped_only_when_max_step_ge3_and_refuted():
    async def go():
        db = await _engine()
        await _row(db, "c1", 0, "a", "refuted")
        await _row(db, "c1", 1, "b", "refuted")
        await _row(db, "c1", 2, "c", "refuted")
        await _row(db, "c1", 3, "d", "refuted")   # max step 3, refuted → stopped
        assert await get_chain_status(db, "c1") == "stopped"
    _run(go())


def test_status_not_stopped_when_max_step_insufficient():
    async def go():
        db = await _engine()
        await _row(db, "c1", 0, "a", "refuted")
        await _row(db, "c1", 1, "b", "refuted")
        await _row(db, "c1", 2, "c", "refuted")
        await _row(db, "c1", 3, "d", "insufficient")  # max step 3 but NOT refuted → open
        assert await get_chain_status(db, "c1") == "open"
    _run(go())


def test_status_open_when_only_insufficient():
    async def go():
        db = await _engine()
        await _row(db, "c1", 0, "a", "insufficient")
        await _row(db, "c1", 0, "a", "insufficient")
        assert await get_chain_status(db, "c1") == "open"
    _run(go())


def test_status_open_empty_chain():
    async def go():
        db = await _engine()
        assert await get_chain_status(db, "nope") == "open"
        assert await get_chain_status(db, None) == "open"
    _run(go())


def test_status_open_below_three():
    async def go():
        db = await _engine()
        await _row(db, "c1", 0, "a", "refuted")
        await _row(db, "c1", 1, "b", "refuted")
        assert await get_chain_status(db, "c1") == "open"
    _run(go())


# ── read-only + guards ───────────────────────────────────────────────────────

def test_helpers_are_read_only_source():
    src = inspect.getsource(dm.get_used_actions) + inspect.getsource(dm.get_current_step) \
        + inspect.getsource(dm.get_chain_status)
    for forbidden in ("db.add", "db.flush", "db.commit", ".delete(", ".merge(", "update("):
        assert forbidden not in src, f"chain helper must be read-only ({forbidden})"


def test_no_forbidden_imports():
    tree = ast.parse(inspect.getsource(dm))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("decision_candidate_engine", "decision_policy_engine",
                "autonomy_scoring_engine", "measurement_close_bridge",
                "insight_decision_bridge", "sklearn", "numpy", "torch"):
        assert all(bad not in m for m in mods), f"memory service must not import {bad}"
