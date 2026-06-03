"""ME-3 — set_price goes through the shared executor to a real marketplace API."""
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


async def _setup(scope="prices", marketplace="wildberries", ozon_client_id=None):
    engine = create_async_engine("sqlite+aiosqlite://",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    uid = str(uuid.uuid4())
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                 status="connected", scopes=[scope], ozon_client_id=ozon_client_id)
    db.add(conn)
    await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return db, uid


def test_set_price_success():
    async def go():
        db, uid = await _setup()
        calls = {"n": 0}
        async def fake(*, token, offer_id, price, discount=None):
            calls["n"] += 1; calls["price"] = price
            return {"requestId": "rq-1"}
        wb_client.set_price = fake
        res = await executor.execute(
            db=db, user_id=uid, action_type="set_price",
            payload={"marketplace": "wildberries", "offer_id": "12345", "price": 1990, "old_price": 1500},
        )
        assert res.status == "success", res.error
        assert calls["n"] == 1 and calls["price"] == 1990
    _run(go())


def test_guard_max_price_blocks():
    async def go():
        db, uid = await _setup()
        wb_client.set_price = lambda **k: (_ for _ in ()).throw(AssertionError("should not dispatch"))
        res = await executor.execute(
            db=db, user_id=uid, action_type="set_price",
            payload={"marketplace": "wildberries", "offer_id": "1", "price": 9999},
            mode="automated_l4",
            rule={"enabled": True, "guard": {"max_price": 5000}},
        )
        assert res.status == "rejected"
        assert res.error["code"] == "GUARD_MAX_PRICE"
    _run(go())


def test_set_price_validation_positive():
    async def go():
        db, uid = await _setup()
        res = await executor.execute(
            db=db, user_id=uid, action_type="set_price",
            payload={"marketplace": "wildberries", "offer_id": "1", "price": -5},
        )
        assert res.status == "rejected" and res.error["code"] == "VALIDATION"
    _run(go())


def test_set_price_reversible_and_revert():
    async def go():
        db, uid = await _setup()
        seen = []
        async def fake(*, token, offer_id, price, discount=None):
            seen.append(price); return {"requestId": f"rq-{price}"}
        wb_client.set_price = fake
        res = await executor.execute(
            db=db, user_id=uid, action_type="set_price",
            payload={"marketplace": "wildberries", "offer_id": "1", "price": 2000, "old_price": 1500},
        )
        assert res.status == "success" and res.reversible
        rev = await executor.revert(db=db, user_id=uid, log_id=res.log_id)
        assert rev.status == "success"
        assert seen == [2000, 1500]   # revert pushed the old price back
    _run(go())


def test_ozon_requires_client_id():
    async def go():
        db, uid = await _setup(marketplace="ozon", ozon_client_id=None)
        res = await executor.execute(
            db=db, user_id=uid, action_type="set_price",
            payload={"marketplace": "ozon", "offer_id": "off-1", "price": 1000},
        )
        assert res.status == "failed"
        assert res.error["code"] == "AUTH"   # Ozon needs client_id
    _run(go())
