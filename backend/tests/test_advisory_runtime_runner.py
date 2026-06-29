"""
Advisory Runtime A5 — manual runner (AdvisoryRuntime.run_one).

Orchestration layer only — NOT a scheduler. Proves run_one drives ONE producer
through the single contract, writes an AdvisoryRun ledger row (always, incl. on
producer failure), commits, stores opaque stats verbatim, ignores `enabled`, and
never touches producer internals.
"""
import ast
import asyncio
import inspect
import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.advisory_run import AdvisoryRun

from services.advisory_runtime.runtime import (
    AdvisoryRuntime, AdvisoryRuntimeError, RuntimeContext, ProducerResult,
)
from services.advisory_runtime.registry import ProducerSpec
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


async def _seed_profitable(db, uid, *, mp="ozon", sku="SKU1"):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date="2026-06-20", sku=sku, revenue=1000.0, net_profit=300.0,
                              ad_spend=0.0, quantity=10))
    await db.commit()


async def _runs(db, uid):
    return (await db.execute(select(AdvisoryRun).where(AdvisoryRun.user_id == uid))).scalars().all()


# ── (1)(2)(3)(6) run_one drives the real growth producer + writes ledger ─────

def test_run_one_drives_growth_and_writes_ledger():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_profitable(db, uid)
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="growth", now=NOW)
        assert isinstance(row, AdvisoryRun)
        assert row.status == "ok"
        assert row.producer_key == "growth"
        assert row.finished_at is not None and row.duration_ms is not None
        # committed + queryable
        persisted = await _runs(db, uid)
        assert len(persisted) == 1 and persisted[0].status == "ok"
    _run(go())


# ── (4) stats stored verbatim as opaque JSON ─────────────────────────────────

def test_run_one_stores_opaque_stats_json():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_profitable(db, uid)
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="growth", now=NOW)
        parsed = json.loads(row.stats)           # valid JSON object
        assert isinstance(parsed, dict)          # Runtime stored it; never read its keys
    _run(go())


# ── (5) producer error → status=error, error filled, ledger committed ────────

def test_run_one_records_producer_error(monkeypatch):
    async def _boom(ctx: RuntimeContext) -> ProducerResult:
        raise RuntimeError("kaboom")
    failing = ProducerSpec(key="boom", run=_boom, cadence_seconds=1, enabled=False)
    monkeypatch.setattr(registry_mod, "ADVISORY_PRODUCERS", (failing,))

    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        raised = False
        try:
            await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="boom", now=NOW)
        except RuntimeError as e:
            raised = True
            assert "kaboom" in str(e)
        assert raised
        rows = await _runs(db, uid)
        assert len(rows) == 1                     # ledger persisted despite failure
        assert rows[0].status == "error"
        assert rows[0].error == "kaboom"
        assert rows[0].finished_at is not None
    _run(go())


# ── unknown producer → Runtime-level error, no ledger row ────────────────────

def test_run_one_unknown_producer_raises():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        raised = False
        try:
            await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="nope", now=NOW)
        except AdvisoryRuntimeError:
            raised = True
        assert raised
        assert len(await _runs(db, uid)) == 0
    _run(go())


# ── enabled=False does NOT block run_one (manual path) ───────────────────────

def test_run_one_ignores_enabled_flag():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_profitable(db, uid)
        # growth spec is enabled=False in the real registry; run_one still runs it
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="growth", now=NOW)
        assert row.status == "ok"
    _run(go())


# ── (8) Runtime imports only registry/model — no producer internals / live ───

def test_runtime_imports_no_producer_internals():
    tree = ast.parse(inspect.getsource(runtime_mod))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    blob = " ".join(names).lower()
    for forbidden in ("growth", "audit_persist", "internal_source", "snapshot",
                      "telegram", "scheduler", "intelligence_loop", "decision_feed",
                      "executor", "action_engine", "producers"):
        assert forbidden not in blob, f"runtime must not import {forbidden}"
