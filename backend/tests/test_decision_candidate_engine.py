"""
Decision candidate engine (Slice 7: proposals only) — tests.

Threshold rules over Slice 5 aggregation produce candidates (NOT decisions):
high/low action success, insight refuted / insufficient share. Empty → [].
Deterministic, read-only, never creates a Decision or calls the executor.
"""
import ast
import asyncio
import inspect
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers all tables
from models.decision import Decision
from models.decision_outcome import DecisionOutcome
from services import decision_candidate_engine as eng


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, action_key, itype, label, n=1):
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action_key, insight_key=f"{itype}:wb:{did[:8]}"))
        db.add(DecisionOutcome(decision_id=did, metric_name="revenue",
                               expected_window_days=7, outcome_label=label))
    await db.flush()


def _by_cand(cands):
    return {c["candidate"]: c for c in cands}


# ── action-based ─────────────────────────────────────────────────────────────

def test_high_success_scale():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=8)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=2)
        await db.commit()
        m = _by_cand(await eng.generate_decision_candidates(db, uid))
        c = m["scale usage of set_price"]
        assert c["type"] == "action_based" and c["target"] == "set_price"
        assert c["confidence"] == 0.8   # success_rate 8/10
    _run(go())


def test_low_success_reduce():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="confirmed", n=1)
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="refuted", n=9)
        await db.commit()
        m = _by_cand(await eng.generate_decision_candidates(db, uid))
        c = m["reduce or review usage of update_card"]
        assert c["type"] == "action_based"
        assert c["confidence"] == 0.9   # 1 - 0.1
    _run(go())


def test_mid_success_no_candidate():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=1)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=1)
        await db.commit()
        assert all(c["type"] != "action_based"
                   for c in await eng.generate_decision_candidates(db, uid))
    _run(go())


# ── insight-based ────────────────────────────────────────────────────────────

def test_high_refuted_revise():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=2)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=8)
        await db.commit()
        m = _by_cand(await eng.generate_decision_candidates(db, uid))
        c = m["revise insight logic for margin_crisis"]
        assert c["type"] == "insight_based" and c["confidence"] == 0.8
    _run(go())


def test_high_insufficient_improve_data():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="insufficient_data", n=7)
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="confirmed", n=3)
        await db.commit()
        m = _by_cand(await eng.generate_decision_candidates(db, uid))
        c = m["improve data collection for seo_opportunity"]
        assert c["type"] == "insight_based" and c["confidence"] == 0.7
    _run(go())


# ── edge / determinism / schema ──────────────────────────────────────────────

def test_empty_returns_empty():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert await eng.generate_decision_candidates(db, uid) == []
    _run(go())


def test_deterministic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=8)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=2)
        await db.commit()
        a = await eng.generate_decision_candidates(db, uid)
        b = await eng.generate_decision_candidates(db, uid)
        assert a == b
    _run(go())


def test_output_schema():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=8)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=1)
        await db.commit()
        for c in await eng.generate_decision_candidates(db, uid):
            assert set(c.keys()) == {"type", "target", "candidate", "reason", "confidence"}
            assert c["type"] in ("action_based", "insight_based")
            assert isinstance(c["confidence"], float) and 0.0 <= c["confidence"] <= 1.0
    _run(go())


# ── no writes / no execution guarantees ──────────────────────────────────────

def test_no_write_side_effects():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=4)
        await db.commit()

        async def _counts():
            d = (await db.execute(select(func.count()).select_from(Decision))).scalar()
            o = (await db.execute(select(func.count()).select_from(DecisionOutcome))).scalar()
            return d, o

        before = await _counts()
        await eng.generate_decision_candidates(db, uid)
        assert await _counts() == before
    _run(go())


def test_no_forbidden_imports():
    src = inspect.getsource(eng)
    for forbidden in ("db.add", "db.commit", "db.flush", ".delete(", "session.add"):
        assert forbidden not in src
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("executor", "scheduler", "sklearn", "numpy", "torch",
                "insight_decision_bridge", "decision_apply", "close_measurement"):
        assert all(bad not in m for m in mods), f"candidate engine must not import {bad}"
