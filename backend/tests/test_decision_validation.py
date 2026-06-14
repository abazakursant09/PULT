"""
Decision validation — CLOSE side tests.

Closes measurement: realized read → observed delta vs metric direction →
confirmed | refuted | insufficient_data. Records observed post-window state
only; no attribution. metric_reader.read_metric monkeypatched (no network).
"""
import asyncio
import ast
import inspect
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from database import Base
import models  # registers spine + observation + decision_outcome
from models.observation import Observation
from models.decision_outcome import DecisionOutcome
from repositories import decision_outcome as outcome_repo
from services import decision_validation
from services.marketplace import metric_reader
from services.marketplace.metric_reader import MetricSample, MetricUnavailable

NOW = datetime(2026, 6, 14)


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


async def _baseline(db, uid, value, metric="revenue", marketplace="wb"):
    o = Observation(id=str(uuid.uuid4()), user_id=uid, entity_grain="listing",
                    entity_id="111", metric_name=metric, marketplace=marketplace,
                    value=value, unit="rub", observed_at=NOW, source="api")
    db.add(o)
    await db.flush()
    return o


async def _open(db, *, decision_id, metric="revenue", window=7, baseline_id=None):
    return await outcome_repo.create_still_open_outcome(
        db, decision_id=decision_id, metric_name=metric,
        expected_window_days=window, baseline_observation_id=baseline_id)


def _realized(value):
    async def _r(**kw):
        return MetricSample(value=value, unit="rub", observed_at=kw.get("now") or NOW,
                            source="api", quality="n=5")
    return _r


def _unavailable():
    async def _r(**kw):
        return MetricUnavailable(kw["metric_name"], "adapter_not_implemented")
    return _r


# 1 — favorable delta on higher_better → confirmed
def test_close_confirmed(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); did = str(uuid.uuid4())
        b = await _baseline(db, uid, 100.0)
        o = await _open(db, decision_id=did, baseline_id=b.id)
        r = await decision_validation.close_measurement(db, outcome=o, token="t", now=NOW)
        assert r.outcome_label == "confirmed"
        assert r.realized_delta == 50.0
        assert r.realized_observation_id is not None
        assert r.measured_at is not None
    _run(go())


# 2 — unfavorable delta on higher_better → refuted
def test_close_refuted(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(80.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); did = str(uuid.uuid4())
        b = await _baseline(db, uid, 100.0)
        o = await _open(db, decision_id=did, baseline_id=b.id)
        r = await decision_validation.close_measurement(db, outcome=o, token="t", now=NOW)
        assert r.outcome_label == "refuted"
        assert r.realized_delta == -20.0
    _run(go())


# 3 — lower_better metric: a DECREASE is favorable → confirmed
def test_direction_lower_better_confirmed(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(20.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); did = str(uuid.uuid4())
        b = await _baseline(db, uid, 30.0, metric="ad_cost_ratio")
        o = await _open(db, decision_id=did, metric="ad_cost_ratio", baseline_id=b.id)
        r = await decision_validation.close_measurement(db, outcome=o, token="t", now=NOW)
        assert r.outcome_label == "confirmed"
        assert r.realized_delta == -10.0
    _run(go())


# 4 — zero delta → not favorable → refuted (data exists, just no favorable move)
def test_zero_delta_is_refuted(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(100.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); did = str(uuid.uuid4())
        b = await _baseline(db, uid, 100.0)
        o = await _open(db, decision_id=did, baseline_id=b.id)
        r = await decision_validation.close_measurement(db, outcome=o, token="t", now=NOW)
        assert r.outcome_label == "refuted"
        assert r.realized_delta == 0.0
    _run(go())


# 5 — realized unreadable → insufficient_data, no fabricated delta
def test_realized_unavailable_insufficient(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _unavailable())

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); did = str(uuid.uuid4())
        b = await _baseline(db, uid, 100.0)
        o = await _open(db, decision_id=did, baseline_id=b.id)
        r = await decision_validation.close_measurement(db, outcome=o, token="t", now=NOW)
        assert r.outcome_label == "insufficient_data"
        assert r.realized_delta is None
        # only the baseline observation exists; no realized fabricated
        assert len((await db.execute(select(Observation))).scalars().all()) == 1
    _run(go())


# 6 — no baseline → insufficient_data, no read attempted
def test_missing_baseline_insufficient(monkeypatch):
    called = {"n": 0}

    async def _r(**kw):
        called["n"] += 1
        return MetricSample(value=1.0, unit="rub", observed_at=NOW, source="api")
    monkeypatch.setattr(metric_reader, "read_metric", _r)

    async def go():
        db = await _engine(); did = str(uuid.uuid4())
        o = await _open(db, decision_id=did, baseline_id=None)
        r = await decision_validation.close_measurement(db, outcome=o, token="t", now=NOW)
        assert r.outcome_label == "insufficient_data"
        assert called["n"] == 0  # no realized read attempted without baseline context
    _run(go())


# 7 — due selection: only elapsed windows
def test_select_due_outcomes(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        old = await _open(db, decision_id=str(uuid.uuid4()), window=7)
        old.created_at = NOW - timedelta(days=10)        # elapsed
        fresh = await _open(db, decision_id=str(uuid.uuid4()), window=7)
        fresh.created_at = NOW - timedelta(days=2)        # not yet due
        await db.flush()
        due = await decision_validation.select_due_outcomes(db, now=NOW)
        ids = {o.id for o in due}
        assert old.id in ids
        assert fresh.id not in ids
    _run(go())


# 8 — idempotent: a closed outcome is not re-closed / no new observation
def test_idempotent_already_closed(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); did = str(uuid.uuid4())
        b = await _baseline(db, uid, 100.0)
        o = await _open(db, decision_id=did, baseline_id=b.id)
        first = await decision_validation.close_measurement(db, outcome=o, token="t", now=NOW)
        assert first.outcome_label == "confirmed"
        obs_count = len((await db.execute(select(Observation))).scalars().all())
        second = await decision_validation.close_measurement(db, outcome=o, token="t", now=NOW)
        assert second.outcome_label == "confirmed"
        assert len((await db.execute(select(Observation))).scalars().all()) == obs_count
    _run(go())


# 9 — no attribution / learning imports
def test_no_attribution_or_learning_imports():
    tree = ast.parse(inspect.getsource(decision_validation))
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
        assert forbidden not in joined
