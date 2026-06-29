"""
Advisory Runtime A9-impl — review producer adapter (disabled + shadow validation).

Review is the second live-safe candidate: advisory-only (publish_review_response is
PAYLOAD_NOT_DERIVABLE; negatives MANUAL_ONLY), threshold-free, DB-headless, no
marketplace read. Shipped DISABLED. Tests run the adapter directly / via run_one —
never via the scheduler.
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
from models.product import Product
from models.review_response import ReviewResponse
from models.review_signal import ReviewSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun

from services.advisory_runtime.runtime import AdvisoryRuntime, RuntimeContext, ProducerResult
from services.advisory_runtime.registry import ADVISORY_PRODUCERS
from services.advisory_runtime import producers as producers_mod
from services.advisory_runtime.producers import run_review_producer
from services.action_binding.registry import BY_SIGNAL_TYPE

NOW = datetime(2026, 6, 29, 12, 0, 0)
REV_SIG = "rev_five_star_without_text"
REV_NEG = "rev_unanswered_negative_review"


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
                          logger=logging.getLogger("test.review"), triggered_by="manual")


async def _seed_review(db, uid, *, mp="ozon", sku="SKU1"):
    # five-star, no text, unanswered → five_star_without_text triggers (deterministic)
    pid = str(uuid.uuid4())
    db.add(Product(id=pid, user_id=uid, name="товар", marketplace=mp, sku=sku, price=100.0))
    db.add(ReviewResponse(id=str(uuid.uuid4()), product_id=pid, rating=5, review_text=None,
                          response_text=None, status="pending", marketplace=mp))
    await db.commit()


async def _rev_sigs(db, uid):
    return (await db.execute(select(ReviewSignal).where(
        ReviewSignal.user_id == uid, ReviewSignal.status.in_(("active", "reopened"))))).scalars().all()


# ── (1) registry: review disabled, legal enabled, growth disabled ────────────

def test_registry_state():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["review"].enabled is False
    assert by_key["review"].cadence_seconds == 86400
    assert by_key["review"].run is run_review_producer
    assert by_key["legal"].enabled is True       # unchanged
    assert by_key["growth"].enabled is False      # unchanged


# ── (2) shadow validation via run_one ────────────────────────────────────────

def test_run_one_shadow_validation():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_review(db, uid)
        row = await AdvisoryRuntime().run_one(db, user_id=uid, producer_key="review", now=NOW)
        assert isinstance(row, AdvisoryRun) and row.status == "ok"
        assert isinstance(json.loads(row.stats), dict)         # opaque stats
        assert len(await _rev_sigs(db, uid)) >= 1               # review_signal created
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
    _run(go())


# ── (3) idempotency / reconciliation ─────────────────────────────────────────

def test_idempotent_no_duplicates():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_review(db, uid)
        await run_review_producer(_ctx(db, uid)); await db.commit()
        await run_review_producer(_ctx(db, uid)); await db.commit()
        live = await _rev_sigs(db, uid)
        keys = [s.insight_key for s in live]
        assert len(keys) == len(set(keys)), f"duplicate live review signals: {keys}"
    _run(go())


# ── (4) advisory-only guard: not bindable / negatives MANUAL_ONLY ────────────

def test_review_advisory_only():
    # the produced signal type binds NO executor action
    b = BY_SIGNAL_TYPE.get(REV_SIG)
    if b is not None:
        assert not b.bindable and b.binding_status != "bound"
    # negative reviews stay MANUAL_ONLY (never auto)
    neg = BY_SIGNAL_TYPE.get(REV_NEG)
    assert neg is not None and not neg.bindable and neg.safety_class == "manual_only"
    # no review signal type binds publish_review_response as an executable action
    for st, binding in BY_SIGNAL_TYPE.items():
        if st.startswith("rev_"):
            assert binding.action_key != "publish_review_response" or binding.binding_status != "bound"


# ── (5) import guard — adapter pulls in no live/executable module ────────────

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
                      "executor", "wb_client", "ozon_client", "intelligence_loop",
                      "promotion"):
        assert forbidden not in blob, f"adapter must not import {forbidden}"
