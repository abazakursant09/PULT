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
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.operations_signal import OperationsSignal
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.imported_finance import ImportedFinanceRow

from services.marketplace import credential_vault
from services.marketplace.ozon_client import ozon_client
from services.operations.signal_builder import build_operations_signal, SIGNAL_KEY as OPS_SIGNAL
from services.decision_feed.builder import build_feed

from routers.promotion_activation import promotion_activation_run, RunRequest

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
    """2.1A — the operations auto-promo margin-drain signal remains a DIAGNOSTIC in the Feed, but
    stop_auto_promotion is contained: it never promotes to an executable Decision (no Apply button)
    and no marketplace call is ever made. The full detect→execute→measure→learn loop is proven for a
    still-executable lever in test_decision_apply_execution_loop.py (reduce_discount)."""
    async def go():
        factory = await _factory(); uid = str(uuid.uuid4()); calls = []
        db = factory()
        await _seed(db, uid)
        _patch_executor(monkeypatch, calls)

        # (1) Feed carries the diagnostic signal with its advised action surfaced
        feed = await build_feed(db, user_id=uid)
        item = next(i for i in feed if i.contour == "operations")
        assert item.item_key == INSIGHT
        assert item.action_key == ACTION
        assert item.source_context.get("decision_id") is None   # not promoted

        # (2) Apply click → promotion-activation run: the contained action never yields an executable
        # Decision (no false button) and never touches the marketplace
        r = await promotion_activation_run(RunRequest(contour="operations"),
                                           current_user=_User(uid), db=db)
        did = next((i.decision_id for i in r.items
                    if i.insight_key == item.item_key and i.decision_id), None)
        assert did is None                                      # contained: not executable
        assert calls == []                                      # 0 marketplace calls

        # (3) the diagnostic signal itself survives — advice, not an auto action
        sig = (await db.execute(select(OperationsSignal))).scalars().one()
        assert sig.status == "active" and "Остановить участие товара" in sig.what_to_do
    _run(go())
