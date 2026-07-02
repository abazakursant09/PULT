"""
Closed Decision Loop v1 — PR 2 (proof, not new function).

Proves the EXISTING architecture closes the full loop through the real user surfaces
for one safe, reversible bound action (stop_auto_promotion, operations auto-promo
margin drain, measured on net_profit):

  Detect → Feed (bound signal, action_key surfaced)
  → Apply click → promotion-activation run endpoint → Decision
  → Preview endpoint → Confirm (records intent, executes via the bridge)
  → Marketplace Executor (stubbed) → baseline EngineEffectObservation
  → run_measurement_close() tick → closed observation with a proven band
  → Effect Summary in the feed → Learning OS bucket

Only the marketplace client is stubbed (reused Ozon stub). No production code is
changed by this test — it verifies connection, not new behaviour. Time is pinned to
the same T0/T1 model the service-level dual-contour e2e already proves.
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
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.operations_signal import OperationsSignal
from models.decision import Decision
from models.engine_effect_observation import EngineEffectObservation
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.imported_finance import ImportedFinanceRow

from services.marketplace import credential_vault
from services.marketplace.ozon_client import ozon_client
from services.operations.signal_builder import build_operations_signal, SIGNAL_KEY as OPS_SIGNAL
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_feed.builder import build_feed
from services.decision_apply_ux.confirm import confirm_and_apply_decision
from services.learning_os.registry import get_action_learning_summary
import tasks.measurement_close as mclose

from routers.promotion_activation import promotion_activation_run, RunRequest
from routers.decision_apply import decision_apply_preview

ACTION = "stop_auto_promotion"
METRIC = "net_profit"
SKU = "OPS1"
INSIGHT = f"{OPS_SIGNAL}:ozon:{SKU}"
T0 = datetime(2026, 6, 1)
T1 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


class _User:
    def __init__(self, uid):
        self.id = uid


async def _factory():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)


async def _fin(db, uid, *, date, net_profit):
    db.add(ImportedFinanceRow(import_id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                              date=date, sku=SKU, revenue=10000.0, net_profit=net_profit))


async def _seed(db, uid):
    phys = str(uuid.uuid4())
    db.add(PhysicalProduct(id=phys, user_id=uid, title="товар", cogs=50.0, cogs_source="manual"))
    db.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace="ozon", external_id=SKU))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                                 status="connected", scopes=["promotions"], ozon_client_id="cid")
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="promotions",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    # operations auto-promo margin drain → stop_auto_promotion, measured on net_profit
    await build_operations_signal(db, user_id=uid, marketplace="ozon", sku=SKU,
                                  net_profit=-100.0, in_auto_promotion=True)
    await _fin(db, uid, date="2026-06-01", net_profit=-200.0)   # loss baseline
    await db.commit()


def _patch_executor(monkeypatch, calls):
    async def fake(*, token, client_id, offer_id, enabled):
        calls.append((offer_id, enabled)); return {"requestId": "rq"}
    monkeypatch.setattr(ozon_client, "set_auto_promotion", fake)


def test_closed_decision_loop_e2e(monkeypatch):
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4()); calls = []
        db = factory()
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)
        # close tick owns its own session → point it at this in-memory DB
        monkeypatch.setattr(mclose, "AsyncSessionLocal", factory)

        # (1) Feed carries the bound signal with a surfaced action_key
        feed = await build_feed(db, user_id=uid)
        item = next(i for i in feed if i.contour == "operations")
        assert item.item_key == INSIGHT
        assert item.action_key == ACTION
        assert item.source_context.get("decision_id") is None   # not promoted yet

        # (2)(3) Apply click → promotion-activation run endpoint → Decision
        r = await promotion_activation_run(RunRequest(contour="operations"),
                                           current_user=_User(uid), db=db)
        did = next((i.decision_id for i in r.items
                    if i.insight_key == item.item_key and i.decision_id), None)
        assert did, "promotion did not yield a decision_id for the clicked signal"
        assert (await db.execute(select(Decision).where(Decision.id == did))).scalar_one()

        # (4) Preview endpoint
        pv = await decision_apply_preview(did, marketplace="ozon", sku=SKU,
                                          current_user=_User(uid), db=db)
        assert pv.applyable is True
        assert pv.action_key == ACTION

        # (5)(6) Confirm → executor + baseline measurement (time pinned to T0)
        cf = await confirm_and_apply_decision(
            db, user_id=uid, decision_id=did, marketplace="ozon", sku=SKU,
            idempotency_key="k-loop-1", now=T0)
        assert cf.ok and cf.status == "success"
        assert cf.measurement_opened is True
        assert calls == [(SKU, False)]                          # executor really ran

        # (7) baseline observation exists, still open
        obs = (await db.execute(select(EngineEffectObservation)
                                .where(EngineEffectObservation.user_id == uid))).scalars().all()
        assert len(obs) == 1 and obs[0].measured_at is None
        assert obs[0].metric_key == METRIC == BY_SIGNAL_KEY[OPS_SIGNAL].default_metric_key

        # observed improvement in the window, then (8) the close tick (own session)
        await _fin(db, uid, date="2026-06-20", net_profit=500.0)   # loss → profit
        await db.commit()
        n = await mclose.run_measurement_close(now=T1)
        assert n >= 1

        # steps 8-10 read on a FRESH session — the tick committed in its own session,
        # so the long-lived `db` above holds a stale (still-open) view of the observation.
        async with factory() as db2:
            # (8) observation closed with a proven band
            closed = (await db2.execute(select(EngineEffectObservation)
                                        .where(EngineEffectObservation.user_id == uid))).scalars().one()
            assert closed.measured_at is not None
            assert closed.effect_band == "improved"

            # (9) Effect Summary surfaces the proven effect in the feed
            feed2 = await build_feed(db2, user_id=uid, include_resolved=True)
            do = next(i for i in feed2 if i.contour == "decision_outcome")
            assert do.effect_status == "proven_improved"

            # (10) Learning OS counts the closed observation, keyed (mp, action, metric)
            summ = await get_action_learning_summary(
                db2, user_id=uid, marketplace="ozon", action_key=ACTION, metric_key=METRIC)
            assert summ is not None
            assert summ.total_count >= 1 and summ.improved_count >= 1
    _run(go())
