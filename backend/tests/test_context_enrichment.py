"""
Sprint L3 — context enrichment for decision_memory.

record_decision_memory now writes a real context_group:
marketplace|category|price_band|margin_band, resolved from the listing's legacy
Product (category/price) and imported finance (margin). Missing segments →
unknown, never fabricated. Deterministic bands. Old rows unaffected (no backfill).
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.decision_memory import DecisionMemory
from models.product import Product
from models.product_listing import ProductListing
from models.imported_finance import ImportedFinanceRow
from services.decision_memory import (
    record_decision_memory, price_band, margin_band, build_context_group,
)

SKU = "SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, category="electronics", price=1000.0,
                net_profit=300.0, revenue=1000.0, with_finance=True):
    """A decision linked listing→legacy Product, + finance for margin."""
    prod = Product(id=str(uuid.uuid4()), user_id=uid, name="P", marketplace="wb", sku=SKU,
                   category=category, price=price)
    db.add(prod)
    listing = ProductListing(id=str(uuid.uuid4()), physical_product_id="phys-1",
                             user_id=uid, marketplace="wb", external_id=SKU,
                             legacy_product_id=prod.id)
    db.add(listing)
    if with_finance:
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="wb",
                                  date="2026-06-19", sku=SKU, net_profit=net_profit,
                                  revenue=revenue))
    d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                 action_key="set_price", insight_key=f"margin_crisis:wb:{SKU}",
                 listing_id=listing.id)
    db.add(d); await db.flush()
    return d


async def _cg(db, d, uid):
    row = await record_decision_memory(db, decision=d, outcome="confirmed")
    return row.context_group


# ── band unit tests (deterministic thresholds) ───────────────────────────────

def test_price_band_thresholds():
    assert price_band(None) == "unknown"
    assert price_band(499) == "low"
    assert price_band(500) == "mid"
    assert price_band(1999) == "mid"
    assert price_band(2000) == "high"


def test_margin_band_thresholds():
    assert margin_band(None) == "unknown"
    assert margin_band(9.9) == "low_margin"
    assert margin_band(10) == "mid_margin"
    assert margin_band(24.9) == "mid_margin"
    assert margin_band(25) == "high_margin"


# ── complete context ─────────────────────────────────────────────────────────

def test_complete_context():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _seed(db, uid, category="electronics", price=1000.0,
                        net_profit=300.0, revenue=1000.0)  # 30% margin
        assert await _cg(db, d, uid) == "wb|electronics|mid|high_margin"
    _run(go())


# ── missing segments → unknown ───────────────────────────────────────────────

def test_missing_category():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _seed(db, uid, category=None, price=1000.0)
        assert await _cg(db, d, uid) == "wb|unknown|mid|high_margin"
    _run(go())


def test_missing_price():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _seed(db, uid, category="electronics", price=None)
        assert await _cg(db, d, uid) == "wb|electronics|unknown|high_margin"
    _run(go())


def test_missing_margin():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        d = await _seed(db, uid, category="electronics", price=1000.0, with_finance=False)
        assert await _cg(db, d, uid) == "wb|electronics|mid|unknown"
    _run(go())


# ── backward compatibility ───────────────────────────────────────────────────

def test_no_listing_fully_degraded():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # decision with no listing and no finance → same degraded form as before
        d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                     action_key="set_price", insight_key=f"margin_crisis:wb:{SKU}")
        db.add(d); await db.flush()
        assert await _cg(db, d, uid) == "unknown|unknown|unknown|unknown"
    _run(go())


def test_old_rows_unaffected():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # an old-style row written directly with the degraded context_group
        old = DecisionMemory(decision_id=str(uuid.uuid4()), action_type="set_price",
                             context_group="wb|unknown|unknown|unknown", outcome="confirmed")
        db.add(old); await db.commit()
        row = (await db.execute(select(DecisionMemory).where(
            DecisionMemory.id == old.id))).scalar_one()
        assert row.context_group == "wb|unknown|unknown|unknown"  # unchanged, no backfill
    _run(go())


def test_build_context_group_unchanged_signature():
    # builder still pure; lowercases + null→unknown
    assert build_context_group("WB", "Electronics", "mid", "high_margin") == "wb|electronics|mid|high_margin"
    assert build_context_group("wb", None, None, None) == "wb|unknown|unknown|unknown"
