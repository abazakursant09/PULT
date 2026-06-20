"""
Sprint L5 — read-side context resolver.

resolve_context_group_for_insight derives the read-side context_group from
insight/listing/user data using the SAME helpers as the Memory write path, so
write-side context_group == read-side context_group for the same data. Missing
segments degrade to 'unknown'. get_ranked_alternatives_for_insight resolves then
delegates. Read-only — no writes, no execution imports.
"""
import ast
import asyncio
import inspect
import uuid

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

from services import decision_memory
from services.decision_memory import (
    record_decision_memory, resolve_context_group_for_insight,
)
from services.ranked_alternatives import get_ranked_alternatives_for_insight

SKU = "SKU1"
IKEY = f"margin_crisis:wildberries:{SKU}"
ENRICHED = "wildberries|electronics|mid|high_margin"
ACTIONS = ["set_price", "reduce_discount", "stop_auto_promotion"]


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_domain(db, uid, *, category="electronics", price=1000.0, finance=True):
    prod = Product(id=str(uuid.uuid4()), user_id=uid, name="P", marketplace="wildberries",
                   category=category, sku=SKU, price=price)
    db.add(prod)
    listing = ProductListing(id=str(uuid.uuid4()), physical_product_id=str(uuid.uuid4()),
                             user_id=uid, marketplace="wildberries", external_id="nm1",
                             legacy_product_id=prod.id)
    db.add(listing)
    if finance:
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace="wildberries",
                                  sku=SKU, net_profit=300.0, revenue=1000.0))
    await db.flush()
    return listing.id


# ── CHECK: read resolver == write-side enriched context ──────────────────────

def test_read_resolver_matches_write_side():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing_id = await _seed_domain(db, uid)
        # write side
        d = Decision(id=str(uuid.uuid4()), user_id=uid, problem="p", status="open",
                     listing_id=listing_id, action_key="set_price", insight_key=IKEY)
        db.add(d); await db.flush()
        write_cg = (await record_decision_memory(db, decision=d, outcome="refuted")).context_group
        # read side, same data
        read_cg = await resolve_context_group_for_insight(
            db, user_id=uid, insight_key=IKEY, listing_id=listing_id)
        assert write_cg == read_cg == ENRICHED
    _run(go())


# ── CHECK: missing category/price/margin degrade segment-by-segment ──────────

def test_missing_margin_degrades():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing_id = await _seed_domain(db, uid, finance=False)  # no finance → margin unknown
        cg = await resolve_context_group_for_insight(
            db, user_id=uid, insight_key=IKEY, listing_id=listing_id)
        assert cg == "wildberries|electronics|mid|unknown"
    _run(go())


def test_missing_product_degrades_category_and_price():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # listing without a legacy product → category/price unknown; finance present → margin
        listing = ProductListing(id=str(uuid.uuid4()), physical_product_id=str(uuid.uuid4()),
                                 user_id=uid, marketplace="wildberries", external_id="nm1",
                                 legacy_product_id=None)
        db.add(listing)
        db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace="wildberries",
                                  sku=SKU, net_profit=300.0, revenue=1000.0))
        await db.flush()
        cg = await resolve_context_group_for_insight(
            db, user_id=uid, insight_key=IKEY, listing_id=listing.id)
        assert cg == "wildberries|unknown|unknown|high_margin"
    _run(go())


# ── CHECK: sku hint fills margin when key has none; parity preserved ─────────

def test_sku_hint_fills_margin_when_key_lacks_sku():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_domain(db, uid)  # finance keyed by SKU
        # key has no sku segment; pass sku + marketplace hints, no listing
        cg = await resolve_context_group_for_insight(
            db, user_id=uid, insight_key="margin_crisis", marketplace="wildberries", sku=SKU)
        # no listing → category/price unknown; margin resolved from finance via hint
        assert cg == "wildberries|unknown|unknown|high_margin"
    _run(go())


# ── CHECK: malformed insight → safe fallback ─────────────────────────────────

def test_malformed_insight_safe_fallback():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert await resolve_context_group_for_insight(db, user_id=uid, insight_key="") \
            == "unknown|unknown|unknown|unknown"
        assert await resolve_context_group_for_insight(db, user_id=uid, insight_key=None) \
            == "unknown|unknown|unknown|unknown"
    _run(go())


# ── CHECK: old degraded context still possible ───────────────────────────────

def test_degraded_context_when_no_data():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # marketplace hint only, no listing/product/finance
        cg = await resolve_context_group_for_insight(
            db, user_id=uid, insight_key=IKEY, marketplace="wildberries")
        assert cg == "wildberries|unknown|unknown|unknown"
    _run(go())


# ── CHECK: convenience fn uses the resolved context ──────────────────────────

def test_for_insight_uses_resolved_context():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing_id = await _seed_domain(db, uid)
        # history under the ENRICHED context the resolver will compute
        for _ in range(3):
            did = str(uuid.uuid4())
            db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                            action_key="reduce_discount", insight_key=f"{IKEY}:{did[:6]}"))
            db.add(DecisionMemory(decision_id=did, action_type="reduce_discount",
                                  context_group=ENRICHED, outcome="confirmed"))
        await db.commit()
        alts = await get_ranked_alternatives_for_insight(
            db, user_id=uid, insight_key=IKEY, listing_id=listing_id)
        top = next(a for a in alts if a["action_key"] == "reduce_discount")
        # ranked from enriched history → not a fallback, sample read
        assert top["fallback"] is False and top["sample"] == 3
        assert top["rank"] == 1
    _run(go())


def test_for_insight_degraded_falls_back_when_no_history():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # no domain data, no history → resolver gives unknown context → static fallback
        alts = await get_ranked_alternatives_for_insight(db, user_id=uid, insight_key=IKEY)
        assert [a["action_key"] for a in alts] == ACTIONS
        assert all(a["fallback"] is True for a in alts)
    _run(go())


# ── purity: resolver writes nothing; module has no execution imports ─────────

def test_resolver_no_writes():
    src = inspect.getsource(resolve_context_group_for_insight)
    for bad in ("db.add", "db.commit", "db.flush", ".delete(", "random", "sklearn"):
        assert bad not in src


def test_decision_memory_no_execution_imports():
    src = inspect.getsource(decision_memory)
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for bad in ("executor", "measurement_close_bridge", "refuted_loop",
                "sklearn", "numpy", "torch"):
        assert all(bad not in m for m in mods), f"must not import {bad}"
