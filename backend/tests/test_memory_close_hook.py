"""
Memory OS Phase 1, Slice 4 — memory write hook on measurement close.

Every terminal outcome (confirmed/refuted/insufficient) appends ONE DecisionMemory
row after the close commits; still_open writes nothing; duplicates are not
created; a memory failure never breaks the close; a missing Decision is skipped.
Effect (measured) and estimate stay separate.
"""
import ast
import asyncio
import inspect
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from models.observation import Observation
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from repositories import decision_outcome as outcome_repo
from services.marketplace import credential_vault, metric_reader
from services.marketplace.metric_reader import MetricSample, MetricUnavailable
from services import measurement_close_bridge as cb
from services.measurement_close_bridge import close_due_measurements

NOW = datetime(2026, 6, 20)
PAST = NOW - timedelta(days=30)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, baseline=True, with_cred=True, window=7, created_at=PAST, pnl=12000.0):
    uid = str(uuid.uuid4())
    did = str(uuid.uuid4())
    db.add(Decision(id=did, user_id=uid, problem="p", status="open", action_key="set_price",
                    pnl_impact=pnl, insight_key=f"margin_crisis:wb:{did[:8]}",
                    decision_chain_id="chain-1", step_in_chain=0))
    if with_cred:
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                     status="connected", scopes=["prices"])
        db.add(conn)
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                             secret_enc=credential_vault.encrypt("tok")))
    bid = None
    if baseline:
        obs = Observation(id=str(uuid.uuid4()), user_id=uid, entity_grain="listing",
                          entity_id="OFF1", metric_name="revenue", marketplace="wb",
                          value=100.0, unit="rub", observed_at=PAST, source="api")
        db.add(obs); await db.flush(); bid = obs.id
    out = await outcome_repo.create_still_open_outcome(
        db, decision_id=did, metric_name="revenue", expected_window_days=window,
        baseline_observation_id=bid)
    out.created_at = created_at
    await db.commit()
    return did


def _realized(v):
    async def _r(**kw):
        return MetricSample(value=v, unit="rub", observed_at=kw.get("now") or NOW, source="api")
    return _r


def _unavailable():
    async def _r(**kw):
        return MetricUnavailable(kw["metric_name"], "adapter_not_implemented")
    return _r


async def _mem(db, did=None):
    q = select(DecisionMemory)
    if did:
        q = q.where(DecisionMemory.decision_id == did)
    return (await db.execute(q)).scalars().all()


# ── terminal outcomes write one row each ─────────────────────────────────────

def test_confirmed_writes_memory(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); did = await _seed(db)
        await close_due_measurements(db, now=NOW)
        rows = await _mem(db, did)
        assert len(rows) == 1
        assert rows[0].outcome == "confirmed"
        assert rows[0].effect_value == 50.0          # measured
        assert rows[0].estimate_value == 12000.0     # estimate, separate
    _run(go())


def test_refuted_writes_memory(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(80.0))

    async def go():
        db = await _engine(); did = await _seed(db)
        await close_due_measurements(db, now=NOW)
        rows = await _mem(db, did)
        assert len(rows) == 1 and rows[0].outcome == "refuted"
        assert rows[0].effect_value == -20.0
    _run(go())


def test_insufficient_writes_memory_null_effect():
    async def go():
        db = await _engine(); did = await _seed(db, baseline=False)  # null baseline → insufficient
        await close_due_measurements(db, now=NOW)
        rows = await _mem(db, did)
        assert len(rows) == 1 and rows[0].outcome == "insufficient"
        assert rows[0].effect_value is None
        assert rows[0].estimate_value == 12000.0
    _run(go())


# ── still_open / skip cases write nothing ────────────────────────────────────

def test_still_open_not_due_writes_nothing(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); await _seed(db, created_at=NOW)  # not due → stays still_open
        await close_due_measurements(db, now=NOW)
        assert await _mem(db) == []
    _run(go())


def test_missing_token_skip_writes_nothing(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); await _seed(db, with_cred=False)  # skipped, stays still_open
        await close_due_measurements(db, now=NOW)
        assert await _mem(db) == []
    _run(go())


# ── duplicate protection ─────────────────────────────────────────────────────

def test_duplicate_close_does_not_duplicate_memory(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def go():
        db = await _engine(); did = await _seed(db)
        await close_due_measurements(db, now=NOW)
        await close_due_measurements(db, now=NOW)  # re-run; outcome already closed
        rows = await _mem(db, did)
        assert len(rows) == 1
    _run(go())


# ── failure isolation ────────────────────────────────────────────────────────

def test_memory_failure_does_not_break_close(monkeypatch):
    monkeypatch.setattr(metric_reader, "read_metric", _realized(150.0))

    async def boom(*a, **k):
        raise RuntimeError("memory down")

    monkeypatch.setattr(cb, "record_decision_memory", boom)

    async def go():
        db = await _engine(); did = await _seed(db)
        s = await close_due_measurements(db, now=NOW)
        # close still succeeded despite memory failure
        assert s.confirmed == 1 and s.errors == 0
        row = await outcome_repo.get_by_decision_id(db, did)
        assert row.outcome_label == "confirmed"
        assert await _mem(db, did) == []
    _run(go())


def test_missing_decision_skips_memory(monkeypatch):
    # _record_memory_safe must skip when the Decision can't be loaded.
    async def go():
        db = await _engine()
        await cb._record_memory_safe(db, "no-such-decision", "confirmed")
        assert await _mem(db) == []
    _run(go())


# ── guards ───────────────────────────────────────────────────────────────────

def test_single_memory_call_site():
    src = inspect.getsource(cb)
    # Two write sites, both in the close bridge's own best-effort hooks:
    # the terminal outcome row + the L1 FOLLOWUP_CREATED event.
    assert src.count("record_decision_memory(") == 2


def test_no_learning_imports_added():
    tree = ast.parse(inspect.getsource(cb))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("decision_candidate_engine", "decision_policy_engine",
                "autonomy_scoring_engine", "insight_decision_bridge",
                "sklearn", "numpy", "torch"):
        assert all(bad not in m for m in mods), f"close bridge must not import {bad}"
    # no refuted-loop / step mutation language
    src = inspect.getsource(cb)
    assert "step_in_chain" not in src
    assert "used_actions" not in src
