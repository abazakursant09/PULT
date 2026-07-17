"""R-OZ3 — Ozon Auto Reviews enabled (honest).

End-to-end confirmation that flipping Ozon on (capability_registry pult_supported=true +
OzonReviewProvider.supports_reviews()=True) makes sync + publish work through the real code paths
with a mocked Ozon client — and that AR5 ambiguous-execution protection holds on the Ozon path.
Automation stays OFF; this is the manual (L3) capability.
"""
import asyncio
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
import models  # noqa: F401
from models.user import User
from models.product import Product
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from services.marketplace import executor, credential_vault
from services.marketplace import ozon_client as ozon_mod
from services.marketplace.errors import ExecutionError
from routers import reviews as reviews_router


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _new_db():
    engine = create_async_engine("sqlite+aiosqlite://",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()


async def _seed_ozon(db, uid):
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    p = Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="OZ-1", marketplace="ozon")
    db.add(p)
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                                 status="connected", scopes=["feedbacks"], ozon_client_id="CID")
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return p


def _client(db, uid):
    app = FastAPI()
    app.include_router(reviews_router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    return TestClient(app)


def test_ozon_sync_no_longer_unsupported(monkeypatch):
    # With Ozon enabled + a connected Ozon account, /sync ingests instead of answering 422.
    async def fake_list(*, token, client_id, product_ref, limit=100):
        assert (client_id, token) == ("CID", "tok")
        return [{"id": "OZ-R1", "text": "отлично", "rating": 5, "author_name": "И",
                 "published_at": "2026-07-14T10:00:00Z"}]
    monkeypatch.setattr(ozon_mod.ozon_client, "list_reviews", fake_list)

    db = _run(_new_db())
    uid = str(uuid.uuid4())
    prod = _run(_seed_ozon(db, uid))
    r = _client(db, uid).post(f"/api/reviews/{prod.id}/sync")
    assert r.status_code == 200
    assert r.json()["imported"] == 1


def test_ozon_publish_timeout_is_ambiguous_and_not_retried(monkeypatch):
    # AR5 on the Ozon path: a TIMEOUT after the request left is recorded ambiguous and a retry with
    # the same idempotency key returns needs_reconcile without a second marketplace call.
    async def go():
        db = await _new_db()
        uid = str(uuid.uuid4())
        await _seed_ozon(db, uid)
        calls = {"n": 0}
        async def raiser(*, token, client_id, review_id, text):
            calls["n"] += 1
            raise ExecutionError(ExecutionError.TIMEOUT, "marketplace timeout")
        monkeypatch.setattr(ozon_mod.ozon_client, "publish_feedback_answer", raiser)

        payload = {"marketplace": "ozon", "feedback_id": "OZ-R1", "text": "Спасибо!", "rating": 5}
        r1 = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                    payload=payload, idempotency_key="review:oz1")
        assert r1.status == "ambiguous" and calls["n"] == 1
        # retry same key — even if Ozon would now answer, we must not call it again
        async def ok(*, token, client_id, review_id, text):
            calls["n"] += 1; return {"requestId": "x"}
        monkeypatch.setattr(ozon_mod.ozon_client, "publish_feedback_answer", ok)
        r2 = await executor.execute(db=db, user_id=uid, action_type="publish_review_response",
                                    payload=payload, idempotency_key="review:oz1")
        assert r2.status == "needs_reconcile" and calls["n"] == 1     # NO second Ozon call
    _run(go())
