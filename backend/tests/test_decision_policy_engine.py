"""
Decision policy engine (Slice 8: governance) — tests.

Deterministic precedence: confidence filter → insufficient-override → margin
proxy block → priority (pricing HIGH > content MEDIUM > insight LOW). Read-only,
no writes, no execution.
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
from services import decision_policy_engine as pol


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


def _cand(kind, target, conf):
    return {"type": kind, "target": target, "candidate": f"do {target}",
            "reason": "r", "confidence": conf}


# ── priority: pricing first ──────────────────────────────────────────────────

def test_pricing_prioritized_over_content_and_insight():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())   # empty DB → no margin/insuf rules
        cands = [
            _cand("insight_based", "margin_crisis", 0.9),
            _cand("action_based", "update_card", 0.9),
            _cand("action_based", "set_price", 0.9),
        ]
        out = await pol.apply_decision_policy(db, uid, cands)
        ranking = out["priority_ranking"]
        assert [r["target"] for r in ranking] == ["set_price", "update_card", "margin_crisis"]
        assert [r["priority"] for r in ranking] == ["high", "medium", "low"]
        assert out["blocked_actions"] == []
    _run(go())


# ── confidence filter ────────────────────────────────────────────────────────

def test_low_confidence_filtered_out():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        cands = [_cand("action_based", "set_price", 0.25),  # below 0.3 → dropped
                 _cand("action_based", "update_card", 0.5)]
        out = await pol.apply_decision_policy(db, uid, cands)
        targets = [r["target"] for r in out["priority_ranking"]]
        assert "set_price" not in targets
        assert all(b["target"] != "set_price" for b in out["blocked_actions"])
        assert "update_card" in targets
    _run(go())


# ── margin proxy blocks non-pricing ──────────────────────────────────────────

def test_margin_low_blocks_non_pricing():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # margin proxy = confirmed/decided = 1/10 = 0.1 < 0.4 → margin_low
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=1)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=9)
        await db.commit()
        cands = [_cand("action_based", "set_price", 0.9),
                 _cand("action_based", "update_card", 0.9),
                 _cand("insight_based", "margin_crisis", 0.9)]
        out = await pol.apply_decision_policy(db, uid, cands)
        allowed = {a["target"] for a in out["allowed_actions"]}
        blocked = {b["target"] for b in out["blocked_actions"]}
        assert allowed == {"set_price"}
        assert blocked == {"update_card", "margin_crisis"}
        assert all(b["blocked_reason"] == "margin_proxy_below_threshold"
                   for b in out["blocked_actions"])
    _run(go())


# ── insufficient override beats everything ───────────────────────────────────

def test_insufficient_override_returns_only_diagnostics():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # insufficient ratio = 8/10 = 0.8 > 0.7
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="insufficient_data", n=8)
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="confirmed", n=2)
        await db.commit()
        cands = [_cand("action_based", "set_price", 0.9),   # pricing, still suppressed
                 _cand("insight_based", "seo_opportunity", 0.9)]
        out = await pol.apply_decision_policy(db, uid, cands)
        assert out["allowed_actions"] == []
        assert out["blocked_actions"] == []
        assert [r["target"] for r in out["priority_ranking"]] == ["seo_opportunity"]
    _run(go())


# ── edge / determinism ───────────────────────────────────────────────────────

def test_empty_candidates_safe():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        out = await pol.apply_decision_policy(db, uid, [])
        assert out == {"allowed_actions": [], "blocked_actions": [], "priority_ranking": []}
    _run(go())


def test_deterministic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        cands = [_cand("action_based", "set_price", 0.9),
                 _cand("action_based", "update_card", 0.9)]
        a = await pol.apply_decision_policy(db, uid, cands)
        b = await pol.apply_decision_policy(db, uid, cands)
        assert a == b
    _run(go())


def test_input_not_mutated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        cands = [_cand("action_based", "set_price", 0.9)]
        await pol.apply_decision_policy(db, uid, cands)
        assert "priority" not in cands[0]  # original dict untouched
    _run(go())


# ── no writes / no ML ────────────────────────────────────────────────────────

def test_no_write_side_effects():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=3)
        await db.commit()

        async def _counts():
            d = (await db.execute(select(func.count()).select_from(Decision))).scalar()
            o = (await db.execute(select(func.count()).select_from(DecisionOutcome))).scalar()
            return d, o

        before = await _counts()
        await pol.apply_decision_policy(db, uid, [_cand("action_based", "set_price", 0.9)])
        assert await _counts() == before
    _run(go())


def test_no_forbidden_imports():
    src = inspect.getsource(pol)
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
        assert all(bad not in m for m in mods), f"policy engine must not import {bad}"
