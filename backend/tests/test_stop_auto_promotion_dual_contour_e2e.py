"""
Regression E2E — one action_key, two problem spaces, NEVER pooled.

Advertising (indirect: ad_on_low_stock) and Operations (auto_promo_margin_drain)
BOTH bind `stop_auto_promotion` on `ozon`, for the same seller, at the same time.
They must stay independent through the WHOLE loop because their metric_key differs:

  advertising -> (ozon, stop_auto_promotion, ad_profit_impact)
  operations  -> (ozon, stop_auto_promotion, net_profit)

Locks the Learning Key Doctrine (docs/learning-key-doctrine.md) end-to-end:
distinct Learning buckets, independent summaries, and both items in the Feed.
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
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.advertising_signal import AdvertisingSignal
from models.decision import Decision
from models.engine_effect_observation import EngineEffectObservation
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.imported_finance import ImportedFinanceRow

from services.marketplace import credential_vault
from services.marketplace.ozon_client import ozon_client
from services.operations.signal_builder import build_operations_signal, SIGNAL_KEY as OPS_SIGNAL
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.decision_outcome.effect_measurement import (
    open_effect_measurement, close_effect_measurement, IMPROVED,
)
from services.action_binding.execution_bridge import execute_bound_decision
from services.learning_os.registry import (
    aggregate_learning_observations, get_action_learning_summary,
)
from services.decision_feed.builder import build_feed

ADV_SIGNAL = "adv_ad_on_low_stock"
ACTION = "stop_auto_promotion"
ADV_METRIC = "ad_profit_impact"
OPS_METRIC = "net_profit"
ADV_SKU = "ADV1"
OPS_SKU = "OPS1"
T0 = datetime(2026, 6, 1)
T1 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _listing(db, uid, sku):
    phys = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys, user_id=uid, title="товар", cogs=50.0, cogs_source="manual"))
    db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace="ozon", external_id=sku))


async def _fin(db, uid, *, sku, date, net_profit):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                              date=date, sku=sku, revenue=10000.0, net_profit=net_profit))


async def _seed(db, uid):
    """One ozon cabinet, two listings, two signals (advertising + operations),
    each with a thin loss baseline."""
    await _listing(db, uid, ADV_SKU)
    await _listing(db, uid, OPS_SKU)
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                                 status="connected", scopes=["promotions"], ozon_client_id="cid")
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="promotions",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    # advertising indirect signal → stop_auto_promotion (measured on ad_profit_impact)
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid, signal_key=ADV_SIGNAL,
           problem_type="ad_on_low_stock", insight_key=f"{ADV_SIGNAL}:ozon:{ADV_SKU}",
           marketplace="ozon", sku=ADV_SKU, status="active", what="x", why="y",
           expected_effect="z", what_to_do="w", priority_level="high"))
    # operations signal → stop_auto_promotion (measured on net_profit)
    await build_operations_signal(db, user_id=uid, marketplace="ozon", sku=OPS_SKU,
                                  net_profit=-100.0, in_auto_promotion=True)
    await _fin(db, uid, sku=ADV_SKU, date="2026-06-01", net_profit=-200.0)
    await _fin(db, uid, sku=OPS_SKU, date="2026-06-01", net_profit=-200.0)
    await db.commit()


def _patch_executor(monkeypatch, calls):
    async def fake(*, token, client_id, offer_id, enabled):
        calls.append((offer_id, enabled)); return {"requestId": "rq"}
    monkeypatch.setattr(ozon_client, "set_auto_promotion", fake)


async def _decision_for(db, uid, insight_prefix):
    rows = (await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all()
    return next(d for d in rows if (d.insight_key or "").startswith(insight_prefix))


async def _run_full_dual_loop(monkeypatch):
    db = await _engine(); uid = str(uuid.uuid4()); calls = []
    await _seed(db, uid)
    _patch_executor(monkeypatch, calls)

    # promote + bridge → TWO decisions, both stop_auto_promotion, different insights
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()
    adv_d = await _decision_for(db, uid, ADV_SIGNAL)
    ops_d = await _decision_for(db, uid, OPS_SIGNAL)
    assert adv_d.action_key == ACTION and ops_d.action_key == ACTION       # (1) same action_key
    assert BY_SIGNAL_KEY[ADV_SIGNAL].default_metric_key == ADV_METRIC      # (3)
    assert BY_SIGNAL_KEY[OPS_SIGNAL].default_metric_key == OPS_METRIC      # (4)
    assert ADV_METRIC != OPS_METRIC                                        # (2) metric differs

    # apply both
    for d, sku in ((adv_d, ADV_SKU), (ops_d, OPS_SKU)):
        res = await execute_bound_decision(db, user_id=uid, decision_id=d.id,
                                           marketplace="ozon", sku=sku, dry_run=False)
        assert res.ok and res.status == "success"
    assert sorted(calls) == sorted([(ADV_SKU, False), (OPS_SKU, False)])

    # open + close measurement for BOTH (loss → profit on each)
    await open_effect_measurement(db, user_id=uid, window_days=14, now=T0); await db.commit()
    await _fin(db, uid, sku=ADV_SKU, date="2026-06-20", net_profit=500.0)
    await _fin(db, uid, sku=OPS_SKU, date="2026-06-20", net_profit=500.0)
    await db.commit()
    await close_effect_measurement(db, user_id=uid, now=T1); await db.commit()
    return db, uid


# ── (5) two distinct Learning buckets, split on metric_key ────────────────────

def test_distinct_learning_buckets(monkeypatch):
    async def go():
        db, uid = await _run_full_dual_loop(monkeypatch)
        obs = (await db.execute(select(EngineEffectObservation))).scalars().all()
        metrics = {o.metric_key for o in obs}
        assert metrics == {ADV_METRIC, OPS_METRIC}                 # (3)+(4)
        assert all(o.effect_band == IMPROVED for o in obs)

        buckets = await aggregate_learning_observations(db, user_id=uid)
        keys = {(b.marketplace, b.action_key, b.metric_key) for b in buckets}
        assert ("ozon", ACTION, ADV_METRIC) in keys
        assert ("ozon", ACTION, OPS_METRIC) in keys                # (5) two buckets
        assert len([b for b in buckets if b.action_key == ACTION]) == 2
    _run(go())


# ── (6) per-metric summaries stay independent (ranking independence) ──────────

def test_summaries_independent(monkeypatch):
    async def go():
        db, uid = await _run_full_dual_loop(monkeypatch)
        adv = await get_action_learning_summary(db, user_id=uid, marketplace="ozon",
                                                action_key=ACTION, metric_key=ADV_METRIC)
        ops = await get_action_learning_summary(db, user_id=uid, marketplace="ozon",
                                                action_key=ACTION, metric_key=OPS_METRIC)
        assert adv.improved_count == 1 and adv.metric_key == ADV_METRIC
        assert ops.improved_count == 1 and ops.metric_key == OPS_METRIC
        # each bucket counts ONLY its own metric's single observation — no pooling
        assert adv.total_count == 1 and ops.total_count == 1
    _run(go())


# ── (7) Feed shows BOTH scenarios, no frontend change ─────────────────────────

def test_feed_shows_both(monkeypatch):
    async def go():
        db, uid = await _run_full_dual_loop(monkeypatch)
        items = await build_feed(db, user_id=uid, include_resolved=True)
        # both ride the generic decision_outcome feed path (not _ENGINES); the origin
        # problem is carried by the canonical insight_key (group_key) + sku, not contour.
        action_items = [it for it in items if it.action_key == ACTION]
        groups = " ".join(it.group_key or "" for it in action_items)
        assert ADV_SIGNAL in groups and OPS_SIGNAL in groups       # both scenarios present
        assert {ADV_SKU, OPS_SKU} <= {it.sku for it in action_items}
        assert all(it.effect_band == IMPROVED for it in action_items)
    _run(go())
