"""Selected manual/scheduled YM read paths, synthetic HTTP and isolated WAL DB."""
import asyncio
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import models  # noqa: F401
from database import Base, get_db
from dependencies import get_current_user
from models.api_credential import ApiCredential
from models.automation_rule import AutomationRule
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.product import Product
from models.review_response import ReviewResponse
from models.user import User
from models.workspace import Workspace
from routers import reviews as routes
from services.marketplace import credential_vault
from services.marketplace.review_automation_gate import CONSENT_VERSION
import tasks.auto_review_pipeline as pipeline

from test_marketplace_retry_policy import no_external_io  # noqa: F401


@asynccontextmanager
async def isolated_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'paths.sqlite').as_posix()}",
                                 connect_args={"timeout": .2})
    async with engine.begin() as connection:
        assert (await connection.execute(text("PRAGMA journal_mode=WAL"))).scalar_one() == "wal"
        await connection.run_sync(Base.metadata.create_all)
        # Baseline proof expands only this disposable DB; old readers remain untouched.
        columns = {row[1] for row in (await connection.execute(text("PRAGMA table_info(marketplace_accounts)")))}
        if "ym_review_read_not_before" not in columns:
            await connection.execute(text("ALTER TABLE marketplace_accounts ADD COLUMN ym_review_read_not_before DATETIME"))
        if "ym_review_read_indeterminate" not in columns:
            await connection.execute(text("ALTER TABLE marketplace_accounts ADD COLUMN ym_review_read_indeterminate BOOLEAN NOT NULL DEFAULT 0"))
    async with engine.connect() as first, engine.connect() as second:
        assert first.sync_connection.connection.dbapi_connection is not second.sync_connection.connection.dbapi_connection
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
    hold_module = sys.modules.get("services.marketplace.ym_review_read_hold")
    if hold_module is not None:
        monkeypatch.setattr(hold_module, "AsyncSessionLocal", factory)
    try:
        async with factory() as db:
            yield db, factory
    finally:
        await engine.dispose()


async def seed(db, *, future=True, awaiting=False, business="BIZ1"):
    uid, wid, aid, cid, pid = [str(uuid.uuid4()) for _ in range(5)]
    user = User(id=uid, email=f"{uid}@example.invalid", name="Synthetic", hashed_password="x", is_verified=True)
    db.add(user)
    db.add(Workspace(id=wid, owner_user_id=uid))
    db.add(MarketplaceAccount(id=aid, workspace_id=wid, marketplace="yandex",
                             external_account_id=business, identity_status="verified", label="synthetic"))
    db.add(MarketplaceConnection(id=cid, user_id=uid, workspace_id=wid, marketplace_account_id=aid,
        marketplace="yandex", status="connected", verification_status="verified", scopes=["feedbacks"]))
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=cid, scope="feedbacks",
        secret_enc=credential_vault.encrypt("SYNTHETIC"), meta={"account_ref": business}))
    product = Product(id=pid, user_id=uid, marketplace="yandex", name="Synthetic", sku="SKU1")
    db.add(product)
    db.add(AutomationRule(id=str(uuid.uuid4()), user_id=uid, contour="reputation",
        action_type="publish_review_response", mode="auto", enabled=True, guard={}, connection_id=cid,
        consent_at=datetime.utcnow(), consent_version=CONSENT_VERSION))
    review = None
    if awaiting:
        review = ReviewResponse(id=str(uuid.uuid4()), product_id=pid, marketplace="yandex",
            external_review_id="42", response_text="retained", status="awaiting_moderation", safety_category="SAFE")
        db.add(review)
    await db.commit()
    if future:
        await db.execute(text("UPDATE marketplace_accounts SET ym_review_read_not_before = :bound WHERE id = :id"),
                         {"bound": "2099-11-02 09:00:00", "id": aid})
        await db.commit()
    return user, product, review, aid, cid


def marketplace_http(monkeypatch, responses=None):
    sent = []

    class SyntheticHTTP:
        def __init__(self, *, timeout):
            assert timeout is not None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, *, headers, json, params):
            assert method == "POST" and headers["Api-Key"] == "SYNTHETIC"
            assert "goods-feedback" in url
            assert params in ({"limit": 50}, {"limit": 50, "pageToken": "P2"})
            sent.append(url)
            if responses is not None:
                assert len(sent) <= len(responses), "unexpected retry/page"
                return responses[len(sent) - 1]
            if url.endswith("/comments"):
                assert json == {"feedbackId": 42}
                return httpx.Response(200, json={"result": {"comments": [
                    {"id": 1, "author": {"type": "BUSINESS"}, "status": "PUBLISHED"}]}})
            assert json == {"offerIds": ["SKU1"]}
            return httpx.Response(200, json={"result": {"feedbacks": []}})

    monkeypatch.setattr(httpx, "AsyncClient", SyntheticHTTP)
    return sent


def test_manual_future_hold_denies_before_actual_transport(tmp_path, monkeypatch):
    real_http = httpx.AsyncClient

    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            user, product, _, _, _ = await seed(db)
            app = FastAPI()
            app.include_router(routes.router, prefix="/api")

            async def current_user():
                return user

            async def session():
                yield db

            app.dependency_overrides[get_current_user] = current_user
            app.dependency_overrides[get_db] = session
            sent = marketplace_http(monkeypatch)
            async with real_http(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid") as client:
                response = await client.post(f"/api/reviews/{product.id}/sync")
            assert response.status_code == 429, response.text
            assert sent == []
            assert int(response.headers["Retry-After"]) > 0

    asyncio.run(scenario())


def test_moderation_future_hold_preserves_pending_without_transport(tmp_path, monkeypatch):
    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            _, _, review, _, _ = await seed(db, awaiting=True)
            review_id = review.id
            sent = marketplace_http(monkeypatch)
            outcome = await pipeline.run_reconcile_moderation()
            assert sent == []
            assert outcome["errors"] == 1 and outcome["resolved"] == 0
            await db.refresh(review)
            assert review.id == review_id and review.status == "awaiting_moderation"
            assert review.response_text == "retained" and review.published_at is None

    asyncio.run(scenario())


async def manual_response(real_http, db, user, product):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def current_user():
        return user

    async def session():
        yield db

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = session
    async with real_http(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid") as client:
        return await client.post(f"/api/reviews/{product.id}/sync")


@pytest.mark.parametrize("problem,expected", [("indeterminate", 503), ("identity", 409)])
def test_manual_denials_are_not_empty_success(tmp_path, monkeypatch, problem, expected):
    real_http = httpx.AsyncClient

    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            user, product, _, aid, cid = await seed(db, future=False)
            if problem == "indeterminate":
                await db.execute(text("UPDATE marketplace_accounts SET ym_review_read_indeterminate = 1 WHERE id = :id"), {"id": aid})
            else:
                credential = (await db.execute(select(ApiCredential).where(ApiCredential.connection_id == cid))).scalar_one()
                credential.meta = {"account_ref": "WRONG-BUSINESS"}
            await db.commit()
            sent = marketplace_http(monkeypatch)
            response = await manual_response(real_http, db, user, product)
            assert response.status_code == expected, response.text
            assert "Retry-After" not in response.headers and sent == []
            assert "SYNTHETIC" not in response.text and "BIZ1" not in response.text

    asyncio.run(scenario())


def test_clear_managed_cabinet_still_returns_real_success(tmp_path, monkeypatch):
    real_http = httpx.AsyncClient

    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            user, product, _, _, _ = await seed(db, future=False)
            sent = marketplace_http(monkeypatch)
            response = await manual_response(real_http, db, user, product)
            assert response.status_code == 200 and len(sent) == 1
            assert response.json()["synced"] == 0 and response.json()["imported"] == 0

    asyncio.run(scenario())


def test_scheduled_hold_preserves_failed_cursor_and_other_cabinet(tmp_path, monkeypatch):
    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            _, product, _, _, cid = await seed(db)
            await seed(db, future=False, business="BIZ2")
            sent = marketplace_http(monkeypatch)
            outcome = await pipeline.run_auto_sync_reviews()
            assert outcome["stopped"] == 1 and outcome["errors"] == 1
            assert len(sent) == 1 and "/businesses/BIZ2/" in sent[0]
            connection = await db.get(MarketplaceConnection, cid)
            await db.refresh(connection)
            assert connection.review_sync_cursor is None and connection.review_sync_fail_count == 1
            assert connection.review_sync_next_at >= datetime(2099, 11, 2, 9)
            assert (await db.execute(text("SELECT count(*) FROM review_responses WHERE product_id = :id"), {"id": product.id})).scalar_one() == 0

    asyncio.run(scenario())


def test_observed_hold_survives_caller_rollback_and_second_manual_request(tmp_path, monkeypatch):
    real_http = httpx.AsyncClient

    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            user, product, _, aid, _ = await seed(db, future=False)
            uid, pid = user.id, product.id
            sent = marketplace_http(monkeypatch, [httpx.Response(420, headers={"Retry-After": "3600"})])
            response = await manual_response(real_http, db, user, product)
            assert response.status_code == 429 and int(response.headers["Retry-After"]) >= 3590
            assert len(sent) == 1
            await db.rollback()
            async with factory() as independent:
                hold = (await independent.execute(text("SELECT ym_review_read_not_before FROM marketplace_accounts WHERE id = :id"), {"id": aid})).scalar_one()
                assert hold is not None
            # Re-resolve identity through a fresh caller session, never stale expired ORM.
            async with factory() as fresh:
                user = await fresh.get(User, uid)
                product = await fresh.get(Product, pid)
                again = await manual_response(real_http, fresh, user, product)
                assert again.status_code == 429 and len(sent) == 1

    asyncio.run(scenario())


def test_missing_hints_denies_invocation_without_fabricated_deadline(tmp_path, monkeypatch):
    real_http = httpx.AsyncClient

    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            user, product, _, aid, _ = await seed(db, future=False)
            sent = marketplace_http(monkeypatch, [httpx.Response(420)])
            response = await manual_response(real_http, db, user, product)
            assert response.status_code == 503 and "Retry-After" not in response.headers
            assert len(sent) == 1
            async with factory() as independent:
                row = (await independent.execute(text("SELECT ym_review_read_not_before, ym_review_read_indeterminate FROM marketplace_accounts WHERE id=:id"), {"id": aid})).one()
                assert row[0] is None and not row[1]

    asyncio.run(scenario())


def test_second_page_throttle_never_inserts_partial_reviews(tmp_path, monkeypatch):
    real_http = httpx.AsyncClient

    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            user, product, _, _, _ = await seed(db, future=False)
            product_id = product.id
            sent = marketplace_http(monkeypatch, [
                httpx.Response(200, json={"result": {"feedbacks": [{"feedbackId": 1,
                    "identifiers": {"offerId": "SKU1"}, "statistics": {"rating": 5}}],
                    "paging": {"nextPageToken": "P2"}}}),
                httpx.Response(420, headers={"Retry-After": "3600"}),
            ])
            response = await manual_response(real_http, db, user, product)
            assert response.status_code == 429 and len(sent) == 2
            assert (await db.execute(text("SELECT count(*) FROM review_responses WHERE product_id=:id"), {"id": product_id})).scalar_one() == 0

    asyncio.run(scenario())


def test_caller_write_lock_fails_closed_without_committing_caller(tmp_path, monkeypatch):
    real_http = httpx.AsyncClient

    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            user, product, _, _, _ = await seed(db, future=False)
            uid = user.id
            user.name = "uncommitted caller change"
            await db.flush()
            sent = marketplace_http(monkeypatch)
            response = await manual_response(real_http, db, user, product)
            assert response.status_code == 503 and sent == []
            assert "Retry-After" not in response.headers
            assert db.in_transaction() and user.name == "uncommitted caller change"
            async with factory() as independent:
                persisted = await independent.get(User, uid)
                assert persisted.name == "Synthetic"
            await db.rollback()

    asyncio.run(scenario())


def test_authority_factory_failure_maps_503_without_http(tmp_path, monkeypatch):
    real_http = httpx.AsyncClient

    async def scenario():
        async with isolated_db(tmp_path, monkeypatch) as (db, factory):
            user, product, _, _, _ = await seed(db, future=False)
            from services.marketplace import ym_review_read_hold as hold

            @asynccontextmanager
            async def broken_factory():
                raise RuntimeError("synthetic DB authority failure")
                yield  # pragma: no cover - marks context manager without acquiring a DB

            monkeypatch.setattr(hold, "AsyncSessionLocal", broken_factory)
            sent = marketplace_http(monkeypatch)
            response = await manual_response(real_http, db, user, product)
            assert response.status_code == 503 and "Retry-After" not in response.headers
            assert sent == [] and "synthetic DB" not in response.text

    asyncio.run(scenario())
