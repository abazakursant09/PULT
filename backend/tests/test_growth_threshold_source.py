"""
Advisory Runtime A11 — growth threshold source.

Derives per-seller GrowthThresholds read-only from the seller's own finance, replacing
the permissive (0,0,0) default. Growth stays DISABLED — this only fixes the threshold
blocker. Proves: built from finance, deterministic, no DB write, the adapter uses it
(only above-typical listings surface), growth disabled, legal/review unchanged.
"""
import asyncio
import logging
import uuid
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_finance import ImportedFinanceRow
from models.product_listing import ProductListing
from models.physical_product import PhysicalProduct
from models.growth_signal import GrowthSignal
from models.growth_audit import GrowthAudit

from services.growth.rules import GrowthThresholds
from services.growth.threshold_source import derive_growth_thresholds
from services.advisory_runtime.runtime import RuntimeContext
from services.advisory_runtime.producers import run_growth_producer
from services.advisory_runtime.registry import ADVISORY_PRODUCERS

NOW = datetime(2026, 6, 30, 12, 0, 0)


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
                          logger=logging.getLogger("test.growth"), triggered_by="manual")


async def _fin(db, uid, *, sku, revenue, net_profit, mp="ozon"):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date="2026-06-20", sku=sku, revenue=revenue, net_profit=net_profit,
                              ad_spend=0.0, quantity=10))
    await db.flush()


# ── (1) built from finance (median per-sku revenue / positive net_profit) ─────

def test_thresholds_built_from_finance():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="BIG", revenue=1000.0, net_profit=300.0)
        await _fin(db, uid, sku="SMALL", revenue=100.0, net_profit=30.0)
        await db.commit()
        th = await derive_growth_thresholds(db, uid, now=NOW)
        assert th.low_stock_units == 5
        assert th.min_revenue_for_growth_signal == 550.0          # median(1000,100)
        assert th.min_net_profit_for_growth_signal == 165.0        # median(300,30)
    _run(go())


def test_thresholds_empty_when_no_finance():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        th = await derive_growth_thresholds(db, uid, now=NOW)
        assert th == GrowthThresholds()                            # all None → no signals
    _run(go())


# ── (2) deterministic for the same data ──────────────────────────────────────

def test_thresholds_deterministic():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="A", revenue=500.0, net_profit=120.0)
        await _fin(db, uid, sku="B", revenue=300.0, net_profit=60.0)
        await db.commit()
        a = await derive_growth_thresholds(db, uid, now=NOW)
        b = await derive_growth_thresholds(db, uid, now=NOW)
        assert a == b                                              # frozen dataclass equality
    _run(go())


# ── (3) read-only — no DB write ──────────────────────────────────────────────

def test_thresholds_no_db_write():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        await _fin(db, uid, sku="X", revenue=200.0, net_profit=50.0)
        await db.commit()
        before = (await db.execute(select(func.count()).select_from(ImportedFinanceRow))).scalar()
        await derive_growth_thresholds(db, uid, now=NOW)
        after = (await db.execute(select(func.count()).select_from(ImportedFinanceRow))).scalar()
        assert before == after
        # derive creates no growth artifacts
        assert (await db.execute(select(GrowthAudit))).scalars().all() == []
        assert (await db.execute(select(GrowthSignal))).scalars().all() == []
    _run(go())


# ── (4) growth producer uses the source → only above-typical listings surface ─

def test_growth_producer_uses_derived_thresholds():
    async def go():
        db = await _db(); uid = str(uuid.uuid4())
        # two profitable, no-ad listings; BIG above median, SMALL below
        for sku, rev, np in (("BIG", 1000.0, 300.0), ("SMALL", 100.0, 30.0)):
            phys = str(uuid.uuid4())
            db.add(PhysicalProduct(id=phys, user_id=uid, title="t", cogs=10.0, cogs_source="manual"))
            db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace="ozon", external_id=sku))
            await _fin(db, uid, sku=sku, revenue=rev, net_profit=np)
        await db.commit()

        await run_growth_producer(_ctx(db, uid)); await db.commit()

        skus = {s.sku for s in (await db.execute(select(GrowthSignal).where(
            GrowthSignal.user_id == uid, GrowthSignal.status.in_(("active", "reopened"))))).scalars().all()}
        assert skus == {"BIG"}          # SMALL filtered out by the data-derived threshold
    _run(go())


# ── (5)(6) growth still disabled; legal/review unchanged ─────────────────────

def test_registry_unchanged():
    by_key = {s.key: s for s in ADVISORY_PRODUCERS}
    assert by_key["growth"].enabled is False
    assert by_key["legal"].enabled is True
    assert by_key["review"].enabled is True
