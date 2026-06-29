"""
Advisory Runtime A7 — legal producer adapter (disabled + shadow validation).

Legal is the first live-safe candidate: advisory-only, AUTO_FORBIDDEN, threshold-free,
DB-headless, no marketplace read. Shipped DISABLED. Tests run the adapter directly and
through the manual runner (run_one) — never via the scheduler.
"""
import ast
import asyncio
import inspect
import json
import uuid
from datetime import datetime
from dataclasses import fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.product import Product
from models.legal_signal import LegalSignal
from models.decision import Decision
from models.advisory_run import AdvisoryRun

from services.advisory_runtime.runtime import AdvisoryRuntime, RuntimeContext, ProducerResult
from services.advisory_runtime.registry import ADVISORY_PRODUCERS, ProducerSpec
from services.advisory_runtime import producers as producers_mod
from services.advisory_runtime.producers import run_legal_producer
from services.action_binding.registry import binding_for_action, BY_SIGNAL_TYPE

NOW = datetime(2026, 6, 29, 12, 0, 0)
LEGAL_SIG = "legal_content_claim_risk"


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _ctx(db, uid):
    import logging
    return RuntimeContext(db=db, user_id=uid, now=NOW, run_id=str(uuid.uuid4()),
                          logger=logging.getLogger("test.legal"), triggered_by="manual")


async def _seed_claim(db, uid, *, mp="ozon", sku="SKU1"):
    # "оригинал" is in the legal CLAIM_DENYLIST → content_claim_risk triggers
    db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="оригинал товар",
                   marketplace=mp, sku=sku, price=100.0))
    await db.commit()


async def _legal_sigs(db, uid):
    return (await db.execute(select(LegalSignal).where(
        LegalSignal.user_id == uid, LegalSignal.status.in_(("active", "reopened"))))).scalars().all()


# ── registry ─────────────────────────────────────────────────────────────────

def test_registry_has_legal_spec():
    spec = next((s for s in ADVISORY_PRODUCERS if s.key == "legal"), None)
    assert spec is not None
    assert spec.enabled is False
    assert spec.cadence_seconds == 86400
    assert spec.run is run_legal_producer


def test_growth_still_disabled():
    growth = next((s for s in ADVISORY_PRODUCERS if s.key == "growth"), None)
    assert growth is not None and growth.enabled is False


# ── adapter contract + behavior ──────────────────────────────────────────────

def test_legal_adapter_returns_producer_result():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        res = await run_legal_producer(_ctx(db, uid))   # no subjects → ok, empty work
        assert isinstance(res, ProducerResult) and res.ok is True
        assert isinstance(res.stats, dict)
    _run(go())


def test_legal_adapter_creates_signal_from_observed_seed():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_claim(db, uid)
        res = await run_legal_producer(_ctx(db, uid)); await db.commit()
        assert res.ok
        sigs = await _legal_sigs(db, uid)
        assert any(LEGAL_SIG in (s.signal_key or "") for s in sigs)
    _run(go())


def test_legal_adapter_idempotent_no_duplicates():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_claim(db, uid)
        await run_legal_producer(_ctx(db, uid)); await db.commit()
        await run_legal_producer(_ctx(db, uid)); await db.commit()
        live = await _legal_sigs(db, uid)
        keys = [s.insight_key for s in live]
        assert len(keys) == len(set(keys)), f"duplicate live legal signals: {keys}"
    _run(go())


# ── legal stays advisory-only / AUTO_FORBIDDEN ───────────────────────────────

def test_legal_signal_not_executable():
    # the canonical signal type binds to NO executor action (advisory only)
    b = BY_SIGNAL_TYPE.get(LEGAL_SIG)
    if b is not None:
        assert not b.bindable
        assert b.binding_status != "bound"
    # binding_for_action never returns a bound executable for any legal action
    assert binding_for_action(LEGAL_SIG, "stop_auto_promotion") is None or \
        binding_for_action(LEGAL_SIG, "stop_auto_promotion").binding_status != "bound"


# ── DB-headless + no live imports ────────────────────────────────────────────

def test_adapter_imports_nothing_live():
    tree = ast.parse(inspect.getsource(producers_mod))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    blob = " ".join(names).lower()
    for forbidden in ("scheduler", "telegram", "compute_insights", "decision_feed",
                      "executor", "wb_client", "ozon_client", "intelligence_loop"):
        assert forbidden not in blob, f"adapter must not import {forbidden}"


# ── A4: shadow validation via run_one ────────────────────────────────────────

def test_run_one_shadow_validation():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_claim(db, uid)
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="legal", now=NOW)
        # (1)(2)(3) AdvisoryRun written, status ok, stats opaque JSON
        assert isinstance(row, AdvisoryRun) and row.status == "ok"
        assert isinstance(json.loads(row.stats), dict)
        # (4) legal_signal appeared
        assert len(await _legal_sigs(db, uid)) >= 1
        # (5) no Decision created (advisory only)
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
    _run(go())
