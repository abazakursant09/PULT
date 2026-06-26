"""
A4-bind — pricing_negative_margin → set_price at the observed BREAK-EVEN price.

break_even = (cogs/unit + logistics/unit) / (1 - commission_rate). Every term observed
(PhysicalProduct.cogs + ImportedFinanceRow). NEVER compute_recommendation, competitor,
ad_spend, target margin, forecast, AI, guess. Clamped to PricingRule [min,max]. WB+Ozon
executable; manual approval. margin_below_target stays advice-only. Measured net_profit.
"""
import asyncio
import ast
import inspect
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.product import Product
from models.product_listing import ProductListing
from models.physical_product import PhysicalProduct
from models.pricing_rule import PricingRule
from models.imported_finance import ImportedFinanceRow
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.pricing_signal import PricingSignal
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential

from services.marketplace import credential_vault
from services.marketplace.wb_client import wb_client
from services.marketplace.ozon_client import ozon_client
from services.action_binding import payload_builder as pb
from services.action_binding.payload_builder import build_action_payload
from services.action_binding.registry import BY_SIGNAL_TYPE
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_outcome.effect_measurement import _READERS
from services.action_binding.execution_bridge import execute_bound_decision

SIG = "pricing_negative_margin"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, mp="wb", sku="SKU1", price=100.0, cogs=50.0,
                commission=1000.0, logistics=100.0, revenue=10000.0, quantity=10,
                listing=True, finance=True, rule=None):
    phys_id = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys_id, user_id=uid, title="товар", cogs=cogs,
                           cogs_source="manual" if cogs is not None else None))
    pid = str(uuid.uuid4())
    db.add(Product(id=pid, user_id=uid, name="товар", marketplace=mp, sku=sku, price=price))
    if listing:
        db.add(ProductListing(physical_product_id=phys_id, user_id=uid, marketplace=mp,
                              external_id=sku))
    if finance:
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                                  date="2026-06-20", sku=sku, revenue=revenue,
                                  commission=commission, logistics=logistics, quantity=quantity))
    if rule is not None:
        lo, hi = rule
        db.add(PricingRule(product_id=pid, min_price=lo, max_price=hi))
    await db.commit()


async def _payload(db, uid, *, mp="wb", sku="SKU1"):
    return await build_action_payload(db, user_id=uid, signal_type=SIG, marketplace=mp, sku=sku)


# ── binding ──────────────────────────────────────────────────────────────────

def test_negative_margin_binds_set_price():
    b = BY_SIGNAL_TYPE[SIG]
    assert b.bindable and b.binding_status == "bound" and b.action_key == "set_price"
    assert b.required_capability == "prices" and b.safety_class == "manual_approval"


def test_margin_below_target_stays_advice_only():
    b = BY_SIGNAL_TYPE["pricing_margin_below_target"]
    assert b.bindable is False and b.action_key is None
    assert b.binding_status == "no_catalog_action"


def test_metric_net_profit_and_reader_exists():
    assert BY_SIGNAL_KEY[SIG].default_metric_key == "net_profit"
    assert "net_profit" in _READERS                    # Learning OS path unchanged


# ── successful payload + correct break-even math ─────────────────────────────

def test_payload_break_even_math():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # cogs 50, logistics/unit 100/10=10, commission_rate 1000/10000=0.1
        # break_even = (50+10)/0.9 = 66.67
        await _seed(db, uid, cogs=50.0, logistics=100.0, quantity=10,
                    commission=1000.0, revenue=10000.0, price=40.0)
        res = await _payload(db, uid)
        assert res.ok and res.action_key == "set_price"
        assert res.payload == {"offer_id": "SKU1", "price": 66.67, "old_price": 40.0}
        assert set(res.payload) <= {"offer_id", "price", "old_price"}
    _run(go())


# ── clamp to PricingRule bounds ──────────────────────────────────────────────

def test_clamp_to_min_price():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # break_even 66.67 < min 80 → clamped up to 80
        await _seed(db, uid, rule=(80.0, 9999.0))
        res = await _payload(db, uid)
        assert res.ok and res.payload["price"] == 80.0
    _run(go())


def test_clamp_to_max_price():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # break_even 66.67 > max 60 → clamped down to 60
        await _seed(db, uid, rule=(1.0, 60.0))
        res = await _payload(db, uid)
        assert res.ok and res.payload["price"] == 60.0
    _run(go())


# ── not derivable cases ──────────────────────────────────────────────────────

def _nd(**seed):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, **seed)
        res = await _payload(db, uid)
        assert res.ok is False and res.payload is None
        assert res.reason == "payload_not_derivable"
    _run(go())


def test_missing_cogs_not_derivable():
    _nd(cogs=None)


def test_missing_finance_not_derivable():
    _nd(finance=False)


def test_missing_listing_not_derivable():
    _nd(listing=False)


def test_zero_quantity_not_derivable():
    _nd(quantity=0)


def test_zero_revenue_not_derivable():
    _nd(revenue=0.0)


def test_invalid_commission_not_derivable():
    _nd(commission=10000.0, revenue=10000.0)   # commission_rate == 1.0 → undefined


# ── no compute_recommendation / competitor ───────────────────────────────────

def test_compute_recommendation_not_called(monkeypatch):
    import tasks.check_pricing as cp
    monkeypatch.setattr(cp, "compute_recommendation",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("forbidden")))
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid)
        res = await _payload(db, uid)
        assert res.ok and res.payload["price"] == 66.67
    _run(go())


def test_payload_builder_imports_no_competitor_or_recommendation():
    tree = ast.parse(inspect.getsource(pb))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    for bad in ("check_pricing", "competitor_analysis", "compute_recommendation"):
        assert all(bad not in m for m in mods), bad


# ── WB / Ozon apply path ─────────────────────────────────────────────────────

async def _seed_decision(db, uid, *, mp):
    label = "wildberries" if mp == "wb" else mp
    await _seed(db, uid, mp=mp)
    did = str(uuid.uuid4())
    ikey = f"pricing_negative_margin:{mp}:SKU1"
    db.add(Decision(id=did, user_id=uid, problem="pricing", action_key="set_price",
                    insight_key=ikey, status="open"))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=label,
                                 status="connected", scopes=["prices"],
                                 ozon_client_id="cid" if mp == "ozon" else None)
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    db.add(EngineSignalDecisionLink(user_id=uid, contour="pricing", signal_table="pricing_signal",
           signal_id="s1", insight_key=ikey, action_key="set_price", decision_id=did,
           link_status="promoted", marketplace=mp, sku="SKU1"))
    db.add(PricingSignal(user_id=uid, signal_key=SIG, problem_type="negative_margin",
           insight_key=ikey, marketplace=mp, sku="SKU1", status="promoted_to_decision"))
    await db.commit()
    return did


def test_wb_apply_calls_set_price(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_decision(db, uid, mp="wb")
        seen = []
        async def fake(*, token, offer_id, price, discount=None):
            seen.append((offer_id, price)); return {"requestId": "rq"}
        monkeypatch.setattr(wb_client, "set_price", fake)
        res = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                           marketplace="wb", sku="SKU1", dry_run=False)
        assert res.ok and res.status == "success"
        assert seen == [("SKU1", 66.67)]
    _run(go())


def test_ozon_apply_calls_set_price(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_decision(db, uid, mp="ozon")
        seen = []
        async def fake(*, token, client_id, offer_id, price):
            seen.append((offer_id, price)); return {"requestId": "rq"}
        monkeypatch.setattr(ozon_client, "set_price", fake)
        res = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                           marketplace="ozon", sku="SKU1", dry_run=False)
        assert res.ok and res.status == "success"
        assert seen == [("SKU1", 66.67)]
    _run(go())
