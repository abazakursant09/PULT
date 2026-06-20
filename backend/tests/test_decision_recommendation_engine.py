"""
Decision recommendation engine (Slice 6: rule-based) — tests.

Threshold rules over Slice 5 aggregation: high/low action success_rate, insight
refuted_rate, insufficient-data share. Empty → []. Deterministic, read-only.
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
from services import decision_recommendation_engine as eng


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


def _by_msg(recs):
    return {r["message"]: r for r in recs}


# ── action rules ─────────────────────────────────────────────────────────────

def test_high_success_rate_increase():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # set_price: 8 confirmed, 1 refuted → success 8/9 ≈ 0.889 > 0.7
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=8)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=1)
        await db.commit()
        recs = await eng.generate_recommendations(db, uid)
        m = _by_msg(recs)
        assert "increase usage of set_price" in m
        r = m["increase usage of set_price"]
        assert r["type"] == "action" and r["target"] == "set_price" and r["priority"] == "medium"
    _run(go())


def test_low_success_rate_reduce():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # update_card: 1 confirmed, 9 refuted → success 0.1 < 0.3
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="confirmed", n=1)
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="refuted", n=9)
        await db.commit()
        recs = await eng.generate_recommendations(db, uid)
        m = _by_msg(recs)
        assert "reduce usage of update_card" in m
        assert m["reduce usage of update_card"]["priority"] == "high"
    _run(go())


def test_mid_success_rate_no_action_rec():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # 1 confirmed, 1 refuted → 0.5, between thresholds → no action rec
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=1)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=1)
        await db.commit()
        recs = await eng.generate_recommendations(db, uid)
        assert all(r["type"] != "action" for r in recs)
    _run(go())


# ── insight rules ────────────────────────────────────────────────────────────

def test_high_refuted_rate_review():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # margin_crisis: 2 confirmed, 8 refuted → refuted_rate 0.8 > 0.5
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=2)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=8)
        await db.commit()
        m = _by_msg(await eng.generate_recommendations(db, uid))
        assert "review insight type margin_crisis" in m
        assert m["review insight type margin_crisis"]["priority"] == "high"
    _run(go())


def test_high_insufficient_share():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # seo_opportunity: 7 insufficient of 10 → 0.7 > 0.5
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="insufficient_data", n=7)
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="confirmed", n=3)
        await db.commit()
        m = _by_msg(await eng.generate_recommendations(db, uid))
        assert "insufficient data for seo_opportunity" in m
        assert m["insufficient data for seo_opportunity"]["priority"] == "low"
    _run(go())


# ── edge / determinism ───────────────────────────────────────────────────────

def test_empty_dataset_returns_empty():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert await eng.generate_recommendations(db, uid) == []
    _run(go())


def test_deterministic_output():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=8)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=2)
        await db.commit()
        r1 = await eng.generate_recommendations(db, uid)
        r2 = await eng.generate_recommendations(db, uid)
        assert r1 == r2
    _run(go())


def test_output_schema():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed", n=8)
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted", n=1)
        await db.commit()
        for r in await eng.generate_recommendations(db, uid):
            assert set(r.keys()) == {"type", "target", "message", "priority"}
            assert r["type"] in ("action", "insight")
            assert r["priority"] in ("low", "medium", "high")
    _run(go())


# ── write-free / no-ML guarantees ────────────────────────────────────────────

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
        await eng.generate_recommendations(db, uid)
        assert await _counts() == before
    _run(go())


def test_no_writes_or_ml_imports():
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
    for bad in ("sklearn", "numpy", "torch", "scheduler", "executor",
                "close_measurement", "decision_validation"):
        assert all(bad not in m for m in mods), f"engine must not import {bad}"
