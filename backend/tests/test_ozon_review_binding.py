"""Synthetic, read-only cabinet/product binding; not unanswered import admission."""
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, func, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool, AsyncAdaptedQueuePool

import models  # noqa: F401
from database import Base
from models.user import User
from models.workspace import Workspace
from models.product import Product
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.review_response import ReviewResponse
from services.marketplace import credential_vault
from services.marketplace.base_client import BaseMarketplaceClient
from services.marketplace.errors import ExecutionError
from services.marketplace.ozon_review_read import ReviewReadLimits


@asynccontextmanager
async def seeded():
    # Distinct physical connections, shared in-memory DB, no caller rollback.
    engine = create_async_engine(
        "sqlite+aiosqlite:///file:binding-" + uuid.uuid4().hex + "?mode=memory&cache=shared&uri=true",
        poolclass=AsyncAdaptedQueuePool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add_all([User(id="owner", name="Synthetic", email="binding@example.invalid",
                         hashed_password="not-a-password"),
                    Workspace(id="workspace", owner_user_id="owner"),
                    MarketplaceAccount(id="account", workspace_id="workspace", marketplace="ozon",
                                       external_account_id="client-A", identity_status="verified"),
                    MarketplaceConnection(id="connection", user_id="owner", workspace_id="workspace",
                        marketplace="ozon", marketplace_account_id="account", status="connected",
                        ozon_client_id="client-A", scopes=["feedbacks"]),
                    ApiCredential(id="credential", connection_id="connection", scope="feedbacks",
                        secret_enc=credential_vault.encrypt("synthetic-key-A"), meta={}),
                    Product(id="product", user_id="owner", name="Synthetic", marketplace="ozon",
                        marketplace_account_id="account", external_product_id="101", sku="ARTICLE")])
        await db.commit()
        yield db
    await engine.dispose()


def catalog(**changes):
    row = dict(id=101, offer_id="ARTICLE", sku=777)
    row.update(changes)
    return {"items": [row]}


def detail(review_id="review", sku=777, status="NEW"):
    return dict(id=review_id, sku=sku, rating=5, status=status, text="Synthetic",
                published_at="2026-11-05T10:00:00Z")


def wire(monkeypatch, replies, mutate=None):
    calls = []
    async def request(self, method, path, **kwargs):
        calls.append((path, kwargs))
        if mutate is not None:
            await mutate(len(calls))
        response = replies[len(calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(BaseMarketplaceClient, "request", request)
    return calls


async def read(db, **options):
    from services.marketplace.ozon_review_binding import read_bound_product_selection
    return await read_bound_product_selection(db, user_id="owner", product_id="product",
                                              connection_id="connection", **options)


def test_explicit_product_id_maps_to_sku_not_seller_article(monkeypatch):
    calls = wire(monkeypatch, [catalog(),
        {"reviews": [{"id": "other"}, {"id": "review"}], "has_next": False, "last_id": ""},
        detail("other", 888), detail(), catalog()])
    async def scenario():
        async with seeded() as db:
            result = await read(db)
            assert result.product_id == "product" and result.marketplace_account_id == "account"
            assert result.sku == 777 and [r.id for r in result.reviews] == ["review"]
            assert result.unanswered_proven is False
            assert not hasattr(result, "token") and not hasattr(result, "secret_enc")
            assert (await db.execute(select(func.count(ReviewResponse.id)))).scalar_one() == 0
            assert not db.new and not db.dirty and not db.deleted
    asyncio.run(scenario())
    assert calls[0][1]["json"] == {"product_id": ["101"]}
    assert calls[-1][1]["json"] == {"product_id": ["101"]}
    assert all(k["token"] == "synthetic-key-A" and k["extra_headers"] == {"Client-Id": "client-A"}
               for _, k in calls)


@pytest.mark.parametrize("model,field,value", [
    (Product, "user_id", "foreign"), (Product, "marketplace", "wildberries"),
    (Product, "marketplace_account_id", None), (Product, "external_product_id", None),
    (Product, "external_product_id", "ARTICLE"), (Product, "external_product_id", "00101"),
    (Product, "sku", None), (Product, "sku", ""),
    (MarketplaceConnection, "user_id", "foreign"),
    (MarketplaceConnection, "marketplace_account_id", "different-account"),
    (MarketplaceConnection, "workspace_id", "different-workspace"),
    (MarketplaceConnection, "marketplace", "yandex"),
    (MarketplaceConnection, "status", "revoked"),
    (MarketplaceConnection, "ozon_client_id", "client-B"),
    (MarketplaceAccount, "workspace_id", "different-workspace"),
    (MarketplaceAccount, "identity_status", "unverified"),
    (MarketplaceAccount, "external_account_id", None),
    (MarketplaceAccount, "marketplace", "wildberries"),
    (Workspace, "owner_user_id", "foreign"), (User, "deleted_at", datetime(2026, 1, 1)),
    (ApiCredential, "scope", "prices"),
])
def test_invalid_local_binding_denies_before_http(monkeypatch, model, field, value):
    calls = wire(monkeypatch, [])
    async def scenario():
        async with seeded() as db:
            ids = {Product: "product", MarketplaceConnection: "connection",
                   MarketplaceAccount: "account", Workspace: "workspace", User: "owner",
                   ApiCredential: "credential"}
            setattr(await db.get(model, ids[model]), field, value)
            await db.commit()
            with pytest.raises(ExecutionError) as caught:
                await read(db)
            assert caught.value.code == "GUARD_OZON_REVIEW_BINDING"
    asyncio.run(scenario())
    assert calls == []


@pytest.mark.parametrize("response", [
    {}, {"result": {"items": [{"id": 101, "offer_id": "ARTICLE", "sku": 777}]}},
    {"items": []}, {"items": [{"id": 101, "offer_id": "ARTICLE", "sku": 777}] * 2},
    {"items": [{"id": 102, "offer_id": "ARTICLE", "sku": 777}]},
    {"items": [{"id": "101", "offer_id": "ARTICLE", "sku": 777}]},
    {"items": [{"id": 101, "offer_id": "article", "sku": 777}]},
    {"items": [{"id": 101, "offer_id": "ARTICLE", "sku": "777"}]},
    {"items": [{"id": 101, "offer_id": "ARTICLE", "sku": True}]},
])
def test_catalog_schema_and_identity_fail_closed(monkeypatch, response):
    calls = wire(monkeypatch, [response])
    async def scenario():
        async with seeded() as db:
            with pytest.raises(ExecutionError):
                await read(db)
    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("model,field,value", [
    (MarketplaceConnection, "status", "revoked"),
    (MarketplaceConnection, "ozon_client_id", "client-B"),
    (Product, "sku", "CHANGED"), (Product, "marketplace_account_id", None),
    (MarketplaceAccount, "identity_status", "unverified"),
    (User, "deleted_at", datetime(2026, 1, 1)),
    (ApiCredential, "secret_enc", b"rotated-ciphertext"),
])
def test_binding_change_during_request_discards_result(monkeypatch, model, field, value):
    async def scenario():
        async with seeded() as db:
            async def mutate(n):
                if n == 1:
                    ids = {MarketplaceConnection: "connection", Product: "product",
                           MarketplaceAccount: "account", User: "owner", ApiCredential: "credential"}
                    setattr(await db.get(model, ids[model]), field, value)
                    await db.commit()
            calls = wire(monkeypatch, [catalog()], mutate)
            with pytest.raises(ExecutionError) as caught:
                await read(db)
            assert caught.value.code == "GUARD_OZON_REVIEW_BINDING"
            assert len(calls) == 1
    asyncio.run(scenario())


def test_budget_includes_both_catalog_reads_and_all_review_calls(monkeypatch):
    calls = wire(monkeypatch, [catalog(), {"reviews": [], "has_next": False, "last_id": ""}])
    async def scenario():
        async with seeded() as db:
            with pytest.raises(ExecutionError) as caught:
                await read(db, limits=ReviewReadLimits(max_attempts=2))
            assert caught.value.code == ExecutionError.PAGINATION_INCOMPLETE
            assert "attempt_limit" in str(caught.value)
    asyncio.run(scenario())
    assert len(calls) == 2


def test_catalog_change_after_review_scan_discards_buffer(monkeypatch):
    calls = wire(monkeypatch, [catalog(),
        {"reviews": [{"id": "review"}], "has_next": False, "last_id": ""}, detail(),
        {"items": [{"id": 101, "offer_id": "ARTICLE", "sku": 999}]}])
    async def scenario():
        async with seeded() as db:
            with pytest.raises(ExecutionError):
                await read(db)
            assert (await db.execute(select(func.count(ReviewResponse.id)))).scalar_one() == 0
    asyncio.run(scenario())
    assert len(calls) == 4


@pytest.mark.parametrize("status", ["NEW", "VIEWED", "PROCESSED"])
def test_processing_status_never_proves_unanswered(monkeypatch, status):
    wire(monkeypatch, [catalog(),
        {"reviews": [{"id": "review"}], "has_next": False, "last_id": ""},
        detail(status=status), catalog()])
    async def scenario():
        async with seeded() as db:
            result = await read(db)
            assert len(result.reviews) == 1 and result.unanswered_proven is False
    asyncio.run(scenario())


def test_no_fallback_to_other_connected_cabinet_with_same_article(monkeypatch):
    calls = wire(monkeypatch, [])
    async def scenario():
        from services.marketplace.ozon_review_binding import read_bound_product_selection
        async with seeded() as db:
            db.add(MarketplaceAccount(id="account-B", workspace_id="workspace", marketplace="ozon",
                                     external_account_id="client-B", identity_status="verified"))
            db.add(MarketplaceConnection(id="connection-B", user_id="owner", marketplace="ozon",
                marketplace_account_id="account-B", workspace_id="workspace", status="connected",
                ozon_client_id="client-B", scopes=["feedbacks"]))
            db.add(ApiCredential(id="credential-B", connection_id="connection-B", scope="feedbacks",
                                 secret_enc=credential_vault.encrypt("synthetic-key-B")))
            db.add(Product(id="product-B", user_id="owner", marketplace="ozon", name="Synthetic B",
                sku="ARTICLE", external_product_id="101", marketplace_account_id="account-B"))
            await db.commit()
            with pytest.raises(ExecutionError):
                await read_bound_product_selection(db, user_id="owner", product_id="product-B",
                                                   connection_id="connection")
    asyncio.run(scenario())
    assert calls == []


@pytest.mark.parametrize("change", ["pending-edit", "missing-key", "expired-key", "invalid-key"])
def test_unsafe_session_or_key_denies_without_side_effects(monkeypatch, change):
    calls = wire(monkeypatch, [])
    async def scenario():
        async with seeded() as db:
            if change == "pending-edit":
                product = await db.get(Product, "product")
                product.sku = "uncommitted"
            elif change == "missing-key":
                await db.delete(await db.get(ApiCredential, "credential"))
                await db.commit()
            else:
                credential = await db.get(ApiCredential, "credential")
                if change == "expired-key":
                    credential.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
                else:
                    credential.secret_enc = b"synthetic-invalid-ciphertext"
                await db.commit()
            with pytest.raises(ExecutionError) as caught:
                await read(db)
            assert "synthetic" not in str(caught.value)
            if change == "pending-edit":
                assert product in db.dirty and product.sku == "uncommitted"
    asyncio.run(scenario())
    assert calls == []


def test_independent_session_rotation_is_seen_despite_cached_caller_objects(monkeypatch):
    async def scenario():
        async with seeded() as db:
            cached = await db.get(ApiCredential, "credential")
            original_cipher = cached.secret_enc
            await db.commit()  # reader requires caller's transaction to be closed
            async def mutate(n):
                if n == 2:
                    async with AsyncSession(db.bind) as writer:
                        current = await writer.get(ApiCredential, "credential")
                        current.secret_enc = credential_vault.encrypt("synthetic-key-B")
                        await writer.commit()
            calls = wire(monkeypatch, [catalog(),
                {"reviews": [], "has_next": False, "last_id": ""}], mutate)
            with pytest.raises(ExecutionError):
                await read(db)
            assert cached.secret_enc == original_cipher  # cached object really remains stale
            assert len(calls) == 2  # no final catalog request after rotation is detected
    asyncio.run(scenario())


def test_transport_failure_stays_secret_safe_and_cancellation_propagates(monkeypatch):
    async def scenario():
        async with seeded() as db:
            wire(monkeypatch, [ExecutionError(ExecutionError.MARKETPLACE_4XX,
                                              "synthetic-key-A provider-payload")])
            with pytest.raises(ExecutionError) as caught:
                await read(db)
            assert caught.value.code == ExecutionError.MARKETPLACE_4XX
            assert "synthetic" not in str(caught.value) and "provider-payload" not in str(caught.value)
            async def cancelled(*a, **kw):
                raise asyncio.CancelledError()
            monkeypatch.setattr(BaseMarketplaceClient, "request", cancelled)
            with pytest.raises(asyncio.CancelledError):
                await read(db)
    asyncio.run(scenario())


def test_deadline_covers_authority_lookup(monkeypatch):
    async def scenario():
        import services.marketplace.ozon_review_binding as binding
        async with seeded() as db:
            calls = wire(monkeypatch, [])
            async def slow(*a, **kw):
                await asyncio.Event().wait()
            monkeypatch.setattr(binding, "_snapshot", slow)
            with pytest.raises(ExecutionError) as caught:
                await read(db, limits=ReviewReadLimits(deadline_seconds=0.01))
            assert caught.value.code == ExecutionError.PAGINATION_INCOMPLETE
            assert "deadline" in str(caught.value) and not calls
    asyncio.run(scenario())


def test_parallel_selections_do_not_share_credential_cursor_or_product(monkeypatch):
    async def scenario():
        from services.marketplace.ozon_review_binding import read_bound_product_selection
        async with seeded() as db:
            db.add(MarketplaceAccount(id="account-B", workspace_id="workspace", marketplace="ozon",
                                     external_account_id="client-B", identity_status="verified"))
            db.add(MarketplaceConnection(id="connection-B", user_id="owner", marketplace="ozon",
                marketplace_account_id="account-B", workspace_id="workspace", status="connected",
                ozon_client_id="client-B", scopes=["feedbacks"]))
            db.add(ApiCredential(id="credential-B", connection_id="connection-B", scope="feedbacks",
                                 secret_enc=credential_vault.encrypt("synthetic-key-B")))
            db.add(Product(id="product-B", user_id="owner", marketplace="ozon", name="Synthetic B",
                sku="ARTICLE", external_product_id="202", marketplace_account_id="account-B"))
            await db.commit()
            seen = []
            async def request(self, method, path, **kwargs):
                client = kwargs["extra_headers"]["Client-Id"]
                token, body = kwargs["token"], kwargs["json"]
                expected = ("synthetic-key-A", 101, 777) if client == "client-A" else (
                            "synthetic-key-B", 202, 888)
                assert token == expected[0]
                seen.append((client, path, dict(body)))
                await asyncio.sleep(0)  # exercise interleaved invocations
                if path == "/v3/product/info/list":
                    assert body == {"product_id": [str(expected[1])]}
                    return catalog(id=expected[1], sku=expected[2])
                if path == "/v2/review/list":
                    if "last_id" not in body:
                        return {"reviews": [], "has_next": True, "last_id": "cursor-" + client}
                    assert body["last_id"] == "cursor-" + client
                    return {"reviews": [{"id": "review-" + client}], "has_next": False, "last_id": ""}
                assert body == {"review_id": "review-" + client}
                return detail("review-" + client, expected[2])
            monkeypatch.setattr(BaseMarketplaceClient, "request", request)
            # Both calls get their own read-only authority sessions and request budgets.
            a, b = await asyncio.gather(read(db), read_bound_product_selection(db, user_id="owner",
                product_id="product-B", connection_id="connection-B"))
            assert (a.sku, b.sku) == (777, 888)
            assert [r.id for r in a.reviews] == ["review-client-A"]
            assert [r.id for r in b.reviews] == ["review-client-B"]
            assert len(seen) == 10
    asyncio.run(scenario())


def test_shared_connection_pool_is_rejected_before_any_query(monkeypatch):
    calls = wire(monkeypatch, [])
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with AsyncSession(engine) as db:
            with pytest.raises(ExecutionError) as caught:
                await read(db)
            assert caught.value.code == "GUARD_OZON_REVIEW_BINDING"
        await engine.dispose()
    asyncio.run(scenario())
    assert calls == []


def test_flushed_uncommitted_caller_transaction_remains_untouched(monkeypatch):
    calls = wire(monkeypatch, [catalog(),
        {"reviews": [], "has_next": False, "last_id": ""}, catalog()])
    async def scenario():
        async with seeded() as db:
            product = await db.get(Product, "product")
            product.name = "Caller uncommitted name"
            await db.flush()
            assert not db.dirty and db.in_transaction()
            with pytest.raises(ExecutionError) as caught:
                await read(db)
            assert caught.value.code == "GUARD_OZON_REVIEW_BINDING"
            assert (await db.execute(select(Product.name))).scalar_one() == "Caller uncommitted name"
    asyncio.run(scenario())
    assert calls == []


def test_database_exception_parameters_are_not_exposed(monkeypatch):
    async def scenario():
        async with seeded() as db:
            calls = wire(monkeypatch, [])
            def fail(*args):
                raise DBAPIError("synthetic-sql", {"private": "synthetic-sensitive-param"},
                                 Exception("synthetic-db-message"))
            event.listen(db.bind.sync_engine, "before_cursor_execute", fail)
            try:
                with pytest.raises(ExecutionError) as caught:
                    await read(db)
                assert "synthetic" not in str(caught.value) and not calls
                assert caught.value.__cause__ is None
            finally:
                event.remove(db.bind.sync_engine, "before_cursor_execute", fail)
    asyncio.run(scenario())
