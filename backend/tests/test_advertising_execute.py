"""ME-4 — advertising bid/state changes through the shared executor."""
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
from services.marketplace.wb_client import wb_client


def _run(c):
    return asyncio.run(c)


async def _setup(scope="advert"):
    engine = create_async_engine("sqlite+aiosqlite://",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    uid = str(uuid.uuid4())
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
                                 status="connected", scopes=[scope])
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="advert",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return db, uid


def test_set_bid_success_and_revert():
    async def go():
        db, uid = await _setup()
        seen = []
        async def fake(*, token, campaign_id, cpm, adv_type, param=None):
            seen.append(cpm); return {"requestId": "rq"}
        wb_client.set_bid = fake
        res = await executor.execute(
            db=db, user_id=uid, action_type="ad_set_bid",
            payload={"marketplace": "wildberries", "campaign_id": 7, "cpm": 210,
                     "adv_type": 8, "old_cpm": 320},
        )
        assert res.status == "success" and res.reversible
        rev = await executor.revert(db=db, user_id=uid, log_id=res.log_id)
        assert rev.status == "success"
        assert seen == [210, 320]    # revert restored old cpm
    _run(go())


def test_set_state_pause():
    async def go():
        db, uid = await _setup()
        async def fake(*, token, campaign_id, action):
            return {"requestId": f"rq-{action}"}
        wb_client.set_campaign_state = fake
        res = await executor.execute(
            db=db, user_id=uid, action_type="ad_set_state",
            payload={"marketplace": "wildberries", "campaign_id": 7, "action": "pause"},
        )
        assert res.status == "success" and res.result["state"] == "pause"
    _run(go())


def test_bid_guard_max_step_l4():
    async def go():
        db, uid = await _setup()
        wb_client.set_bid = lambda **k: (_ for _ in ()).throw(AssertionError("no dispatch"))
        res = await executor.execute(
            db=db, user_id=uid, action_type="ad_set_bid",
            payload={"marketplace": "wildberries", "campaign_id": 7, "cpm": 100,
                     "adv_type": 8, "step_pct": 80},
            mode="automated_l4",
            rule={"enabled": True, "guard": {"max_step_pct": 25}},
        )
        assert res.status == "rejected" and res.error["code"] == "GUARD_MAX_STEP"
    _run(go())


def test_invalid_state_action():
    async def go():
        db, uid = await _setup()
        res = await executor.execute(
            db=db, user_id=uid, action_type="ad_set_state",
            payload={"marketplace": "wildberries", "campaign_id": 7, "action": "explode"},
        )
        assert res.status == "rejected" and res.error["code"] == "VALIDATION"
    _run(go())
