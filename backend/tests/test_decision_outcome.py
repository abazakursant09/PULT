"""
Decision Outcome foundation tests.

Validates persistence + label lifecycle + doctrine boundaries. Uses create_all
(not alembic) so it exercises the model directly. Imports the Decision spine via
the models package (present in the working tree).
"""
import asyncio
import ast
import inspect
import sys
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers User / Decision / Observation / spine tables
from models.decision import Decision
from models.observation import Observation
from models.decision_outcome import DecisionOutcome, DecisionOutcomeLabel, ALLOWED_LABELS
from repositories import decision_outcome as repo


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


async def _decision(db, uid):
    d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="margin low",
                 action_key="set_price", pnl_impact=1000.0)
    db.add(d)
    await db.flush()
    return d


async def _obs(db, uid, value):
    o = Observation(id=str(uuid.uuid4()), user_id=uid, entity_grain="listing",
                    entity_id="1", metric_name="revenue", marketplace="wb",
                    value=value, unit="rub", observed_at=datetime(2026, 6, 14), source="api")
    db.add(o)
    await db.flush()
    return o


# 1
def test_create_still_open():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        o = await repo.create_still_open_outcome(
            db, decision_id=d.id, metric_name="revenue", expected_window_days=7)
        assert o.outcome_label == "still_open"
        assert o.realized_delta is None
        assert o.realized_observation_id is None
    _run(go())


# 2
def test_unique_decision_id():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        await repo.create_still_open_outcome(db, decision_id=d.id, metric_name="revenue", expected_window_days=7)
        await db.commit()
        raised = False
        try:
            await repo.create_still_open_outcome(db, decision_id=d.id, metric_name="revenue", expected_window_days=7)
            await db.commit()
        except Exception:
            raised = True
        assert raised, "expected unique violation on second outcome for same decision"
    _run(go())


# 3
def test_reference_baseline_and_realized_observations():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        b = await _obs(db, uid, 100.0); r = await _obs(db, uid, 150.0)
        await repo.create_still_open_outcome(
            db, decision_id=d.id, metric_name="revenue", expected_window_days=7,
            baseline_observation_id=b.id)
        row = await repo.mark_confirmed(db, decision_id=d.id, realized_observation_id=r.id, realized_delta=50.0)
        assert row.baseline_observation_id == b.id
        assert row.realized_observation_id == r.id
    _run(go())


# 4
def test_mark_confirmed_with_delta():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await _obs(db, uid, 150.0)
        await repo.create_still_open_outcome(db, decision_id=d.id, metric_name="revenue", expected_window_days=7)
        row = await repo.mark_confirmed(db, decision_id=d.id, realized_observation_id=r.id, realized_delta=50.0)
        assert row.outcome_label == "confirmed"
        assert row.realized_delta == 50.0
        assert row.measured_at is not None
    _run(go())


# 5
def test_mark_refuted_with_delta():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await _obs(db, uid, 70.0)
        await repo.create_still_open_outcome(db, decision_id=d.id, metric_name="revenue", expected_window_days=7)
        row = await repo.mark_refuted(db, decision_id=d.id, realized_observation_id=r.id, realized_delta=-30.0)
        assert row.outcome_label == "refuted"
        assert row.realized_delta == -30.0
    _run(go())


# 6
def test_mark_not_taken_without_observations():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        await repo.create_still_open_outcome(db, decision_id=d.id, metric_name="revenue", expected_window_days=7)
        row = await repo.mark_not_taken(db, decision_id=d.id)
        assert row.outcome_label == "not_taken"
        assert row.realized_observation_id is None
        assert row.realized_delta is None
    _run(go())


# 7
def test_mark_insufficient_data_no_fabricated_delta():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        await repo.create_still_open_outcome(db, decision_id=d.id, metric_name="revenue", expected_window_days=7)
        row = await repo.mark_insufficient_data(db, decision_id=d.id)
        assert row.outcome_label == "insufficient_data"
        assert row.realized_delta is None
    _run(go())


# 8
def _imported_targets(mod) -> str:
    """All actual import targets (modules + from-imports), ignoring docstrings/comments."""
    tree = ast.parse(inspect.getsource(mod))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)
    return " ".join(names)


def test_no_marketplace_adapter_imports():
    forbidden = ("wb_client", "ozon_client", "executor", "metric_reader", "wb_metric_adapter")
    for mod in (repo, sys.modules["models.decision_outcome"]):
        imports = _imported_targets(mod)
        for f in forbidden:
            assert f not in imports, f"{mod.__name__} must not import {f}"


# 9
def test_no_marketplace_column():
    cols = {c.name for c in DecisionOutcome.__table__.columns}
    assert "marketplace" not in cols


# 10
def test_allowed_labels_only():
    assert ALLOWED_LABELS == {"still_open", "confirmed", "refuted", "not_taken", "insufficient_data"}
    assert {l.value for l in DecisionOutcomeLabel} == ALLOWED_LABELS


# 11 (guard) — realized_delta cannot be set without a realized observation
def test_confirmed_requires_realized_observation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        await repo.create_still_open_outcome(db, decision_id=d.id, metric_name="revenue", expected_window_days=7)
        raised = False
        try:
            await repo.mark_confirmed(db, decision_id=d.id, realized_observation_id=None, realized_delta=50.0)
        except ValueError:
            raised = True
        assert raised
    _run(go())
