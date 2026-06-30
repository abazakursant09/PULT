"""
Advisory Runtime A4 — growth producer adapter.

Proves the first adapter satisfies the Runtime contract and is DB-headless: given
only a RuntimeContext, it builds growth snapshots from imported data and runs the
EXISTING growth audit_and_persist (rules → persist → reconcile). enabled=False, so
nothing runs automatically; the test calls the adapter directly.
"""
import ast
import asyncio
import inspect
import logging
import uuid
from datetime import datetime
from dataclasses import fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.growth_signal import GrowthSignal

from services.advisory_runtime.runtime import RuntimeContext, ProducerResult
from services.advisory_runtime.registry import ADVISORY_PRODUCERS, ProducerSpec
from services.advisory_runtime import producers as producers_mod
from services.advisory_runtime.producers import run_growth_producer

NOW = datetime(2026, 6, 28, 12, 0, 0)


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _ctx(db, uid):
    return RuntimeContext(db=db, user_id=uid, now=NOW, run_id=str(uuid.uuid4()),
                          logger=logging.getLogger("test.advisory"), triggered_by="manual")


async def _seed_profitable(db, uid, *, mp="ozon", sku="SKU1"):
    # net_profit>0, margin high (30%), ad_spend==0 → profitable_ad_candidate (no threshold needed)
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date="2026-06-20", sku=sku, revenue=1000.0, net_profit=300.0,
                              ad_spend=0.0, quantity=10))
    await db.commit()


# ── registry ─────────────────────────────────────────────────────────────────

def test_registry_has_growth_spec():
    spec = next((s for s in ADVISORY_PRODUCERS if s.key == "growth"), None)
    assert spec is not None
    assert spec.enabled is True                 # A13: data-derived thresholds → live
    assert spec.cadence_seconds == 3600
    assert spec.run is run_growth_producer


def test_producer_spec_fields_unchanged():
    names = {f.name for f in fields(ProducerSpec)}
    assert names == {"key", "run", "cadence_seconds", "enabled"}
    for bad in ("scope", "snapshot_builder", "thresholds", "max_units_per_run"):
        assert bad not in names


def test_runtime_context_runtime_only():
    names = {f.name for f in fields(RuntimeContext)}
    assert names == {"db", "user_id", "now", "run_id", "logger", "triggered_by"}


# ── adapter contract + behavior ──────────────────────────────────────────────

def test_growth_adapter_returns_producer_result():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        res = await run_growth_producer(_ctx(db, uid))   # no candidates → ok, empty work
        assert isinstance(res, ProducerResult)
        assert res.ok is True
        assert isinstance(res.stats, dict)               # opaque mapping, no required keys
    _run(go())


def test_growth_adapter_creates_canonical_signal():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_profitable(db, uid)
        res = await run_growth_producer(_ctx(db, uid))
        await db.commit()
        assert res.ok
        sigs = (await db.execute(select(GrowthSignal).where(
            GrowthSignal.user_id == uid, GrowthSignal.status.in_(("active", "reopened"))))).scalars().all()
        assert len(sigs) >= 1                            # profitable_ad_candidate produced
        assert any("profitable_ad_candidate" in (s.signal_key or "") for s in sigs)
    _run(go())


def test_growth_adapter_idempotent_no_duplicates():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_profitable(db, uid)
        await run_growth_producer(_ctx(db, uid)); await db.commit()
        await run_growth_producer(_ctx(db, uid)); await db.commit()   # second pass
        # reconciliation keeps ONE live signal per insight_key
        live = (await db.execute(select(GrowthSignal).where(
            GrowthSignal.user_id == uid, GrowthSignal.status.in_(("active", "reopened"))))).scalars().all()
        keys = [s.insight_key for s in live]
        assert len(keys) == len(set(keys)), f"duplicate live signals: {keys}"
    _run(go())


def test_stats_opaque_mapping_only():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_profitable(db, uid)
        res = await run_growth_producer(_ctx(db, uid))
        # Runtime only needs: it's a mapping. No specific key is contractually required.
        from collections.abc import Mapping
        assert isinstance(res.stats, Mapping)
    _run(go())


# ── guard — adapter imports no scheduler/telegram/insights/feed/executor ──────

def test_adapter_imports_nothing_live():
    tree = ast.parse(inspect.getsource(producers_mod))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    blob = " ".join(names).lower()
    for forbidden in ("scheduler", "intelligence_loop", "telegram", "decision_feed",
                      "executor", "action_engine", "compute_insights"):
        assert forbidden not in blob, f"adapter must not import {forbidden}"
