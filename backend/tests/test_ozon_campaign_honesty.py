"""
A2.2-pre-b.1 — Ozon ad_set_state honesty guard.

The capability registry lists campaign_control for Ozon as api-supported, but the
adapter (Performance API, separate OAuth) is not wired. Ozon ad_set_state must
therefore fail HONESTLY as CAPABILITY_NOT_SUPPORTED with a clear reason — never a
generic VALIDATION error that hides a missing integration, and never a fake success.
WB ad_set_state is unaffected. No binding promotes ad_set_state, so the bridge
cannot present Ozon ad_set_state as executable.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog          # noqa: F401
from models.automation_rule import AutomationRule      # noqa: F401
from services.marketplace import executor, credential_vault
from services.marketplace.errors import ExecutionError
from services.marketplace.wb_client import wb_client


def _run(c):
    return asyncio.run(c)


async def _setup(marketplace="ozon"):
    engine = create_async_engine("sqlite+aiosqlite://",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    uid = str(uuid.uuid4())
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                 status="connected", scopes=["advert"],
                                 ozon_client_id="cid" if marketplace == "ozon" else None)
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="advert",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return db, uid


# ── Ozon without the Performance credential fails honestly (guard replaced) ───
# A2.2-pre-b.4 wired the real Ozon path: campaign_control now authenticates via the
# advert_performance grant. A connection lacking that scope/credential fails
# honestly as MISSING_SCOPE — never a generic VALIDATION, never a fake success.

def test_ozon_ad_set_state_without_performance_scope_missing_scope():
    async def go():
        db, uid = await _setup("ozon")   # connection has only "advert", not advert_performance
        res = await executor.execute(
            db=db, user_id=uid, action_type="ad_set_state",
            payload={"marketplace": "ozon", "campaign_id": 7, "action": "pause"},
        )
        assert res.status == "rejected"
        code = res.error["code"]
        assert code == ExecutionError.MISSING_SCOPE
        assert code != ExecutionError.VALIDATION                       # not generic validation
        assert "advert_performance" in (res.error.get("detail") or "")
    _run(go())


# ── (4) WB ad_set_state still works ──────────────────────────────────────────

def test_wb_ad_set_state_still_succeeds():
    async def go():
        db, uid = await _setup("wildberries")
        async def fake(*, token, campaign_id, action):
            return {"requestId": f"rq-{action}"}
        wb_client.set_campaign_state = fake
        res = await executor.execute(
            db=db, user_id=uid, action_type="ad_set_state",
            payload={"marketplace": "wildberries", "campaign_id": 7, "action": "pause"},
        )
        assert res.status == "success" and res.result["state"] == "pause"
    _run(go())


# ── (5) ad_set_state binding scope (A2.2-bind) ────────────────────────────────

def test_ad_set_state_bound_to_overspend_only():
    # A2.2-bind: ad_set_state is bound to the 3 direct-overspend types (campaign
    # pause); ad_set_bid stays unbound (cpm would be a forecast).
    from services.action_binding import registry as binding_registry
    bound = {b.signal_type for b in binding_registry.ACTION_BINDINGS
             if b.bindable and b.action_key == "ad_set_state"}
    assert bound == {"adv_ad_destroying_profit", "adv_ad_spend_without_sales",
                     "adv_ad_on_unprofitable_product"}
    all_bound = {b.action_key for b in binding_registry.ACTION_BINDINGS if b.bindable}
    assert "ad_set_bid" not in all_bound
