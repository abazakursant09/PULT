"""
Advisory Runtime A13 — enable the growth producer (live regression).

Flips growth to enabled=True. The permissive-default blocker is gone (A11 data-derived
thresholds) and the shadow run (A12) proved it advisory-only. This is a MINIMAL enable:
no logic change. Proves through the REAL registry / scheduler path that:

  * all three producers are enabled (growth + legal + review),
  * run_due_producers runs exactly {legal, review, growth},
  * each creates only its advisory signal (growth_signal + legal_signal + review_signal),
  * NOTHING executable is created — 0 Decision, 0 EngineSignalDecisionLink (and the
    adapters are DB-headless, so 0 Apply / 0 executor / 0 marketplace write by
    construction — covered by the per-adapter import guards),
  * growth reconciliation stays idempotent across repeated scheduler runs.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.product import Product
from models.product_listing import ProductListing
from models.physical_product import PhysicalProduct
from models.review_response import ReviewResponse
from models.growth_signal import GrowthSignal
from models.legal_signal import LegalSignal
from models.review_signal import ReviewSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advisory_run import AdvisoryRun

from services.advisory_runtime.runtime import AdvisoryRuntime
from services.advisory_runtime.registry import ADVISORY_PRODUCERS

NOW = datetime(2026, 6, 30, 12, 0, 0)
SKU = "BIG"
MP = "ozon"


def _run(c):
    return asyncio.run(c)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_all_three(db, uid):
    """One seller serving all three producers on the same (marketplace, sku):
      * growth candidate — PhysicalProduct + ProductListing + above-typical finance,
      * legal subject    — Product with a denylist claim title ("оригинал"),
      * review subject    — five-star, no-text, unanswered ReviewResponse.
    """
    # growth: physical product + listing + finance (rev/profit anchor the threshold)
    phys = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys, user_id=uid, title="t", cogs=10.0, cogs_source="manual"))
    db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace=MP, external_id=SKU))
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=MP,
                              date="2026-06-20", sku=SKU, revenue=1000.0, net_profit=300.0,
                              ad_spend=0.0, quantity=10))
    # legal + review: a catalog Product ("оригинал" → content_claim_risk) + a review
    pid = str(uuid.uuid4())
    db.add(Product(id=pid, user_id=uid, name="оригинал товар", marketplace=MP, sku=SKU, price=100.0))
    db.add(ReviewResponse(id=str(uuid.uuid4()), product_id=pid, rating=5, review_text=None,
                          response_text=None, status="pending", marketplace=MP))
    await db.commit()


async def _live(db, uid, model):
    return (await db.execute(select(model).where(
        model.user_id == uid, model.status.in_(("active", "reopened"))))).scalars().all()


# ── (1) registry: legal + review + growth all enabled ────────────────────────

def test_registry_all_enabled():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["legal"].enabled is True
    assert by_key["review"].enabled is True
    assert by_key["growth"].enabled is True       # A13 flip


# ── (2) scheduler runs the enabled producers (incl. operations_low_stock, A22) ─

def test_scheduler_runs_legal_review_growth():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_all_three(db, uid)
        # slot_budget covers all enabled producers in one tick (11 now — default 10 would
        # drop the last-registered producer)
        res = await AdvisoryRuntime().run_due_producers(db, now=NOW, slot_budget=20)   # REAL registry
        assert res.errors == 0
        keys = {r.producer_key for r in (await db.execute(select(AdvisoryRun))).scalars().all()}
        assert keys == {"legal", "review", "growth", "operations_low_stock", "advertising",
                        "pricing", "revenue_diagnosis", "money_leak", "supply", "rating",
                        "review_velocity"}
    _run(go())


# ── (3) each producer creates only its advisory signal ───────────────────────

def test_all_three_signals_created():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_all_three(db, uid)
        await AdvisoryRuntime().run_due_producers(db, now=NOW)
        assert len(await _live(db, uid, GrowthSignal)) >= 1
        assert len(await _live(db, uid, LegalSignal)) >= 1
        assert len(await _live(db, uid, ReviewSignal)) >= 1
    _run(go())


# ── (4) advisory-only: nothing executable downstream ─────────────────────────

def test_advisory_only_no_executable():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_all_three(db, uid)
        await AdvisoryRuntime().run_due_producers(db, now=NOW)
        # 0 Decision, 0 EngineSignalDecisionLink (no Apply / executor / marketplace write
        # downstream of an advisory signal — they require a Decision that is never created)
        assert (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all() == []
        assert (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().all() == []
    _run(go())


# ── (5) growth reconcile stays idempotent across scheduler runs ──────────────

def test_growth_idempotent_across_runs():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _seed_all_three(db, uid)
        # two scheduler ticks far enough apart that the hourly cadence re-runs growth
        await AdvisoryRuntime().run_due_producers(db, now=NOW)
        later = datetime(2026, 6, 30, 14, 0, 0)               # +2h > growth cadence (3600s)
        await AdvisoryRuntime().run_due_producers(db, now=later)
        keys = [s.insight_key for s in await _live(db, uid, GrowthSignal)]
        assert len(keys) == len(set(keys)), f"duplicate live growth signals: {keys}"
    _run(go())
