"""Real client to isolated DB: Ozon pages, cursor recovery and unchanged values."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import uuid

import httpx
import pytest
from sqlalchemy import select, func, event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models.api_sync_state import ApiSyncState
from models.imported_product import ImportedProductRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_price_observation import MarketplacePriceObservation
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.user import User
from models.workspace import Workspace
from services.marketplace.errors import ExecutionError, MarketplacePaginationIncomplete
from services.marketplace.ingest import ozon

KINDS = ["prices", "stocks", "price_observations"]
RUN_ID = "11111111-1111-4111-8111-111111111111"


@asynccontextmanager
async def database(kind, cursor=None):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
            @event.listens_for(db.sync_session, "before_flush")
            def checkpoint_storage_limit(session, *args):
                # SQLite ignores VARCHAR length; enforce PostgreSQL's existing bound here.
                for row in list(session.new) + list(session.dirty):
                    if isinstance(row, ApiSyncState) and row.cursor is not None:
                        assert len(row.cursor) <= ApiSyncState.__table__.c.cursor.type.length

            uid, wid, aid, sid, pid = [str(uuid.uuid4()) for _ in range(5)]
            db.add(User(id=uid, email=uid + "@example.invalid", name="Synthetic",
                        hashed_password="synthetic", is_verified=True))
            db.add(Workspace(id=wid, owner_user_id=uid))
            db.add(MarketplaceAccount(id=aid, workspace_id=wid, marketplace="ozon", label="Synthetic",
                                      identity_status="verified", external_account_id="synthetic-client"))
            db.add(MarketplaceStore(id=sid, marketplace_account_id=aid, marketplace="ozon",
                                    store_key="primary", label="Synthetic", source="manual", status="active"))
            db.add(Product(id=pid, user_id=uid, name="Synthetic", marketplace="ozon", sku="ARTICLE",
                           marketplace_account_id=aid, external_product_id="701"))
            await db.flush()
            db.add(ProductPlacement(product_id=pid, marketplace_account_id=aid,
                                    marketplace_store_id=sid, source="api", status="active"))
            connection = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="ozon",
                                               marketplace_account_id=aid, workspace_id=wid,
                                               status="connected", scopes=["content"],
                                               verification_status="verified", ozon_client_id="synthetic-client")
            db.add(connection)
            await db.flush()
            state = ApiSyncState(id=str(uuid.uuid4()), marketplace_account_id=aid,
                                 marketplace_connection_id=connection.id,
                                 marketplace_store_id=sid, data_type=kind, cursor=cursor, status="running")
            state._owner_user_id = uid
            db.add(state)
            await db.commit()
            yield db, state
    finally:
        await engine.dispose()


def wire(monkeypatch, pages):
    calls = []
    remaining = iter(pages)

    class SyntheticHTTP:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            calls.append(kwargs["json"])
            page = next(remaining)
            if isinstance(page, Exception):
                raise page
            return httpx.Response(200, json=page)

    monkeypatch.setattr(httpx, "AsyncClient", SyntheticHTTP)
    return calls


def item():
    return {"product_id": 701, "offer_id": "ARTICLE", "price": {"price": "10.25", "old_price": "12.00",
            "currency_code": "RUB"}, "stocks": [{"type": "fbo", "present": 2},
                                                 {"type": "fbs", "present": 3}]}


@pytest.mark.parametrize("kind", KINDS)
def test_documented_pages_persist_and_resume_legacy_cursor(monkeypatch, kind):
    calls = wire(monkeypatch, [{"items": [item()], "cursor": "next"},
                               {"items": [], "cursor": ""}])

    async def scenario():
        initial = json.dumps({"last_id": "legacy-resume", "run_id": RUN_ID})
        async with database(kind, initial) as (db, state):
            result = await ozon.fetch_and_persist_page(db, state, "synthetic", "synthetic-client")
            assert result == {"done": False, "count": 1, "defer": False}
            await db.commit()
            saved = json.loads(state.cursor)
            assert saved["last_id"] == "next"
            assert saved["run_id"] == RUN_ID
            if kind == "price_observations":
                rows = (await db.execute(select(MarketplacePriceObservation))).scalars().all()
                assert len(rows) == 1 and str(rows[0].buyer_price) == "10.25"
                assert rows[0].expected_seller_revenue is None
                assert rows[0].commission_base is None and rows[0].marketplace_subsidy is None
                assert rows[0].ingest_run_id == RUN_ID
            else:
                rows = (await db.execute(select(ImportedProductRow))).scalars().all()
                assert len(rows) == 1
                if kind == "prices":
                    assert rows[0].price == 10.25 and rows[0].stock is None
                else:
                    assert rows[0].stock == 5 and rows[0].price is None
            result = await ozon.fetch_and_persist_page(db, state, "synthetic", "synthetic-client")
            assert result == {"done": True, "count": 0, "defer": False}
            assert state.cursor is None
            assert calls[0]["cursor"] == "legacy-resume" and calls[1]["cursor"] == "next"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_empty_continuing_page_does_not_finish(monkeypatch, kind):
    wire(monkeypatch, [{"items": [], "cursor": "next"}])

    async def scenario():
        async with database(kind) as (db, state):
            result = await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            assert result == {"done": False, "count": 0, "defer": False}
            assert json.loads(state.cursor)["last_id"] == "next"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_malformed_page_preserves_cursor_and_writes_nothing(monkeypatch, kind):
    wire(monkeypatch, [{"items": [item()], "cursor": None}])

    async def scenario():
        initial = json.dumps({"last_id": "resume", "run_id": RUN_ID})
        async with database(kind, initial) as (db, state):
            with pytest.raises(MarketplacePaginationIncomplete):
                await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            await db.rollback()
            await db.refresh(state)
            assert state.cursor == initial
            for model in (ImportedProductRow, MarketplacePriceObservation):
                assert (await db.execute(select(func.count()).select_from(model))).scalar() == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_multi_page_cycle_is_rejected_before_row_writes(monkeypatch, kind):
    wire(monkeypatch, [{"items": [item()], "cursor": "earlier"}])

    async def scenario():
        initial = json.dumps({"last_id": "current", "cursor_guard": {
            "anchor": hashlib.sha256(b"earlier").hexdigest(), "power": 2, "distance": 1, "pages": 2}})
        async with database(kind, initial) as (db, state):
            with pytest.raises(MarketplacePaginationIncomplete) as caught:
                await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            assert caught.value.reason == "cycle"
            assert state.cursor == initial
            assert (await db.execute(select(func.count()).select_from(ImportedProductRow))).scalar() == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_page_cap_rejects_without_transport(monkeypatch, kind):
    calls = wire(monkeypatch, [])
    monkeypatch.setattr(ozon, "_CATALOG_MAX_PAGES", 1, raising=False)

    async def scenario():
        initial = json.dumps({"last_id": "current", "cursor_guard": {
            "anchor": hashlib.sha256(b"current").hexdigest(), "power": 1, "distance": 1, "pages": 1}})
        async with database(kind, initial) as (db, state):
            with pytest.raises(MarketplacePaginationIncomplete) as caught:
                await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            assert caught.value.reason == "page_limit"
            assert state.cursor == initial and calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_later_error_keeps_committed_cursor(monkeypatch, kind):
    marker = ExecutionError(ExecutionError.AUTH, "synthetic")
    responses = iter([{"result": {"items": [item()], "last_id": "next"}}, marker])

    async def response(**kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ozon.ozon_client, "product_stocks" if kind == "stocks" else "product_prices", response)

    async def scenario():
        async with database(kind) as (db, state):
            await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            await db.commit()
            saved = state.cursor
            with pytest.raises(ExecutionError) as caught:
                await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            assert caught.value is marker and state.cursor == saved

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_history_detects_cycle_across_committed_empty_pages(monkeypatch, kind):
    calls = wire(monkeypatch, [{"items": [], "cursor": "A"},
                               {"items": [], "cursor": "B"},
                               {"items": [item()], "cursor": "A"}])

    async def scenario():
        async with database(kind) as (db, state):
            for _ in range(2):
                result = await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
                assert not result["done"]
                await db.commit()
            saved = state.cursor
            guard = json.loads(saved)["cursor_guard"]
            assert guard["anchor"] == hashlib.sha256(b"A").hexdigest()
            assert guard["pages"] == 2
            with pytest.raises(MarketplacePaginationIncomplete) as caught:
                await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            assert caught.value.reason == "cycle" and state.cursor == saved
            assert (await db.execute(select(func.count()).select_from(ImportedProductRow))).scalar() == 0
            assert [call["cursor"] for call in calls] == ["", "A", "B"]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("initial", ["not-json", "[]", '{"last_id":null}',
                                     '{"last_id":"A","cursor_guard":{"anchor":"bad-hash"}}'])
def test_corrupt_checkpoint_is_not_silently_restarted(monkeypatch, kind, initial):
    calls = wire(monkeypatch, [])

    async def scenario():
        async with database(kind, initial) as (db, state):
            with pytest.raises(MarketplacePaginationIncomplete) as caught:
                await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            assert caught.value.reason == "schema"
            assert state.cursor == initial and calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_many_pages_persist_constant_size_checkpoints(monkeypatch, kind):
    calls = wire(monkeypatch, [{"items": [], "cursor": f"P{i}"} for i in range(30)]
                  + [{"items": [], "cursor": ""}])

    async def scenario():
        assert ApiSyncState.__table__.c.cursor.type.length == ozon._CATALOG_CURSOR_LIMIT == 500
        async with database(kind) as (db, state):
            for i in range(30):
                result = await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
                assert not result["done"]
                await db.commit()
                await db.refresh(state)
                checkpoint = json.loads(state.cursor)
                assert checkpoint["last_id"] == f"P{i}"
                assert checkpoint["cursor_guard"]["pages"] == i + 1
                assert len(state.cursor) < 300
            result = await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            assert result["done"] and state.cursor is None
            await db.commit()
            await db.refresh(state)
            assert state.cursor is None and len(calls) == 31

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_oversized_next_checkpoint_fails_before_rows_or_commit(monkeypatch, kind):
    wire(monkeypatch, [{"items": [item()], "cursor": "X" * 480}])

    async def scenario():
        initial = json.dumps({"last_id": "resume"})
        async with database(kind, initial) as (db, state):
            with pytest.raises(MarketplacePaginationIncomplete) as caught:
                await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            assert caught.value.reason == "page_limit" and state.cursor == initial
            await db.commit()
            await db.refresh(state)
            assert state.cursor == initial
            for model in (ImportedProductRow, MarketplacePriceObservation):
                assert (await db.execute(select(func.count()).select_from(model))).scalar() == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_long_valid_legacy_checkpoint_is_resumed_without_rewriting_token(monkeypatch, kind):
    calls = wire(monkeypatch, [{"items": [], "cursor": "short-next"}])

    async def scenario():
        token = "L" * 400
        async with database(kind, json.dumps({"last_id": token})) as (db, state):
            result = await ozon.fetch_and_persist_page(db, state, "synthetic", "client")
            assert not result["done"] and calls[0]["cursor"] == token
            await db.commit()
            await db.refresh(state)
            assert json.loads(state.cursor)["last_id"] == "short-next"

    asyncio.run(scenario())


@pytest.mark.parametrize("prefix", [0, 1, 3, 9])
@pytest.mark.parametrize("period", [1, 2, 3, 7, 16])
def test_constant_space_guard_detects_cycles_after_varied_prefixes(prefix, period):
    # Pure traversal proof covers doubling-anchor boundaries without a database per permutation.
    checkpoint = None
    sequence = [f"prefix-{i}" for i in range(prefix)]
    for step in range(4 * (prefix + period + 1)):
        next_token = sequence[step] if step < prefix else f"cycle-{(step - prefix) % period}"
        cur = ozon._catalog_cursor(checkpoint)
        try:
            _, checkpoint = ozon._catalog_progress({"result": {"items": [], "last_id": next_token}}, cur)
        except MarketplacePaginationIncomplete as exc:
            assert exc.reason == "cycle"
            return
        assert len(checkpoint) <= 500
    pytest.fail("A finite cursor cycle was not rejected within the conservative traversal bound")
