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


# ── (1)(2)(3) Ozon fails honestly as CAPABILITY_NOT_SUPPORTED ─────────────────

def test_ozon_ad_set_state_capability_not_supported():
    async def go():
        db, uid = await _setup("ozon")
        res = await executor.execute(
            db=db, user_id=uid, action_type="ad_set_state",
            payload={"marketplace": "ozon", "campaign_id": 7, "action": "pause"},
        )
        assert res.status == "failed"
        code = res.error["code"]
        assert code == ExecutionError.CAPABILITY_NOT_SUPPORTED       # (2)
        assert code != ExecutionError.VALIDATION                     # (1) not generic validation
        detail = (res.error.get("detail") or "").lower()
        assert "performance oauth" in detail                          # (3) clear reason
        assert "not implemented" in detail
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


# ── (5) bridge cannot present Ozon ad_set_state as executable ─────────────────

def test_ad_set_state_is_not_bound_to_any_signal():
    # No action binding produces ad_set_state, so the Decision bridge can never
    # promote/bind it (Ozon or otherwise) until the adapter exists.
    from services.action_binding import registry as binding_registry
    bound = {b.action_key for b in binding_registry.ACTION_BINDINGS if b.bindable}
    assert "ad_set_state" not in bound
    assert "ad_set_bid" not in bound
