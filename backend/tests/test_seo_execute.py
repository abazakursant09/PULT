"""ME-5 — SEO card update through the shared executor."""
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


async def _setup(scope="content"):
    engine = create_async_engine("sqlite+aiosqlite://",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    uid = str(uuid.uuid4())
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
                                 status="connected", scopes=[scope])
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="content",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return db, uid


def test_update_card_success_sets_nmid():
    async def go():
        db, uid = await _setup()
        captured = {}
        async def fake(*, token, card):
            captured["card"] = card; return {"requestId": "rq"}
        wb_client.update_card = fake
        res = await executor.execute(
            db=db, user_id=uid, action_type="update_card",
            payload={"marketplace": "wildberries", "offer_id": "555",
                     "card": {"title": "Новый SEO-заголовок"}},
        )
        assert res.status == "success", res.error
        assert captured["card"]["nmID"] == 555           # dispatcher injects nmID
        assert captured["card"]["title"] == "Новый SEO-заголовок"
    _run(go())


def test_update_card_revert_restores_old():
    async def go():
        db, uid = await _setup()
        seen = []
        async def fake(*, token, card):
            seen.append(card.get("title")); return {"requestId": "rq"}
        wb_client.update_card = fake
        res = await executor.execute(
            db=db, user_id=uid, action_type="update_card",
            payload={"marketplace": "wildberries", "offer_id": "1",
                     "card": {"title": "new"}, "old_card": {"title": "old"}},
        )
        assert res.reversible
        rev = await executor.revert(db=db, user_id=uid, log_id=res.log_id)
        assert rev.status == "success"
        assert seen == ["new", "old"]
    _run(go())


def test_update_card_validation_empty():
    async def go():
        db, uid = await _setup()
        res = await executor.execute(
            db=db, user_id=uid, action_type="update_card",
            payload={"marketplace": "wildberries", "offer_id": "1", "card": {}},
        )
        assert res.status == "rejected" and res.error["code"] == "VALIDATION"
    _run(go())
