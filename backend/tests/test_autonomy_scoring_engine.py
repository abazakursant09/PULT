"""
Autonomy scoring engine (Slice 11: gradation only) — tests.

Per-item autonomy level 0/1/2. Level 0 = blocked or conf<0.5; Level 2 = conf>0.8
+ success_rate>0.7 + safe action (update_card); else Level 1. Read-only, no
execution, deterministic.
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
from services import autonomy_scoring_engine as aut


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, action_key, label, n=1, itype="x"):
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action_key, insight_key=f"{itype}:wb:{did[:8]}"))
        db.add(DecisionOutcome(decision_id=did, metric_name="revenue",
                               expected_window_days=7, outcome_label=label))
    await db.flush()


def _item(action_type, conf, **extra):
    return {"action_type": action_type, "target": action_type,
            "priority": "high", "confidence": conf, **extra}


# ── Level 2 — safe-auto candidate ────────────────────────────────────────────

def test_safe_action_high_conf_high_success_level2():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="update_card", label="confirmed", n=8)
        await _seed(db, uid, action_key="update_card", label="refuted", n=2)  # success 0.8 > 0.7
        await db.commit()
        r = await aut.compute_autonomy_level(db, uid, _item("update_card", 0.9))
        assert r["autonomy_level"] == 2
        assert r["risk_score"] == 0.1
    _run(go())


def test_only_update_card_eligible_for_level2():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # set_price high conf + high success → still NOT level 2 (not in safe subset)
        await _seed(db, uid, action_key="set_price", label="confirmed", n=8)
        await _seed(db, uid, action_key="set_price", label="refuted", n=2)
        await db.commit()
        r = await aut.compute_autonomy_level(db, uid, _item("set_price", 0.9))
        assert r["autonomy_level"] == 1
    _run(go())


def test_safe_action_high_conf_but_low_success_not_level2():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="update_card", label="confirmed", n=2)
        await _seed(db, uid, action_key="update_card", label="refuted", n=8)  # success 0.2
        await db.commit()
        r = await aut.compute_autonomy_level(db, uid, _item("update_card", 0.9))
        assert r["autonomy_level"] == 1
    _run(go())


# ── Level 0 ──────────────────────────────────────────────────────────────────

def test_low_confidence_level0():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await aut.compute_autonomy_level(db, uid, _item("update_card", 0.4))
        assert r["autonomy_level"] == 0 and "low confidence" in r["reason"]
    _run(go())


def test_policy_blocked_level0():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await aut.compute_autonomy_level(
            db, uid, _item("update_card", 0.95, blocked_reason="margin_proxy_below_threshold"))
        assert r["autonomy_level"] == 0 and r["reason"] == "blocked by policy"
    _run(go())


# ── Level 1 ──────────────────────────────────────────────────────────────────

def test_medium_confidence_level1():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await aut.compute_autonomy_level(db, uid, _item("update_card", 0.65))
        assert r["autonomy_level"] == 1
    _run(go())


def test_high_conf_no_history_level1():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # update_card high conf but NO outcomes → success_rate None → not level2
        r = await aut.compute_autonomy_level(db, uid, _item("update_card", 0.9))
        assert r["autonomy_level"] == 1
    _run(go())


# ── output / determinism ─────────────────────────────────────────────────────

def test_output_schema_and_risk():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await aut.compute_autonomy_level(db, uid, _item("update_card", 0.6))
        assert set(r.keys()) == {"autonomy_level", "reason", "risk_score"}
        assert r["autonomy_level"] in (0, 1, 2)
        assert isinstance(r["risk_score"], float) and 0.0 <= r["risk_score"] <= 1.0
        assert r["risk_score"] == 0.4
    _run(go())


def test_deterministic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="update_card", label="confirmed", n=8)
        await _seed(db, uid, action_key="update_card", label="refuted", n=2)
        await db.commit()
        a = await aut.compute_autonomy_level(db, uid, _item("update_card", 0.9))
        b = await aut.compute_autonomy_level(db, uid, _item("update_card", 0.9))
        assert a == b
    _run(go())


# ── no execution / no writes guards ──────────────────────────────────────────

def test_no_write_side_effects():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="update_card", label="confirmed", n=4)
        await db.commit()

        async def _counts():
            d = (await db.execute(select(func.count()).select_from(Decision))).scalar()
            o = (await db.execute(select(func.count()).select_from(DecisionOutcome))).scalar()
            return d, o

        before = await _counts()
        await aut.compute_autonomy_level(db, uid, _item("update_card", 0.9))
        assert await _counts() == before
    _run(go())


def test_no_forbidden_imports():
    src = inspect.getsource(aut)
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
                "insight_decision_bridge", "decision_apply", "close_measurement",
                "execution_measurement_bridge"):
        assert all(bad not in m for m in mods), f"autonomy engine must not import {bad}"
