"""
A3-bind — pricing_price_below_floor → set_price (floor restore).

Only price_below_floor binds. new_price comes ONLY from PricingRule.min_price
(observed, rule-defined, deterministic) — never compute_recommendation, never
competitor prices, never forecast/AI/guess. WB+Ozon executable; Yandex/Megamarket
unsupported. Manual approval, no auto-apply. Measured on net_profit.
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
from models.pricing_rule import PricingRule
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
from services.action_binding.registry import BY_SIGNAL_TYPE, bound_signal_types
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.action_binding.execution_bridge import execute_bound_decision
from services.decision_apply_ux.preview import build_apply_preview, PAYLOAD_NOT_DERIVABLE


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_product(db, uid, *, mp="wb", sku="SKU1", price=100.0, min_price=150.0,
                        listing=True, rule=True, ext_id="SKU1"):
    pid = str(uuid.uuid4())
    db.add(Product(id=pid, user_id=uid, name="товар", marketplace=mp, sku=sku, price=price))
    if listing:
        db.add(ProductListing(physical_product_id="ph1", user_id=uid,
                              marketplace=mp, external_id=ext_id))
    if rule:
        db.add(PricingRule(product_id=pid, min_price=min_price, max_price=9999.0))
    await db.commit()


# ── (1)(2)(3) binding registry ───────────────────────────────────────────────

def test_price_below_floor_binds_set_price():
    b = BY_SIGNAL_TYPE["pricing_price_below_floor"]
    assert b.bindable and b.binding_status == "bound"
    assert b.action_key == "set_price" and b.required_capability == "prices"
    assert b.safety_class == "manual_approval"
    assert "set_price" in {x.action_key for x in BY_SIGNAL_TYPE.values() if x.bindable}


def test_other_pricing_signals_not_bound():
    for t in ("pricing_negative_margin", "pricing_margin_below_target"):
        b = BY_SIGNAL_TYPE[t]
        assert b.bindable is False and b.action_key is None
        assert b.binding_status == "no_catalog_action"


# ── (11) metric_key = net_profit ─────────────────────────────────────────────

def test_metric_key_net_profit():
    assert BY_SIGNAL_KEY["pricing_price_below_floor"].default_metric_key == "net_profit"


# ── (4) payload success: listing.external_id + PricingRule.min_price ─────────

def test_payload_success():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_product(db, uid, price=100.0, min_price=150.0)
        res = await build_action_payload(db, user_id=uid,
                                         signal_type="pricing_price_below_floor",
                                         marketplace="wb", sku="SKU1")
        assert res.ok and res.action_key == "set_price"
        assert res.payload == {"offer_id": "SKU1", "price": 150.0, "old_price": 100.0}
        assert set(res.payload) <= {"offer_id", "price", "old_price"}
    _run(go())


# ── (5)(6)(7)(8) not derivable cases ─────────────────────────────────────────

def _expect_not_derivable(**seed_kw):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_product(db, uid, **seed_kw)
        res = await build_action_payload(db, user_id=uid,
                                         signal_type="pricing_price_below_floor",
                                         marketplace="wb", sku="SKU1")
        assert res.ok is False and res.payload is None
        assert res.reason == "payload_not_derivable"
    _run(go())


def test_missing_listing_not_derivable():
    _expect_not_derivable(listing=False)


def test_missing_external_id_mismatch_not_derivable():
    _expect_not_derivable(ext_id="DIFFERENT")   # listing exists but external_id != sku


def test_missing_rule_not_derivable():
    _expect_not_derivable(rule=False)


def test_invalid_min_price_not_derivable():
    _expect_not_derivable(min_price=0.0)        # min_price <= 0


# ── (9)(10) compute_recommendation / competitors never used ──────────────────

def test_compute_recommendation_not_called(monkeypatch):
    import tasks.check_pricing as cp
    def boom(*a, **k):
        raise AssertionError("compute_recommendation must NOT be called for set_price")
    monkeypatch.setattr(cp, "compute_recommendation", boom)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_product(db, uid)
        res = await build_action_payload(db, user_id=uid,
                                         signal_type="pricing_price_below_floor",
                                         marketplace="wb", sku="SKU1")
        assert res.ok and res.payload["price"] == 150.0
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


# ── (12)(13)(15) WB / Ozon apply path + manual approval / no auto-apply ──────

async def _seed_decision(db, uid, *, mp, sku="SKU1"):
    await _seed_product(db, uid, mp=("wb" if mp == "wildberries" else mp), sku=sku)
    did = str(uuid.uuid4())
    ikey = f"pricing_price_below_floor:{('wb' if mp == 'wildberries' else mp)}:{sku}"
    db.add(Decision(id=did, user_id=uid, problem="pricing", action_key="set_price",
                    insight_key=ikey, status="open"))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                                 status="connected", scopes=["prices"],
                                 ozon_client_id="cid" if mp == "ozon" else None)
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    db.add(EngineSignalDecisionLink(user_id=uid, contour="pricing",
           signal_table="pricing_signal", signal_id="s1", insight_key=ikey,
           action_key="set_price", decision_id=did, link_status="promoted",
           marketplace=("wb" if mp == "wildberries" else mp), sku=sku))
    db.add(PricingSignal(user_id=uid, signal_key="pricing_price_below_floor",
           problem_type="price_below_floor", insight_key=ikey,
           marketplace=("wb" if mp == "wildberries" else mp), sku=sku, status="promoted_to_decision"))
    await db.commit()
    return did


def test_wb_apply_calls_set_price(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_decision(db, uid, mp="wildberries")
        seen = []
        async def fake(*, token, offer_id, price, discount=None):
            seen.append((offer_id, price)); return {"requestId": "rq"}
        monkeypatch.setattr(wb_client, "set_price", fake)
        # preview (dry_run) must NOT hit the marketplace (no auto-apply)
        p = await build_apply_preview(db, user_id=uid, decision_id=did,
                                      marketplace="wb", sku="SKU1")
        assert p.applyable is True and p.action_key == "set_price"
        assert p.payload == {"offer_id": "SKU1", "price": 150.0, "old_price": 100.0}
        assert p.safety_class == "manual_approval"
        assert seen == []                          # dry-run preview: no apply
        # explicit apply
        res = await execute_bound_decision(db, user_id=uid, decision_id=did,
                                           marketplace="wb", sku="SKU1", dry_run=False)
        assert res.ok and res.status == "success"
        assert seen == [("SKU1", 150.0)]           # set_price called with the floor
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
        assert seen == [("SKU1", 150.0)]
    _run(go())


# ── (14) Yandex / Megamarket unsupported ─────────────────────────────────────

def test_yandex_unsupported_in_preview(monkeypatch):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed_decision(db, uid, mp="yandex")
        p = await build_apply_preview(db, user_id=uid, decision_id=did,
                                      marketplace="yandex", sku="SKU1")
        assert p.applyable is False and p.reason == "unsupported_capability"
        assert p.capability_ok is False
    _run(go())


def test_megamarket_unsupported_capability():
    from services.decision_outcome.decision_bridge import capability_supported
    assert capability_supported("set_price", "wildberries") is True
    assert capability_supported("set_price", "ozon") is True
    assert capability_supported("set_price", "yandex") is False
    assert capability_supported("set_price", "megamarket") is False
