"""
Sprint L6 — learning ranked-alternatives read API.

GET /api/learning/alternatives → ranked, explained alternatives for an insight.
Handler is called directly with a real in-memory db (DI bypassed). Verifies
shape, ranked order, fallback, the degraded flag, malformed-insight emptiness,
and that the surface is strictly read-only (no writes, no executor/promotion).
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

from routers import learning
from routers.learning import ranked_alternatives_endpoint, AlternativesResponse

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


class _User:
    def __init__(self, uid):
        self.id = uid


async def _seed_domain(db, uid):
    prod = Product(id=str(uuid.uuid4()), user_id=uid, name="P", marketplace="wildberries",
                   category="electronics", sku=SKU, price=1000.0)
    db.add(prod)
    listing = ProductListing(id=str(uuid.uuid4()), physical_product_id=str(uuid.uuid4()),
                             user_id=uid, marketplace="wildberries", external_id="nm1",
                             legacy_product_id=prod.id)
    db.add(listing)
    db.add(ImportedFinanceRow(import_id="imp1", user_id=uid, marketplace="wildberries",
                              sku=SKU, net_profit=300.0, revenue=1000.0))
    await db.flush()
    return listing.id


async def _mem(db, uid, action, outcome, n, cg=ENRICHED):
    for _ in range(n):
        did = str(uuid.uuid4())
        db.add(Decision(id=did, user_id=uid, problem="p", status="open",
                        action_key=action, insight_key=f"{IKEY}:{did[:6]}"))
        db.add(DecisionMemory(decision_id=did, action_type=action,
                              context_group=cg, outcome=outcome))
    await db.flush()


async def _call(db, uid, insight_key=IKEY, listing_id=None):
    return await ranked_alternatives_endpoint(
        insight_key=insight_key, listing_id=listing_id,
        current_user=_User(uid), db=db)


# ── margin insight returns 3 alternatives ────────────────────────────────────

def test_margin_returns_three_alternatives():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _call(db, uid)
        assert isinstance(resp, AlternativesResponse)
        assert resp.insight_key == IKEY
        assert resp.source == "decision_memory"
        assert [a.action_key for a in resp.alternatives] == ACTIONS
        assert [a.rank for a in resp.alternatives] == [1, 2, 3]
    _run(go())


# ── ranked order when history exists ─────────────────────────────────────────

def test_ranked_order_with_history():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing_id = await _seed_domain(db, uid)
        await _mem(db, uid, "reduce_discount", "confirmed", 4)
        await _mem(db, uid, "reduce_discount", "refuted", 1)
        await _mem(db, uid, "set_price", "refuted", 3)
        await db.commit()
        resp = await _call(db, uid, listing_id=listing_id)
        top = resp.alternatives[0]
        assert top.action_key == "reduce_discount"
        assert top.fallback is False
        assert top.confirmed == 4 and top.refuted == 1 and top.sample == 5
        assert top.confirmed_rate == 0.8 and top.weighted_rate == 0.8
        assert "recent outcomes weighted" in top.reason
        assert resp.degraded is False  # enriched context
    _run(go())


# ── no history → fallback order ──────────────────────────────────────────────

def test_no_history_fallback_order():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        listing_id = await _seed_domain(db, uid)
        await db.commit()
        resp = await _call(db, uid, listing_id=listing_id)
        assert [a.action_key for a in resp.alternatives] == ACTIONS
        assert all(a.fallback is True for a in resp.alternatives)
        assert resp.degraded is False  # enriched context, just no history
    _run(go())


# ── degraded true when unknown segment exists ────────────────────────────────

def test_degraded_true_when_unknown_segment():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # no domain data → context resolves to all-unknown
        resp = await _call(db, uid)
        assert resp.degraded is True
        assert [a.action_key for a in resp.alternatives] == ACTIONS
    _run(go())


# ── malformed insight → empty alternatives ───────────────────────────────────

def test_malformed_insight_empty():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _call(db, uid, insight_key="")
        assert resp.alternatives == []
        assert resp.source == "decision_memory"
        # unknown context → degraded
        resp2 = await _call(db, uid, insight_key="low_stock:wb:SKU1")
        assert resp2.alternatives == []
    _run(go())


# ── route registered ─────────────────────────────────────────────────────────

def test_route_registered():
    paths = {getattr(r, "path", None) for r in learning.router.routes}
    assert "/learning/alternatives" in paths
    # GET only
    route = next(r for r in learning.router.routes
                 if getattr(r, "path", None) == "/learning/alternatives")
    assert route.methods == {"GET"}


# ── read-only: no writes in handler ──────────────────────────────────────────

def test_handler_no_writes():
    src = inspect.getsource(learning)
    for bad in ("db.add", "db.commit", "db.flush", ".delete("):
        assert bad not in src


# ── no executor / promotion imports ──────────────────────────────────────────

def test_no_executor_or_promotion_imports():
    tree = ast.parse(inspect.getsource(learning))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)
    joined = " ".join(names)
    for forbidden in ("executor", "measurement_close_bridge", "decision_apply",
                      "promote_insight", "wb_client", "ozon_client", "refuted_loop"):
        assert forbidden not in joined
