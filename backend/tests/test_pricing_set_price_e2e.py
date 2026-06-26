"""
A3.1 — end-to-end verification of the pricing floor-restore loop:

  PricingSignal(pricing_price_below_floor) → promote → bridge → Decision(set_price)
  → preview → apply (set_price) → measurement open → close (net_profit) → effect band
  → Learning OS aggregation.

Pricing equivalent of the advertising overspend E2E. Proves the wired pieces connect
for WB and Ozon, with marketplace isolation. price comes ONLY from PricingRule.min_price
(never compute_recommendation / competitor / forecast / AI). Measured on net_profit
(NOT ad_profit_impact / ad_cost_ratio). Manual approval, no auto-apply.
"""
import asyncio
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
from models.product_listing import ProductListing
from models.pricing_rule import PricingRule
from models.pricing_signal import PricingSignal
from models.decision import Decision
from models.engine_effect_observation import EngineEffectObservation
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.imported_finance import ImportedFinanceRow

from services.marketplace import credential_vault
from services.marketplace.wb_client import wb_client
from services.marketplace.ozon_client import ozon_client
import tasks.check_pricing as check_pricing
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.action_binding.registry import BY_SIGNAL_TYPE
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.decision_outcome.effect_measurement import (
    open_effect_measurement, close_effect_measurement, IMPROVED, NOT_EVALUATED,
)
from services.decision_apply_ux.preview import build_apply_preview
from services.action_binding.execution_bridge import execute_bound_decision
from services.learning_os.registry import get_action_learning_summary

T0 = datetime(2026, 6, 1)
T1 = datetime(2026, 6, 21)
MIN_PRICE = 150.0


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, mp, sku="SKU1"):
    pid = str(uuid.uuid4())
    db.add(Product(id=pid, user_id=uid, name="товар", marketplace=mp, sku=sku, price=100.0))
    db.add(ProductListing(physical_product_id="ph1", user_id=uid, marketplace=mp, external_id=sku))
    db.add(PricingRule(product_id=pid, min_price=MIN_PRICE, max_price=9999.0))
    conn_label = "wildberries" if mp == "wb" else mp
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=conn_label,
                                 status="connected", scopes=["prices"],
                                 ozon_client_id="cid" if mp == "ozon" else None)
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    ikey = f"pricing_price_below_floor:{mp}:{sku}"
    db.add(PricingSignal(user_id=uid, signal_key="pricing_price_below_floor",
           problem_type="price_below_floor", insight_key=ikey, marketplace=mp, sku=sku,
           status="active", what="x", priority_level="high", category="pricing"))
    await db.commit()


async def _fin(db, uid, *, mp, sku, date, net_profit):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                              date=date, sku=sku, revenue=10000.0, net_profit=net_profit))
    await db.commit()


def _patch_set_price(monkeypatch, mp, calls):
    if mp == "wb":
        async def fake(*, token, offer_id, price, discount=None):
            calls.append((offer_id, price)); return {"requestId": "rq"}
        monkeypatch.setattr(wb_client, "set_price", fake)
    else:
        async def fake(*, token, client_id, offer_id, price):
            calls.append((offer_id, price)); return {"requestId": "rq"}
        monkeypatch.setattr(ozon_client, "set_price", fake)


def _explode_recommendation(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("compute_recommendation must NEVER be called in the pricing loop")
    monkeypatch.setattr(check_pricing, "compute_recommendation", boom)


async def _promote(db, uid):
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()


async def _full_loop(monkeypatch, *, mp):
    _explode_recommendation(monkeypatch)        # compute_recommendation must never fire
    calls = []
    _patch_set_price(monkeypatch, mp, calls)
    db = await _engine(); uid = str(uuid.uuid4()); sku = "SKU1"
    await _seed(db, uid, mp=mp, sku=sku)

    # 1) promote + bridge → Decision(set_price), manual approval
    await _promote(db, uid)
    decision = (await db.execute(select(Decision))).scalars().one()
    assert decision.action_key == "set_price"
    assert BY_SIGNAL_TYPE["pricing_price_below_floor"].safety_class == "manual_approval"
    assert BY_SIGNAL_KEY["pricing_price_below_floor"].default_metric_key == "net_profit"

    # 2)(3) preview: payload = {offer_id, price=min_price, old_price}; no adapter call
    p = await build_apply_preview(db, user_id=uid, decision_id=decision.id, marketplace=mp, sku=sku)
    assert p.applyable is True and p.action_key == "set_price"
    assert p.payload == {"offer_id": "SKU1", "price": MIN_PRICE, "old_price": 100.0}
    assert p.payload["price"] == MIN_PRICE          # the FLOOR, not a recommendation
    assert calls == []                              # dry-run preview: marketplace untouched

    # 4) real apply → set_price called exactly once with the floor
    res = await execute_bound_decision(db, user_id=uid, decision_id=decision.id,
                                       marketplace=mp, sku=sku, dry_run=False)
    assert res.ok and res.status == "success"
    assert calls == [("SKU1", MIN_PRICE)]

    # 5) measurement open on net_profit (NOT ad_profit_impact / ad_cost_ratio)
    await _fin(db, uid, mp=mp, sku=sku, date="2026-06-01", net_profit=100.0)   # baseline low
    await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
    obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
    assert obs.metric_key == "net_profit"
    assert obs.metric_key not in ("ad_profit_impact", "ad_cost_ratio")
    assert json.loads(obs.evidence)["baseline"] == 100.0

    # 6) net_profit recovers → close → improved (higher is better)
    await _fin(db, uid, mp=mp, sku=sku, date="2026-06-20", net_profit=500.0)
    await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()
    obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
    assert obs.effect_band == IMPROVED

    # 7) Learning OS aggregates the improved set_price/net_profit outcome
    summ = await get_action_learning_summary(db, user_id=uid, marketplace=mp, action_key="set_price")
    assert summ.improved_count == 1 and summ.marketplace == mp
    return db, uid


def test_wb_full_loop_improved(monkeypatch):
    _run(_full_loop(monkeypatch, mp="wb"))


def test_ozon_full_loop_improved(monkeypatch):
    _run(_full_loop(monkeypatch, mp="ozon"))


# ── unavailable metric → not_evaluated, no fake zero ─────────────────────────

def test_unavailable_metric_not_evaluated(monkeypatch):
    _explode_recommendation(monkeypatch)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); sku = "SKU1"
        await _seed(db, uid, mp="wb", sku=sku)
        await _promote(db, uid)
        # baseline finance present, but NO post-action finance in the close window
        await _fin(db, uid, mp="wb", sku=sku, date="2026-06-01", net_profit=100.0)
        await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
        await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()
        obs = (await db.execute(select(EngineEffectObservation))).scalars().one()
        assert obs.effect_band == NOT_EVALUATED
        assert "after" not in json.loads(obs.evidence)        # never a fabricated 0
    _run(go())


# ── (8) marketplace isolation in Learning OS ─────────────────────────────────

def test_learning_marketplace_isolation(monkeypatch):
    db, uid = _run(_full_loop(monkeypatch, mp="wb"))
    async def check():
        wb = await get_action_learning_summary(db, user_id=uid, marketplace="wb", action_key="set_price")
        oz = await get_action_learning_summary(db, user_id=uid, marketplace="ozon", action_key="set_price")
        assert wb.improved_count == 1
        assert oz is None or oz.total_count == 0      # WB never leaked into Ozon
    _run(check())
