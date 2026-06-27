"""
A4-margin-target — pricing_margin_below_target → set_price at a cost-plus price.

target_margin = PricingRule.target_margin_pct/100 (persisted, seller-defined).
cost_plus = (cogs/unit + logistics/unit) / (1 - commission_rate - target_margin).
Observed inputs + the seller's explicit target. NEVER compute_recommendation,
competitor, target_percent, PricingThresholds, ad_spend, forecast, AI, guess.
"""
import asyncio
import ast
import inspect
import uuid

from sqlalchemy import select, inspect as sa_inspect
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
from services.action_binding.execution_bridge import execute_bound_decision

SIG = "pricing_margin_below_target"
COST_PLUS = 92.31   # (cogs50 + log/unit10) / (1 - 0.1 - 0.25)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, mp="wb", sku="SKU1", price=80.0, cogs=50.0,
                commission=1000.0, logistics=100.0, revenue=10000.0, quantity=10,
                target_margin_pct=25.0, min_price=1.0, max_price=9999.0,
                listing=True, finance=True, rule=True):
    phys = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys, user_id=uid, title="товар", cogs=cogs,
                           cogs_source="manual" if cogs is not None else None))
    pid = str(uuid.uuid4())
    db.add(Product(id=pid, user_id=uid, name="товар", marketplace=mp, sku=sku, price=price))
    if listing:
        db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace=mp, external_id=sku))
    if finance:
        db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                                  date="2026-06-20", sku=sku, revenue=revenue, commission=commission,
                                  logistics=logistics, quantity=quantity))
    if rule:
        db.add(PricingRule(product_id=pid, min_price=min_price, max_price=max_price,
                           target_margin_pct=target_margin_pct))
    await db.commit()


async def _payload(db, uid, *, mp="wb", sku="SKU1"):
    return await build_action_payload(db, user_id=uid, signal_type=SIG, marketplace=mp, sku=sku)


# ── (1)(2) migration / schema ────────────────────────────────────────────────

def test_target_margin_column_nullable():
    cols = {c.name: c for c in PricingRule.__table__.columns}
    assert "target_margin_pct" in cols and cols["target_margin_pct"].nullable is True


def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["tm1c1a2b3c4d02"], heads


def test_migration_additive_only():
    import pathlib
    src = pathlib.Path("alembic/versions/tm1c1a2b3c4d02_pricing_rule_target_margin.py").read_text(encoding="utf-8")
    up = src.split("def upgrade")[1].split("def downgrade")[0]
    assert "op.add_column(" in up
    for bad in ("op.drop_table", "op.drop_column", "op.alter_column", "op.execute"):
        assert bad not in up


# ── (3)(4)(5) bindings ───────────────────────────────────────────────────────

def test_margin_below_target_binds_set_price():
    b = BY_SIGNAL_TYPE[SIG]
    assert b.bindable and b.binding_status == "bound" and b.action_key == "set_price"
    assert b.required_capability == "prices" and b.safety_class == "manual_approval"


def test_floor_and_break_even_unchanged():
    assert BY_SIGNAL_TYPE["pricing_price_below_floor"].action_key == "set_price"
    assert BY_SIGNAL_TYPE["pricing_negative_margin"].action_key == "set_price"


# ── (23) metric ──────────────────────────────────────────────────────────────

def test_metric_net_profit():
    assert BY_SIGNAL_KEY[SIG].default_metric_key == "net_profit"


# ── (6)(7) payload success + cost-plus math ──────────────────────────────────

def test_payload_cost_plus_math():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, price=80.0)
        res = await _payload(db, uid)
        assert res.ok and res.action_key == "set_price"
        assert res.payload == {"offer_id": "SKU1", "price": COST_PLUS, "old_price": 80.0}
        assert set(res.payload) <= {"offer_id", "price", "old_price"}
    _run(go())


# ── (8)(9) clamp ─────────────────────────────────────────────────────────────

def test_clamp_to_min_price():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, min_price=100.0)   # 92.31 < 100 → clamp up
        res = await _payload(db, uid)
        assert res.ok and res.payload["price"] == 100.0
    _run(go())


def test_clamp_to_max_price():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, max_price=85.0)    # 92.31 > 85 → clamp down
        res = await _payload(db, uid)
        assert res.ok and res.payload["price"] == 85.0
    _run(go())


# ── (10-16) not derivable ────────────────────────────────────────────────────

def _nd(**seed):
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid, **seed)
        res = await _payload(db, uid)
        assert res.ok is False and res.payload is None and res.reason == "payload_not_derivable"
    _run(go())


def test_null_target_margin_not_derivable():
    _nd(target_margin_pct=None)


def test_no_rule_not_derivable():
    _nd(rule=False)


def test_target_margin_zero_not_derivable():
    _nd(target_margin_pct=0.0)


def test_target_margin_ge_100_not_derivable():
    _nd(target_margin_pct=100.0)


def test_no_cogs_not_derivable():
    _nd(cogs=None)


def test_no_finance_not_derivable():
    _nd(finance=False)


def test_invalid_denominator_not_derivable():
    # commission_rate 0.8 + target 0.25 = 1.05 → denom <= 0
    _nd(commission=8000.0, revenue=10000.0, target_margin_pct=25.0)


def test_no_listing_not_derivable():
    _nd(listing=False)


def test_zero_quantity_not_derivable():
    _nd(quantity=0)


def test_zero_revenue_not_derivable():
    _nd(revenue=0.0)


# ── (17)(18)(19) forbidden sources not used ──────────────────────────────────

def test_compute_recommendation_not_called(monkeypatch):
    import tasks.check_pricing as cp
    monkeypatch.setattr(cp, "compute_recommendation",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("forbidden")))
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid)
        res = await _payload(db, uid)
        assert res.ok and res.payload["price"] == COST_PLUS
    _run(go())


def test_payload_builder_forbidden_imports():
    tree = ast.parse(inspect.getsource(pb))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    for bad in ("check_pricing", "competitor_analysis", "compute_recommendation",
                "pricing.rules", "PricingThresholds"):
        assert all(bad not in m for m in mods), bad


def test_target_percent_not_accessed_in_builder():
    # the competitor-relative PricingRule.target_percent attribute must never be read
    # (docstring mentions of the word in a NEVER-list don't count — check attribute access)
    src = inspect.getsource(pb)
    assert ".target_percent" not in src
    assert "PricingThresholds(" not in src and "import PricingThresholds" not in src


# ── (20)(21)(22)(24) apply path ──────────────────────────────────────────────

async def _seed_decision(db, uid, *, mp):
    label = "wildberries" if mp == "wb" else mp
    await _seed(db, uid, mp=mp)
    did = str(uuid.uuid4())
    ikey = f"pricing_margin_below_target:{mp}:SKU1"
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
    db.add(PricingSignal(user_id=uid, signal_key=SIG, problem_type="margin_below_target",
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
        assert res.ok and res.status == "success" and seen == [("SKU1", COST_PLUS)]
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
        assert res.ok and res.status == "success" and seen == [("SKU1", COST_PLUS)]
    _run(go())


def test_yandex_megamarket_unsupported():
    from services.decision_outcome.decision_bridge import capability_supported
    assert capability_supported("set_price", "wildberries") is True
    assert capability_supported("set_price", "ozon") is True
    assert capability_supported("set_price", "yandex") is False
    assert capability_supported("set_price", "megamarket") is False
