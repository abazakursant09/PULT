"""
Advisory Runtime A6 — scheduled runner shell (run_due_producers + scheduler tick).

Shadow-safe: with every ProducerSpec enabled=False (today) the tick is a no-op and
creates no AdvisoryRun. Covers: disabled no-op, enabled+due run, cadence gate,
slot_budget cap, error isolation, scheduler tick swallow, and the import guard.
"""
import ast
import asyncio
import inspect
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.advisory_run import AdvisoryRun

from services.advisory_runtime.runtime import (
    AdvisoryRuntime, RuntimeTickResult, RuntimeContext, ProducerResult,
)
from services.advisory_runtime.registry import ProducerSpec, ADVISORY_PRODUCERS
import services.advisory_runtime.registry as registry_mod
import services.advisory_runtime.runtime as runtime_mod

NOW = datetime(2026, 6, 29, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _active(db, uid):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                              date="2026-06-20", sku="SKU1", revenue=100.0, net_profit=10.0))
    await db.commit()


async def _ok_producer(ctx: RuntimeContext) -> ProducerResult:
    return ProducerResult(ok=True, stats={"did": "nothing"})


async def _boom_producer(ctx: RuntimeContext) -> ProducerResult:
    raise RuntimeError("kaboom")


def _spec(key, run, *, cadence=3600, enabled=True):
    return ProducerSpec(key=key, run=run, cadence_seconds=cadence, enabled=enabled)


async def _runs(db):
    return (await db.execute(select(AdvisoryRun))).scalars().all()


# ── (1) no enabled specs → no-op, no rows ────────────────────────────────────

def test_no_enabled_specs_no_op(monkeypatch):
    # monkeypatch a disabled-only registry (independent of the real registry, where
    # legal is now enabled) to lock the no-op path.
    monkeypatch.setattr(registry_mod, "ADVISORY_PRODUCERS",
                        (_spec("t_off", _ok_producer, enabled=False),))

    async def go():
        db = await _db(); await _active(db, str(uuid.uuid4()))
        res = await AdvisoryRuntime().run_due_producers(db, now=NOW)
        assert (res.ran, res.skipped, res.errors) == (0, 0, 0)
        assert await _runs(db) == []
    _run(go())


# ── (2) enabled + due → run_one called, AdvisoryRun written ──────────────────

def test_enabled_due_runs(monkeypatch):
    monkeypatch.setattr(registry_mod, "ADVISORY_PRODUCERS", (_spec("t_ok", _ok_producer),))

    async def go():
        db = await _db(); uid = str(uuid.uuid4()); await _active(db, uid)
        res = await AdvisoryRuntime().run_due_producers(db, now=NOW)
        assert res.ran == 1 and res.errors == 0
        rows = await _runs(db)
        assert len(rows) == 1 and rows[0].status == "ok" and rows[0].producer_key == "t_ok"
        assert rows[0].triggered_by == "scheduled"
    _run(go())


# ── (3) cadence gate → recent ok run → skipped ───────────────────────────────

def test_cadence_gate_skips(monkeypatch):
    monkeypatch.setattr(registry_mod, "ADVISORY_PRODUCERS", (_spec("t_ok", _ok_producer),))

    async def go():
        db = await _db(); uid = str(uuid.uuid4()); await _active(db, uid)
        db.add(AdvisoryRun(run_id=str(uuid.uuid4()), user_id=uid, producer_key="t_ok",
                           started_at=NOW - timedelta(minutes=5), status="ok"))
        await db.commit()
        res = await AdvisoryRuntime().run_due_producers(db, now=NOW)
        assert res.ran == 0 and res.skipped == 1
        assert len(await _runs(db)) == 1            # no new run created
    _run(go())


# ── (4) slot_budget caps the number run ──────────────────────────────────────

def test_slot_budget_caps(monkeypatch):
    monkeypatch.setattr(registry_mod, "ADVISORY_PRODUCERS", (_spec("t_ok", _ok_producer),))

    async def go():
        db = await _db()
        for _ in range(3):
            await _active(db, str(uuid.uuid4()))
        res = await AdvisoryRuntime().run_due_producers(db, now=NOW, slot_budget=1)
        assert res.ran == 1 and res.skipped == 2    # 3 due, budget 1 (explicit override unchanged)
    _run(go())


def test_default_slot_budget_is_20():
    # Runtime-tuning: the default budget covers all currently enabled producers (11) for a
    # single active user in one tick. Raised from 10 → 20 after Review Velocity (Phase 6.3b).
    import inspect
    sig = inspect.signature(AdvisoryRuntime.run_due_producers)
    assert sig.parameters["slot_budget"].default == 20


# ── (5) error isolation → one producer fails, other still runs ───────────────

def test_error_isolation(monkeypatch):
    monkeypatch.setattr(registry_mod, "ADVISORY_PRODUCERS",
                        (_spec("t_boom", _boom_producer), _spec("t_ok", _ok_producer)))

    async def go():
        db = await _db(); uid = str(uuid.uuid4()); await _active(db, uid)
        res = await AdvisoryRuntime().run_due_producers(db, now=NOW)
        assert res.errors == 1 and res.ran == 1
        rows = {r.producer_key: r.status for r in await _runs(db)}
        assert rows == {"t_boom": "error", "t_ok": "ok"}   # both ledgered
    _run(go())


# ── (6) scheduler tick swallows / no-op ──────────────────────────────────────

def test_scheduler_tick_swallows(monkeypatch):
    import tasks.scheduler as sched

    async def _noop(self, db, **kw):
        return RuntimeTickResult(ran=0, skipped=0, errors=0)

    async def _raise(self, db, **kw):
        raise RuntimeError("tick boom")

    # clean no-op path
    monkeypatch.setattr(AdvisoryRuntime, "run_due_producers", _noop)
    _run(sched._advisory_runtime_tick())          # completes, no exception

    # exception must NOT escape the tick
    monkeypatch.setattr(AdvisoryRuntime, "run_due_producers", _raise)
    _run(sched._advisory_runtime_tick())          # swallowed, no raise


# ── (7) runtime imports no producer internals / live module ──────────────────

def test_runtime_imports_clean():
    tree = ast.parse(inspect.getsource(runtime_mod))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    blob = " ".join(names).lower()
    for forbidden in ("growth", "audit_persist", "internal_source", "snapshot",
                      "telegram", "intelligence_loop", "compute_insights",
                      "decision_feed", "executor", "producers"):
        assert forbidden not in blob, f"runtime must not import {forbidden}"


# ── (8) growth now enabled=True (A13) ────────────────────────────────────────

def test_growth_enabled():
    growth = next((s for s in ADVISORY_PRODUCERS if s.key == "growth"), None)
    assert growth is not None and growth.enabled is True
