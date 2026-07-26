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
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
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
from services.operations.signal_builder import build_operations_signal
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
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


# 2.1A: stop_auto_promotion is contained (capability unsupported). This suite proved the Learning-Key
# doctrine (two buckets, never pooled) by DRIVING both contours through the executor; that doctrine
# is now covered by direct-observation tests in test_learning_os.py. Here we prove containment: both
# contours still surface their DIAGNOSTIC, but neither promotes to an executable Decision and neither
# touches the marketplace.
async def _seed_and_bridge(monkeypatch):
    db = await _engine(); uid = str(uuid.uuid4()); calls = []
    await _seed(db, uid)
    _patch_executor(monkeypatch, calls)
    await promote_eligible_candidates(db, user_id=uid); await db.commit()
    await bridge_links_to_decisions(db, user_id=uid); await db.commit()
    return db, uid, calls


# ── (5) neither contour promotes the contained action to an executable Decision ─

def test_distinct_learning_buckets(monkeypatch):
    async def go():
        db, uid, calls = await _seed_and_bridge(monkeypatch)
        decisions = (await db.execute(
            select(Decision).where(Decision.user_id == uid))).scalars().all()
        assert [d for d in decisions if d.action_key == ACTION] == []   # contained: not promoted
        assert calls == []                                              # 0 marketplace calls
    _run(go())


# ── (6) no effect observations exist for a contained action ───────────────────

def test_summaries_independent(monkeypatch):
    async def go():
        db, uid, calls = await _seed_and_bridge(monkeypatch)
        obs = (await db.execute(select(EngineEffectObservation))).scalars().all()
        assert obs == []                                              # nothing executed → nothing measured
        assert calls == []
    _run(go())


# ── (7) both diagnostics still surface in the Feed, without an executable button ─

def test_feed_shows_both(monkeypatch):
    async def go():
        db, uid, calls = await _seed_and_bridge(monkeypatch)
        items = await build_feed(db, user_id=uid, include_resolved=True)
        action_items = [it for it in items if it.action_key == ACTION]
        assert action_items, "the advised action still shows as diagnostic"
        assert all(it.source_context.get("decision_id") is None for it in action_items)
        assert calls == []
    _run(go())
