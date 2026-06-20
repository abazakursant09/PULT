"""
Decision measurement — OPEN side tests.

Opens measurement (baseline + still_open). Never closes: no realized capture, no
label transition past still_open, no causal claim. metric_reader.read_metric is
monkeypatched (no network); build_observation stays real.
"""
import asyncio
import ast
import inspect
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from database import Base
import models  # registers spine + observation + decision_outcome
from models.decision import Decision
from models.observation import Observation
from models.decision_outcome import DecisionOutcome
from services import decision_measurement
from services.marketplace import metric_reader
from services.marketplace.metric_reader import MetricSample, MetricUnavailable


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


async def _decision(db, uid, action_key="set_price"):
    d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="margin low",
                 action_key=action_key, pnl_impact=1000.0)
    db.add(d)
    await db.flush()
    return d


def _fake_sample(value=123.0):
    async def _r(**kw):
        return MetricSample(value=value, unit="rub", observed_at=kw.get("now") or datetime(2026, 6, 14),
                            source="api", quality="n=3")
    return _r


def _fake_unavailable(reason="adapter_not_implemented"):
    async def _r(**kw):
        return MetricUnavailable(kw["metric_name"], reason)
    return _r


# 1 — available metric → baseline Observation captured + still_open opened
def test_open_captures_baseline(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _fake_sample(500.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        out = await decision_measurement.open_measurement(
            db, decision=d, entity_id="111", marketplace="wb", window_days=7, token="t")
        assert out.outcome_label == "still_open"
        assert out.metric_name == "revenue"          # set_price → revenue
        assert out.baseline_observation_id is not None
        obs = (await db.execute(select(Observation).where(
            Observation.id == out.baseline_observation_id))).scalar_one()
        assert obs.value == 500.0
        assert obs.metric_name == "revenue"
    _run(go())


# 2 — metric unavailable → still_open opened with NO baseline (honest, not fabricated)
def test_open_without_baseline_when_unavailable(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _fake_unavailable())

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        out = await decision_measurement.open_measurement(
            db, decision=d, entity_id="111", marketplace="wb", window_days=7, token="t")
        assert out.outcome_label == "still_open"
        assert out.baseline_observation_id is None
        # no observation fabricated
        n = (await db.execute(select(Observation))).scalars().all()
        assert len(n) == 0
    _run(go())


# 3 — unbound action → no outcome opened
def test_unbound_action_opens_nothing(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _fake_sample())

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _decision(db, uid, action_key="some_unmapped_action")
        out = await decision_measurement.open_measurement(
            db, decision=d, entity_id="1", marketplace="wb", window_days=7, token="t")
        assert out is None
        assert (await db.execute(select(DecisionOutcome))).scalars().all() == []
    _run(go())


# 4 — idempotent: second call returns existing, no duplicate
def test_idempotent(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _fake_sample())

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        a = await decision_measurement.open_measurement(
            db, decision=d, entity_id="1", marketplace="wb", window_days=7, token="t")
        b = await decision_measurement.open_measurement(
            db, decision=d, entity_id="1", marketplace="wb", window_days=7, token="t")
        assert a.id == b.id
        assert len((await db.execute(select(DecisionOutcome))).scalars().all()) == 1
    _run(go())


# 5 — opens but does NOT close: no realized, no label past still_open
def test_opens_but_does_not_close(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _fake_sample())

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        out = await decision_measurement.open_measurement(
            db, decision=d, entity_id="1", marketplace="wb", window_days=7, token="t")
        assert out.outcome_label == "still_open"
        assert out.realized_observation_id is None
        assert out.realized_delta is None
        assert out.measured_at is None
        # Decision.status untouched (no mutation)
        assert d.status == "open"
    _run(go())


# 6 — no attribution / learning imports in the open-measurement service
def test_no_attribution_or_learning_imports():
    tree = ast.parse(inspect.getsource(decision_measurement))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)
    joined = " ".join(names)
    for forbidden in ("attribution", "learning", "operator_decision"):
        assert forbidden not in joined, f"open-measurement must not import {forbidden}"
