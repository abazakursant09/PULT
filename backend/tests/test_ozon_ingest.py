"""PULT-LAUNCH-1.4.5F — Ozon API ingestion.

No network: the Ozon client is stubbed with official-shaped fixtures. Same honesty rules as WB —
product_id is identity, an order is not a sale, a cancellation is not a return, FBO and FBS never
collide, money is Decimal with its sign kept, an unknown amount is NULL, a stock refresh keeps the
price, and everything is idempotent and isolated from CSV. The scheduler now serves both WB and
Ozon; a WB regression here would fail too.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.api_credential import ApiCredential
from models.api_sync_state import ApiSyncState
from models.imported_card_content import ImportedCardContentRow
from models.imported_product import ImportedProductRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_operation import MarketplaceOperation
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.user import User
from models.workspace import Workspace
from services.marketplace import credential_vault
from services.marketplace.errors import ExecutionError
from services.marketplace.ingest import ozon as oz
import tasks.api_sync as api_sync

_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, store_status="active", verified=True, client_id="cid-1"):
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace="ozon",
                             identity_status="verified", external_account_id=client_id, label="Каб")
    db.add(acc)
    store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="ozon",
                             store_key="primary", label="Магазин", source="manual", status=store_status)
    db.add(store)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=uid, marketplace="ozon", status="connected",
        verification_status="verified" if verified else "unverified", scopes=["content"],
        ozon_client_id=client_id, marketplace_account_id=acc.id, workspace_id=wid)
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="content",
                         secret_enc=credential_vault.encrypt("oz-token"),
                         verification_status="verified"))
    await db.commit()
    return uid, acc, store, conn


async def _state(db, conn, store, dt):
    st = ApiSyncState(marketplace_connection_id=conn.id, marketplace_account_id=conn.marketplace_account_id,
                      marketplace_store_id=store.id, data_type=dt, status="pending")
    st._owner_user_id = conn.user_id
    db.add(st)
    await db.commit()
    return st


class _StubOzon:
    def __init__(self, *, products=None, infos=None, prices=None, stocks=None,
                 fbo=None, fbs=None, finance=None, returns=None):
        self._products = products or {"result": {"items": [], "last_id": ""}}
        self._infos = infos or []
        self._prices = prices or {"result": {"items": [], "last_id": ""}}
        self._stocks = stocks or {"result": {"items": [], "last_id": ""}}
        self._fbo = fbo or []
        self._fbs = fbs or []
        self._finance = finance or {"result": {"operations": [], "page_count": 1}}
        self._returns = returns or {"returns": [], "has_next": False}

    async def list_products(self, *, token, client_id, last_id="", limit=1000):
        return self._products

    async def product_info_list(self, *, token, client_id, product_ids):
        return self._infos

    async def product_prices(self, *, token, client_id, last_id="", limit=1000):
        return self._prices

    async def product_stocks(self, *, token, client_id, last_id="", limit=1000):
        return self._stocks

    async def posting_fbo_list(self, *, token, client_id, since, to, offset=0, limit=1000):
        return list(self._fbo)

    async def posting_fbs_list(self, *, token, client_id, since, to, offset=0, limit=1000):
        return list(self._fbs)

    async def finance_transactions(self, *, token, client_id, date_from, date_to, page=1, page_size=1000):
        return self._finance

    async def returns_list(self, *, token, client_id, last_id=0, limit=1000):
        return self._returns

    # PULT-LAUNCH-2.5D — promotion reads default to empty so existing full-run tests stay neutral.
    async def list_actions(self, *, token, client_id):
        return []

    async def action_products(self, *, token, client_id, action_id, last_id=0, limit=1000):
        return {"result": {"products": [], "last_id": ""}}

    async def action_candidates(self, *, token, client_id, action_id, last_id=0, limit=1000):
        return {"result": {"products": [], "last_id": ""}}


def _use(monkeypatch, stub):
    monkeypatch.setattr(oz, "ozon_client", stub)


# ── Identity / store ─────────────────────────────────────────────────────────────
def test_product_id_links_one_product(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "products"))
    _use(monkeypatch, _StubOzon(
        products={"result": {"items": [{"product_id": 111, "offer_id": "OF-1"}], "last_id": ""}},
        infos=[{"id": 111, "name": "Товар", "type_name": "Кат"}]))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    prods = _run(db.execute(select(Product))).scalars().all()
    assert len(prods) == 1 and prods[0].external_product_id == "111" and prods[0].sku == "OF-1"
    assert _run(db.execute(select(func.count()).select_from(ProductPlacement))).scalar_one() == 1


def test_products_repeat_no_duplicate(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "products"))
    stub = _StubOzon(products={"result": {"items": [{"product_id": 111, "offer_id": "OF-1"}], "last_id": ""}},
                     infos=[{"id": 111, "name": "Товар"}])
    _use(monkeypatch, stub)
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(Product))).scalar_one() == 1
    assert _run(db.execute(select(func.count()).select_from(ProductPlacement))).scalar_one() == 1
    assert _run(db.execute(select(func.count()).select_from(ImportedCardContentRow))).scalar_one() == 1


def test_offer_id_other_account_not_mixed(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    other = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=str(uuid.uuid4()),
                               marketplace="ozon", identity_status="verified")
    db.add(other)
    db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="X", marketplace="ozon",
                   sku="OF-1", marketplace_account_id=other.id, external_product_id="999"))
    _run(db.commit())
    st = _run(_state(db, conn, store, "products"))
    _use(monkeypatch, _StubOzon(
        products={"result": {"items": [{"product_id": 111, "offer_id": "OF-1"}], "last_id": ""}},
        infos=[{"id": 111, "name": "Товар"}]))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    mine = _run(db.execute(select(Product).where(Product.marketplace_account_id == acc.id))).scalars().all()
    assert len(mine) == 1 and mine[0].external_product_id == "111"


# ── Prices / stocks ──────────────────────────────────────────────────────────────
def test_price_decimal_and_unknown_null(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "prices"))
    _use(monkeypatch, _StubOzon(prices={"result": {"items": [
        {"product_id": 1, "offer_id": "A", "price": {"price": "123.45"}},
        {"product_id": 2, "offer_id": "B", "price": {}}], "last_id": ""}}))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    by = {r.external_row_id: r.price for r in _run(db.execute(select(ImportedProductRow))).scalars().all()}
    # ImportedProductRow.price is the pre-existing CSV snapshot column (float); unknown stays NULL.
    assert by["1"] == pytest.approx(123.45) and by["2"] is None


def test_price_update_keeps_stock_and_stock_keeps_price(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    # stock first
    sst = _run(_state(db, conn, store, "stocks"))
    _use(monkeypatch, _StubOzon(stocks={"result": {"items": [
        {"product_id": 1, "offer_id": "A", "stocks": [{"type": "fbo", "present": 5},
                                                       {"type": "fbs", "present": 3}]}], "last_id": ""}}))
    _run(oz.fetch_and_persist_page(db, sst, "tok", "cid-1")); _run(db.commit())
    # then price for same product
    pst = _run(_state(db, conn, store, "prices"))
    _use(monkeypatch, _StubOzon(prices={"result": {"items": [
        {"product_id": 1, "offer_id": "A", "price": {"price": "50"}}], "last_id": ""}}))
    _run(oz.fetch_and_persist_page(db, pst, "tok", "cid-1")); _run(db.commit())
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.stock == 8 and row.price == Decimal("50")   # fbo+fbs summed once; both kept


def test_stock_repeat_replaces(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "stocks"))
    _use(monkeypatch, _StubOzon(stocks={"result": {"items": [
        {"product_id": 1, "stocks": [{"type": "fbo", "present": 10}]}], "last_id": ""}}))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    _use(monkeypatch, _StubOzon(stocks={"result": {"items": [
        {"product_id": 1, "stocks": [{"type": "fbo", "present": 4}]}], "last_id": ""}}))
    st.cursor = None; _run(db.commit())
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    rows = _run(db.execute(select(ImportedProductRow))).scalars().all()
    assert len(rows) == 1 and rows[0].stock == 4


# ── Postings FBO/FBS ─────────────────────────────────────────────────────────────
def test_fbo_fbs_do_not_collide(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    # same posting_number under both schemes must produce two distinct operations
    fbo_st = _run(_state(db, conn, store, "fbo_postings"))
    _use(monkeypatch, _StubOzon(fbo=[{"posting_number": "P-1", "status": "delivered",
                                      "in_process_at": "2026-07-01T00:00:00Z", "products": []}]))
    _run(oz.fetch_and_persist_page(db, fbo_st, "tok", "cid-1")); _run(db.commit())
    fbs_st = _run(_state(db, conn, store, "fbs_postings"))
    _use(monkeypatch, _StubOzon(fbs=[{"posting_number": "P-1", "status": "awaiting_deliver",
                                      "in_process_at": "2026-07-01T00:00:00Z", "products": []}]))
    _run(oz.fetch_and_persist_page(db, fbs_st, "tok", "cid-1")); _run(db.commit())
    ops = _run(db.execute(select(MarketplaceOperation))).scalars().all()
    assert {o.external_operation_id for o in ops} == {"fbo:P-1", "fbs:P-1"}
    assert all(o.operation_type == "order" for o in ops)
    assert {o.provider_operation_code for o in ops} == {"fbo", "fbs"}


def test_order_is_not_sale(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "fbs_postings"))
    _use(monkeypatch, _StubOzon(fbs=[{"posting_number": "P-9", "status": "delivered",
                                      "in_process_at": "2026-07-01T00:00:00Z",
                                      "products": [{"offer_id": "A", "quantity": 2}]}]))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "order" and op.status == "delivered" and op.quantity == 2


def test_posting_repeat_updates_status(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "fbs_postings"))
    _use(monkeypatch, _StubOzon(fbs=[{"posting_number": "P-1", "status": "awaiting_packaging",
                                      "in_process_at": "2026-07-01T00:00:00Z", "products": []}]))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    st.cursor = None; _run(db.commit())
    _use(monkeypatch, _StubOzon(fbs=[{"posting_number": "P-1", "status": "delivered",
                                      "in_process_at": "2026-07-01T00:00:00Z", "products": []}]))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    ops = _run(db.execute(select(MarketplaceOperation))).scalars().all()
    assert len(ops) == 1 and ops[0].status == "delivered"


# ── Finance ──────────────────────────────────────────────────────────────────────
def test_finance_signed_and_components_separate(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "finance"))
    _use(monkeypatch, _StubOzon(finance={"result": {"operations": [{
        "operation_id": "op1", "operation_type_name": "Продажа", "type": "orders",
        "amount": "1000.50", "operation_date": "2026-07-01", "currency_code": "RUB",
        "posting": {"posting_number": "P-1"}, "items": [{"sku": "111"}],
        "services": [{"name": "Комиссия за продажу", "price": "-150.00"},
                     {"name": "Логистика", "price": "-40.00"}]}], "page_count": 1}}))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    by = {o.operation_type: o.amount for o in _run(db.execute(select(MarketplaceOperation))).scalars().all()}
    assert by["sale"] == Decimal("1000.50")
    assert by["commission"] == Decimal("-150.00")
    assert by["logistics"] == Decimal("-40.00")


def test_finance_return_type(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "finance"))
    _use(monkeypatch, _StubOzon(finance={"result": {"operations": [{
        "operation_id": "op2", "operation_type_name": "Возврат", "type": "returns",
        "amount": "-500", "operation_date": "2026-07-02", "posting": {}, "services": []}],
        "page_count": 1}}))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "return" and op.amount == Decimal("-500")


def test_finance_repeat_idempotent(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "finance"))
    fin = {"result": {"operations": [{"operation_id": "op1", "operation_type_name": "Продажа",
                                      "type": "orders", "amount": "10", "operation_date": "2026-07-01",
                                      "services": [{"name": "Комиссия", "price": "-1"}]}],
                      "page_count": 1}}
    _use(monkeypatch, _StubOzon(finance=fin))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    st.cursor = None; _run(db.commit())
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(MarketplaceOperation))).scalar_one() == 2


# ── Returns ──────────────────────────────────────────────────────────────────────
def test_return_only_from_returns_endpoint(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "returns"))
    _use(monkeypatch, _StubOzon(returns={"returns": [{"id": 55, "posting_number": "P-1",
                                                     "product": {"sku": "111", "quantity": 1},
                                                     "price": "-300", "created_at": "2026-07-03"}],
                                        "has_next": False}))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "return" and op.external_operation_id == "55"
    assert op.amount == Decimal("-300")


# ── Isolation / sync ─────────────────────────────────────────────────────────────
def test_no_pii_stored(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "fbs_postings"))
    _use(monkeypatch, _StubOzon(fbs=[{"posting_number": "P-1", "status": "delivered",
                                      "in_process_at": "2026-07-01T00:00:00Z",
                                      "customer": {"name": "Иван", "phone": "+7999"},
                                      "products": [{"offer_id": "A", "quantity": 1}]}]))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    text = " ".join(str(getattr(op, c.name)) for c in MarketplaceOperation.__table__.columns)
    assert "Иван" not in text and "+7999" not in text


def test_cursor_after_commit_and_failure_keeps_it(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "prices"))
    _use(monkeypatch, _StubOzon(prices={"result": {"items": [{"product_id": 1, "price": {"price": "10"}}],
                                                   "last_id": "L2"}}))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    assert json.loads(st.cursor)["last_id"] == "L2"
    # a failure on the next page must not move the cursor
    class _Boom(_StubOzon):
        async def product_prices(self, *, token, client_id, last_id="", limit=1000):
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "boom")
    _use(monkeypatch, _Boom())
    with pytest.raises(ExecutionError):
        _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1"))
    _run(db.rollback())
    assert json.loads(st.cursor)["last_id"] == "L2"


def test_csv_unchanged(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    csv = ImportedProductRow(id=str(uuid.uuid4()), import_id="csv", user_id=uid, marketplace="ozon",
                             sku="CSV", price=7.0, source="csv",
                             marketplace_account_id=acc.id, marketplace_store_id=store.id)
    db.add(csv); _run(db.commit())
    st = _run(_state(db, conn, store, "prices"))
    _use(monkeypatch, _StubOzon(prices={"result": {"items": [{"product_id": 1, "price": {"price": "10"}}],
                                                   "last_id": ""}}))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    assert _run(db.get(ImportedProductRow, csv.id)).price == 7.0


# ── Scheduler (WB + Ozon together) ───────────────────────────────────────────────
def test_flag_off_zero_calls(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", False)
    called = {"n": 0}
    class _Track(_StubOzon):
        async def list_products(self, *, token, client_id, last_id="", limit=1000):
            called["n"] += 1; return super()._products if False else self._products
    monkeypatch.setattr(api_sync.ozon_ingest, "ozon_client", _Track())
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is False and called["n"] == 0


def test_scheduler_runs_ozon(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    stub = _StubOzon(
        products={"result": {"items": [{"product_id": 1, "offer_id": "A"}], "last_id": ""}},
        infos=[{"id": 1, "name": "Товар"}],
        prices={"result": {"items": [{"product_id": 1, "price": {"price": "10"}}], "last_id": ""}},
        stocks={"result": {"items": [{"product_id": 1, "stocks": [{"type": "fbo", "present": 3}]}], "last_id": ""}},
        fbs=[{"posting_number": "P-1", "status": "delivered", "in_process_at": "2026-07-01T00:00:00Z",
              "products": [{"offer_id": "A", "quantity": 1}]}])
    monkeypatch.setattr(api_sync.ozon_ingest, "ozon_client", stub)
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is True and out["connections"] == 1
    assert _run(db.execute(select(func.count()).select_from(Product))).scalar_one() == 1
    ops = _run(db.execute(select(MarketplaceOperation))).scalars().all()
    assert any(o.operation_type == "order" for o in ops)
    states = _run(db.execute(select(ApiSyncState))).scalars().all()
    assert not any(s.status in ("paused", "failed") for s in states)


def test_unverified_not_synced(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, verified=False))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    monkeypatch.setattr(api_sync.ozon_ingest, "ozon_client", _StubOzon())
    out = _run(api_sync.run_api_sync_once(db))
    assert out["connections"] == 0


def test_archived_store_no_sync(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, store_status="archived"))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    monkeypatch.setattr(api_sync.ozon_ingest, "ozon_client", _StubOzon())
    _run(api_sync.run_api_sync_once(db))
    assert _run(db.execute(select(func.count()).select_from(ApiSyncState))).scalar_one() == 0


def test_auth_pauses_ozon(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    class _Auth(_StubOzon):
        async def list_products(self, *, token, client_id, last_id="", limit=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def product_prices(self, *, token, client_id, last_id="", limit=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def product_stocks(self, *, token, client_id, last_id="", limit=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def posting_fbo_list(self, *, token, client_id, since, to, offset=0, limit=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def posting_fbs_list(self, *, token, client_id, since, to, offset=0, limit=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def finance_transactions(self, *, token, client_id, date_from, date_to, page=1, page_size=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def returns_list(self, *, token, client_id, last_id=0, limit=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def list_actions(self, *, token, client_id):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def action_products(self, *, token, client_id, action_id, last_id=0, limit=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
    monkeypatch.setattr(api_sync.ozon_ingest, "ozon_client", _Auth())
    _run(api_sync.run_api_sync_once(db))
    states = _run(db.execute(select(ApiSyncState))).scalars().all()
    assert states and all(s.status == "paused" and s.last_safe_error_code == "AUTH" for s in states)


def test_ozon_failure_does_not_stop_other_connection(monkeypatch):
    db = _run(_new_db())
    uid1, acc1, store1, conn1 = _run(_seed(db, client_id="bad"))
    uid2, acc2, store2, conn2 = _run(_seed(db, client_id="good"))
    # give conn2 a different token so we can tell them apart in the stub
    cred2 = _run(db.execute(select(ApiCredential).where(ApiCredential.connection_id == conn2.id))).scalars().first()
    cred2.secret_enc = credential_vault.encrypt("good-token"); _run(db.commit())
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)

    class _Split(_StubOzon):
        async def list_products(self, *, token, client_id, last_id="", limit=1000):
            if client_id == "bad":
                raise ExecutionError(ExecutionError.AUTH, "auth")
            return {"result": {"items": [{"product_id": 1, "offer_id": "A"}], "last_id": ""}}
        async def product_info_list(self, *, token, client_id, product_ids):
            return [{"id": 1, "name": "Товар"}]
    def _mk(*a, **k):
        return _Split()
    monkeypatch.setattr(api_sync.ozon_ingest, "ozon_client", _Split())
    _run(api_sync.run_api_sync_once(db))
    got = _run(db.execute(select(Product).where(Product.marketplace_account_id == acc2.id))).scalars().all()
    assert len(got) == 1


def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["atl1a2b3c4d01"]
