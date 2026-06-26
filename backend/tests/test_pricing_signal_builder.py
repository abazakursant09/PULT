"""
A3-pre — pricing signal generation from OBSERVED finance.

generate_pricing_signals reads only ImportedFinanceRow (+ optional Product price /
PricingRule floor), evaluates deterministic rules, and reconciles one live
PricingSignal per insight_key. No forecast, no competitor price, no recommendation,
no fabricated signal when finance is absent. Marketplace-isolated.
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
from models.pricing_signal import PricingSignal

from services.pricing.generator import generate_pricing_signals
from services.pricing.rules import PricingThresholds

NOW = datetime(2026, 6, 25)
TH = PricingThresholds(min_revenue_for_pricing_signal=100.0, target_margin_pct=10.0)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _fin(db, uid, *, mp, sku, revenue, net_profit):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date="2026-06-20", sku=sku, revenue=revenue, net_profit=net_profit))
    await db.commit()


async def _signals(db, uid, *, mp=None):
    q = select(PricingSignal).where(PricingSignal.user_id == uid)
    if mp is not None:
        q = q.where(PricingSignal.marketplace == mp)
    return (await db.execute(q)).scalars().all()


# ── (3) negative_margin from observed finance ────────────────────────────────

def test_negative_margin_generated_from_finance():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, mp="wb", sku="SKU1", revenue=10000.0, net_profit=-500.0)
        res = await generate_pricing_signals(db, user_id=uid, marketplace="wb",
                                             sku="SKU1", thresholds=TH, now=NOW)
        assert res.evaluated and res.reconciliation.created >= 1
        sigs = {s.problem_type: s for s in await _signals(db, uid)}
        assert "negative_margin" in sigs
        s = sigs["negative_margin"]
        assert s.signal_key == "pricing_negative_margin"
        assert s.insight_key == "pricing_negative_margin:wb:SKU1"   # (6) stable insight_key
        assert s.status == "active" and s.marketplace == "wb"
        assert s.recommended_action_key is None        # no set_price yet
        # evidence-derived, observed values only (no recommended/forecast price)
        assert s.what and s.evidence_hash
    _run(go())


def test_margin_below_target_generated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # positive but thin margin: net_profit 500 / revenue 10000 = 5% < target 10%
        await _fin(db, uid, mp="ozon", sku="SKU2", revenue=10000.0, net_profit=500.0)
        await generate_pricing_signals(db, user_id=uid, marketplace="ozon",
                                       sku="SKU2", thresholds=TH, now=NOW)
        types = {s.problem_type for s in await _signals(db, uid)}
        assert "margin_below_target" in types and "negative_margin" not in types
    _run(go())


# ── (4) no signal when finance data missing ──────────────────────────────────

def test_no_signal_when_finance_missing():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await generate_pricing_signals(db, user_id=uid, marketplace="wb",
                                             sku="GHOST", thresholds=TH, now=NOW)
        assert res.evaluated is False and res.reconciliation is None
        assert await _signals(db, uid) == []          # never a fabricated signal
    _run(go())


def test_healthy_margin_no_signal():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # healthy: 30% margin, above target, positive → no margin problem
        await _fin(db, uid, mp="wb", sku="SKU3", revenue=10000.0, net_profit=3000.0)
        await generate_pricing_signals(db, user_id=uid, marketplace="wb",
                                       sku="SKU3", thresholds=TH, now=NOW)
        # only finance-rule types could fire; none should (price_below_floor needs a rule)
        assert [s for s in await _signals(db, uid)] == []
    _run(go())


# ── (5) marketplace isolation ────────────────────────────────────────────────

def test_marketplace_isolation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, mp="wb", sku="SKU1", revenue=10000.0, net_profit=-500.0)   # WB loss
        await _fin(db, uid, mp="ozon", sku="SKU1", revenue=10000.0, net_profit=3000.0)  # Ozon healthy
        await generate_pricing_signals(db, user_id=uid, marketplace="wb", sku="SKU1",
                                       thresholds=TH, now=NOW)
        await generate_pricing_signals(db, user_id=uid, marketplace="ozon", sku="SKU1",
                                       thresholds=TH, now=NOW)
        wb = await _signals(db, uid, mp="wb")
        oz = await _signals(db, uid, mp="ozon")
        assert {s.problem_type for s in wb} == {"negative_margin"}   # WB only
        assert oz == []                                              # Ozon unaffected, healthy
        # never blended: WB net loss did not create an Ozon signal and vice versa
        assert all(s.insight_key.endswith(":wb:SKU1") for s in wb)
    _run(go())


# ── (7) no duplicate signal on rerun (reconciliation) ────────────────────────

def test_no_duplicate_on_rerun():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, mp="wb", sku="SKU1", revenue=10000.0, net_profit=-500.0)
        r1 = await generate_pricing_signals(db, user_id=uid, marketplace="wb", sku="SKU1",
                                            thresholds=TH, now=NOW)
        r2 = await generate_pricing_signals(db, user_id=uid, marketplace="wb", sku="SKU1",
                                            thresholds=TH, now=NOW)
        assert r1.reconciliation.created == 1
        assert r2.reconciliation.created == 0 and r2.reconciliation.unchanged >= 1
        # exactly one row per insight_key — no duplicate
        sigs = await _signals(db, uid)
        keys = [s.insight_key for s in sigs]
        assert len(keys) == len(set(keys)) == 1
    _run(go())


# ── resolution: problem gone → resolved (not deleted, not duplicated) ────────

def test_resolved_when_margin_recovers():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _fin(db, uid, mp="wb", sku="SKU1", revenue=10000.0, net_profit=-500.0)
        await generate_pricing_signals(db, user_id=uid, marketplace="wb", sku="SKU1",
                                       thresholds=TH, now=NOW)
        # margin recovers (add profit so the SKU is now healthy)
        await _fin(db, uid, mp="wb", sku="SKU1", revenue=0.0, net_profit=4000.0)
        await generate_pricing_signals(db, user_id=uid, marketplace="wb", sku="SKU1",
                                       thresholds=TH, now=NOW)
        s = (await _signals(db, uid))[0]
        assert s.status == "resolved"
    _run(go())
