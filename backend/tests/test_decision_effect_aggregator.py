"""
Decision effect aggregator (Slice 5: read-only) — tests.

Counts, grouping by action_key / insight_type, decided-set rates, insufficient
handling, empty-dataset safety, no double counting, and a write-free guarantee
(row counts unchanged + no write calls in service source).
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
from services import decision_effect_aggregator as agg


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, action_key, itype, label, mp="wb"):
    did = str(uuid.uuid4())
    db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                    action_key=action_key, insight_key=f"{itype}:{mp}:{did[:8]}"))
    db.add(DecisionOutcome(decision_id=did, metric_name="revenue",
                           expected_window_days=7, outcome_label=label))
    await db.flush()


# ── decision summary ─────────────────────────────────────────────────────────

def test_decision_summary_counts():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed")
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed")
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted")
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="insufficient_data")
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="still_open")
        await db.commit()
        s = await agg.get_decision_summary(db, uid)
        assert s == {"total": 5, "confirmed": 2, "refuted": 1,
                     "insufficient_data": 1, "still_open": 1}
    _run(go())


def test_summary_scoped_to_user():
    async def go():
        db = await _engine(); a = str(uuid.uuid4()); b = str(uuid.uuid4())
        await _seed(db, a, action_key="set_price", itype="margin_crisis", label="confirmed")
        await _seed(db, b, action_key="set_price", itype="margin_crisis", label="refuted")
        await db.commit()
        assert (await agg.get_decision_summary(db, a))["total"] == 1
        assert (await agg.get_decision_summary(db, a))["confirmed"] == 1
    _run(go())


# ── action performance ───────────────────────────────────────────────────────

def test_action_performance_grouping_and_rates():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # set_price: 2 confirmed, 1 refuted, 1 insufficient → decided=3, success=2/3
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed")
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed")
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted")
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="insufficient_data")
        # update_card: 1 insufficient only → decided=0 → success_rate None
        await _seed(db, uid, action_key="update_card", itype="seo_opportunity", label="insufficient_data")
        await db.commit()
        perf = {p["action_key"]: p for p in await agg.get_action_performance(db, uid)}

        sp = perf["set_price"]
        assert sp["total"] == 4 and sp["confirmed"] == 2 and sp["refuted"] == 1
        assert sp["insufficient_data"] == 1
        assert sp["success_rate"] == round(2 / 3, 4)
        assert sp["insufficient_rate"] == round(1 / 4, 4)

        uc = perf["update_card"]
        assert uc["total"] == 1 and uc["success_rate"] is None
        assert uc["insufficient_rate"] == 1.0
    _run(go())


def test_action_none_grouped_as_unmapped():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, action_key=None, itype="low_stock", label="insufficient_data")
        await db.commit()
        perf = {p["action_key"]: p for p in await agg.get_action_performance(db, uid)}
        assert "unmapped" in perf and perf["unmapped"]["total"] == 1
    _run(go())


# ── insight effectiveness ────────────────────────────────────────────────────

def test_insight_effectiveness_rates():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # margin_crisis: 3 confirmed, 1 refuted → decided=4, success=3/4, refuted=1/4
        for _ in range(3):
            await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed")
        await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="refuted")
        await db.commit()
        eff = {e["insight_type"]: e for e in await agg.get_insight_effectiveness(db, uid)}
        mc = eff["margin_crisis"]
        assert mc["total"] == 4
        assert mc["success_rate"] == 0.75 and mc["refuted_rate"] == 0.25
    _run(go())


def test_insight_unknown_when_no_key():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key="set_price", insight_key=None))
        db.add(DecisionOutcome(decision_id=did, metric_name="revenue",
                               expected_window_days=7, outcome_label="confirmed"))
        await db.commit()
        eff = {e["insight_type"]: e for e in await agg.get_insight_effectiveness(db, uid)}
        assert "unknown" in eff
    _run(go())


# ── edge cases ───────────────────────────────────────────────────────────────

def test_empty_dataset_safe():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert (await agg.get_decision_summary(db, uid)) == {
            "total": 0, "confirmed": 0, "refuted": 0,
            "insufficient_data": 0, "still_open": 0}
        assert (await agg.get_action_performance(db, uid)) == []
        assert (await agg.get_insight_effectiveness(db, uid)) == []
    _run(go())


def test_no_double_counting():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for _ in range(6):
            await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed")
        await db.commit()
        s = await agg.get_decision_summary(db, uid)
        perf_total = sum(p["total"] for p in await agg.get_action_performance(db, uid))
        eff_total = sum(e["total"] for e in await agg.get_insight_effectiveness(db, uid))
        assert s["total"] == perf_total == eff_total == 6
    _run(go())


# ── write-free guarantees ────────────────────────────────────────────────────

def test_no_write_side_effects():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for _ in range(3):
            await _seed(db, uid, action_key="set_price", itype="margin_crisis", label="confirmed")
        await db.commit()

        async def _counts():
            d = (await db.execute(select(func.count()).select_from(Decision))).scalar()
            o = (await db.execute(select(func.count()).select_from(DecisionOutcome))).scalar()
            return d, o

        before = await _counts()
        await agg.get_decision_summary(db, uid)
        await agg.get_action_performance(db, uid)
        await agg.get_insight_effectiveness(db, uid)
        after = await _counts()
        assert before == after
    _run(go())


def test_service_has_no_write_calls():
    src = inspect.getsource(agg)
    for forbidden in ("db.add", "db.commit", "db.flush", ".delete(", "db.execute(update",
                      "session.add"):
        assert forbidden not in src, f"aggregator must not write ({forbidden})"
    # No ML / prediction / scheduler imports.
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("executor", "scheduler", "sklearn", "numpy", "torch",
                "close_measurement", "decision_validation"):
        assert all(bad not in m for m in mods), f"aggregator must not import {bad}"
