"""
Decision apply (Slice B) tests.

Applies a Decision through executor.execute with explicit caller overrides.
executor.execute is monkeypatched (no connection/network). Verifies call shape,
failure modes, idempotency default, no status mutation, and import boundaries.
"""
import asyncio
import ast
import inspect
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from database import Base
import models  # registers tables
from models.decision import Decision
from services import decision_apply
from services.marketplace import executor
from services.marketplace.executor import ExecutionResult


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


class _Spy:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def __call__(self, **kw):
        self.calls.append(kw)
        return self.result


def _ok(log_id="log-1", status="success"):
    return ExecutionResult(log_id=log_id, status=status, action_type="set_price", marketplace="wb")


# 1–5 — call shape
def test_loads_and_calls_executor(monkeypatch):
    spy = _Spy(_ok()); monkeypatch.setattr(executor, "execute", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"offer_id": "1", "price": 100})
        assert r.ok and r.execution_log_id == "log-1" and r.status == "success"
        assert len(spy.calls) == 1
        kw = spy.calls[0]
        assert kw["action_type"] == "set_price"               # 2
        assert kw["payload"] == {"offer_id": "1", "price": 100}  # 3
        assert kw["decision_id"] == d.id                      # 4
        assert kw["idempotency_key"] == f"decision:{d.id}"    # 5
    _run(go())


def test_explicit_idempotency_key_preserved(monkeypatch):
    spy = _Spy(_ok()); monkeypatch.setattr(executor, "execute", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"x": 1}, idempotency_key="custom-key")
        assert spy.calls[0]["idempotency_key"] == "custom-key"
    _run(go())


# 6 — missing decision
def test_missing_decision(monkeypatch):
    spy = _Spy(_ok()); monkeypatch.setattr(executor, "execute", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id="nope", overrides={"x": 1})
        assert not r.ok and r.reason == "decision_not_found"
        assert spy.calls == []  # executor not called
    _run(go())


# 7 — missing action_key
def test_missing_action_key(monkeypatch):
    spy = _Spy(_ok()); monkeypatch.setattr(executor, "execute", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _decision(db, uid, action_key=None)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"x": 1})
        assert not r.ok and r.reason == "missing_action_key"
        assert spy.calls == []
    _run(go())


# 8 — missing overrides
def test_missing_overrides(monkeypatch):
    spy = _Spy(_ok()); monkeypatch.setattr(executor, "execute", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={})
        assert not r.ok and r.reason == "missing_overrides"
        assert spy.calls == []
    _run(go())


# 9 — executor rejection, no Decision.status mutation
def test_executor_rejection_no_status_mutation(monkeypatch):
    rej = ExecutionResult(log_id="log-r", status="rejected", action_type="set_price",
                          marketplace="wb", error={"code": "MISSING_SCOPE", "detail": "x"})
    monkeypatch.setattr(executor, "execute", _Spy(rej))

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"x": 1})
        assert not r.ok
        assert r.execution_log_id == "log-r"
        assert r.reason == "MISSING_SCOPE"
        reloaded = (await db.execute(select(Decision).where(Decision.id == d.id))).scalar_one()
        assert reloaded.status == "open"   # unchanged
    _run(go())


# 10 — dry_run passes through
def test_dry_run_passthrough(monkeypatch):
    spy = _Spy(ExecutionResult(log_id=None, status="dry_run_ok", action_type="set_price", marketplace="wb"))
    monkeypatch.setattr(executor, "execute", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"x": 1}, dry_run=True)
        assert spy.calls[0]["dry_run"] is True
        assert r.ok and r.status == "dry_run_ok"
    _run(go())


# 11/12/13 — import boundaries
def _imports(mod) -> str:
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


def test_no_attribution_learning_validation_or_adapter_imports():
    # Slice C legitimately imports decision_measurement (the open hook). It must
    # still NOT import the close side, attribution, learning, or adapters.
    imports = _imports(decision_apply)
    for forbidden in ("decision_validation", "attribution", "learning", "operator_decision",
                      "wb_client", "ozon_client", "wb_metric_adapter"):
        assert forbidden not in imports, f"decision_apply must not import {forbidden}"


# ── Slice C: measurement open hook ───────────────────────────────────────────
class _Outcome:
    def __init__(self, id): self.id = id


class _OpenSpy:
    def __init__(self, outcome=None, raises=False):
        self.outcome = outcome
        self.raises = raises
        self.calls = []

    async def __call__(self, db, **kw):
        self.calls.append(kw)
        if self.raises:
            raise RuntimeError("boom")
        return self.outcome


def test_measure_opens_after_real_success(monkeypatch):
    monkeypatch.setattr(executor, "execute", _Spy(_ok(status="success")))
    spy = _OpenSpy(_Outcome("outc-1")); monkeypatch.setattr(decision_apply.decision_measurement, "open_measurement", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"offer_id": "1", "price": 100},
            measure=True, entity_id="1", token="tkn", window_days=14)
        assert r.ok and r.decision_outcome_id == "outc-1"
        assert len(spy.calls) == 1
        assert spy.calls[0]["entity_id"] == "1"
        assert spy.calls[0]["marketplace"] == "wb"   # from executor result
        assert spy.calls[0]["window_days"] == 14
    _run(go())


def test_dry_run_does_not_open(monkeypatch):
    monkeypatch.setattr(executor, "execute",
                        _Spy(ExecutionResult(log_id=None, status="dry_run_ok", action_type="set_price", marketplace="wb")))
    spy = _OpenSpy(_Outcome("x")); monkeypatch.setattr(decision_apply.decision_measurement, "open_measurement", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"x": 1},
            measure=True, entity_id="1", token="tkn", dry_run=True)
        assert r.status == "dry_run_ok"
        assert r.decision_outcome_id is None
        assert spy.calls == []
    _run(go())


def test_rejection_does_not_open(monkeypatch):
    rej = ExecutionResult(log_id="l", status="rejected", action_type="set_price",
                          marketplace="wb", error={"code": "MISSING_SCOPE"})
    monkeypatch.setattr(executor, "execute", _Spy(rej))
    spy = _OpenSpy(_Outcome("x")); monkeypatch.setattr(decision_apply.decision_measurement, "open_measurement", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"x": 1},
            measure=True, entity_id="1", token="tkn")
        assert not r.ok and r.decision_outcome_id is None
        assert spy.calls == []
    _run(go())


def test_measure_default_off(monkeypatch):
    monkeypatch.setattr(executor, "execute", _Spy(_ok()))
    spy = _OpenSpy(_Outcome("x")); monkeypatch.setattr(decision_apply.decision_measurement, "open_measurement", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"x": 1})  # measure defaults False
        assert r.ok and r.decision_outcome_id is None
        assert spy.calls == []
    _run(go())


def test_open_failure_does_not_break_apply(monkeypatch):
    monkeypatch.setattr(executor, "execute", _Spy(_ok(status="success")))
    spy = _OpenSpy(raises=True); monkeypatch.setattr(decision_apply.decision_measurement, "open_measurement", spy)

    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); d = await _decision(db, uid)
        r = await decision_apply.apply_decision(
            db=db, user_id=uid, decision_id=d.id, overrides={"x": 1},
            measure=True, entity_id="1", token="tkn")
        assert r.ok                      # apply still succeeds
        assert r.status == "success"
        assert r.decision_outcome_id is None   # measurement swallowed
    _run(go())
