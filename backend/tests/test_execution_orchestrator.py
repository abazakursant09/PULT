"""
Execution orchestrator (Slice 9: plan only) — tests.

Builds an inert execution-plan artifact from policy-allowed action_based
candidates: only allowed actions enter, blocked never appear, insight_based
excluded, priority high→low, deterministic. No execution, no writes.
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
from services import execution_orchestrator as orch


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


def _allowed(target, priority, *, kind="action_based", conf=0.8):
    return {"type": kind, "target": target, "candidate": f"do {target}",
            "reason": "r", "confidence": conf, "priority": priority}


def _patch_policy(monkeypatch, *, candidates, allowed, blocked):
    async def fake_cands(db, user_id):
        return candidates

    async def fake_policy(db, user_id, cands):
        return {"allowed_actions": allowed, "blocked_actions": blocked,
                "priority_ranking": allowed}

    monkeypatch.setattr(orch._cand, "generate_decision_candidates", fake_cands)
    monkeypatch.setattr(orch._policy, "apply_decision_policy", fake_policy)


# ── only allowed action_based enter; blocked + insight excluded ──────────────

def test_only_allowed_action_based_enter(monkeypatch):
    _patch_policy(
        monkeypatch,
        candidates=[1, 2, 3],  # only length matters for metadata
        allowed=[_allowed("set_price", "high"),
                 _allowed("margin_crisis", "low", kind="insight_based")],
        blocked=[_allowed("update_card", "medium")],
    )

    async def go():
        out = await orch.build_execution_plan(await _engine(), "u1")
        targets = [p["action_type"] for p in out["execution_plan"]]
        assert targets == ["set_price"]                  # insight excluded
        assert all(p["target"] != "update_card" for p in out["execution_plan"])  # blocked absent
        assert [b["target"] for b in out["blocked_actions"]] == ["update_card"]
        assert out["metadata"] == {"total_candidates": 3, "allowed_count": 1, "blocked_count": 1}
    _run(go())


# ── priority ordering high → low (stable) ────────────────────────────────────

def test_priority_ordering(monkeypatch):
    _patch_policy(
        monkeypatch,
        candidates=[1, 2, 3],
        allowed=[_allowed("update_card", "medium"),
                 _allowed("ad_thing", "low"),
                 _allowed("set_price", "high")],
        blocked=[],
    )

    async def go():
        out = await orch.build_execution_plan(await _engine(), "u1")
        assert [p["action_type"] for p in out["execution_plan"]] == ["set_price", "update_card", "ad_thing"]
        assert [p["priority"] for p in out["execution_plan"]] == ["high", "medium", "low"]
    _run(go())


def test_confidence_copied_not_recomputed(monkeypatch):
    _patch_policy(monkeypatch, candidates=[1],
                  allowed=[_allowed("set_price", "high", conf=0.6123)], blocked=[])

    async def go():
        out = await orch.build_execution_plan(await _engine(), "u1")
        assert out["execution_plan"][0]["confidence"] == 0.6123
    _run(go())


# ── empty / deterministic ────────────────────────────────────────────────────

def test_empty_plan(monkeypatch):
    _patch_policy(monkeypatch, candidates=[], allowed=[], blocked=[])

    async def go():
        out = await orch.build_execution_plan(await _engine(), "u1")
        assert out["execution_plan"] == []
        assert out["metadata"] == {"total_candidates": 0, "allowed_count": 0, "blocked_count": 0}
    _run(go())


def test_deterministic(monkeypatch):
    _patch_policy(monkeypatch, candidates=[1, 2],
                  allowed=[_allowed("set_price", "high"), _allowed("update_card", "medium")],
                  blocked=[])

    async def go():
        db = await _engine()
        a = await orch.build_execution_plan(db, "u1")
        b = await orch.build_execution_plan(db, "u1")
        assert a == b
    _run(go())


# ── integration through real Slice 7 + 8 ─────────────────────────────────────

def test_integration_real_pipeline():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # set_price 8/2 → success 0.8 → "scale set_price" candidate → allowed high
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=8)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=2)
        await db.commit()
        out = await orch.build_execution_plan(db, uid)
        targets = [p["action_type"] for p in out["execution_plan"]]
        assert "set_price" in targets
        assert out["metadata"]["allowed_count"] == len(out["execution_plan"])
    _run(go())


# ── no execution / no writes guards ──────────────────────────────────────────

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
        await orch.build_execution_plan(db, uid)
        assert await _counts() == before
    _run(go())


def test_no_forbidden_imports():
    src = inspect.getsource(orch)
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
        assert all(bad not in m for m in mods), f"orchestrator must not import {bad}"
