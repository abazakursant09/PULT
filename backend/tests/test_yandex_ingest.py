"""PULT-LAUNCH-1.4.5G — Yandex API ingestion + multi-campaign scheduling.

No network: the Yandex client is stubbed with official-shaped fixtures. Honesty rules mirror WB/Ozon
and add Yandex's two-axis reality: business-level cards vs campaign-level prices/stocks/orders/
returns; one offer in two campaigns is one Product with two Placements; two campaigns never share a
snapshot or a namespace; an order is not a sale; a return comes only from type=RETURN; finance is
declared but UNSUPPORTED and makes zero calls; an unmapped store makes zero calls; WB and Ozon still
run through the same scheduler.
"""
import asyncio
import json
import uuid
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
from services.marketplace.ingest import yandex as yx
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


async def _seed(db, *, business_id="BIZ-1", verified=True, campaigns=("111",), store_status="active"):
    """A verified Yandex cabinet with one store per campaignId in `campaigns`."""
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace="yandex",
                             identity_status="verified" if verified else "unverified",
                             external_account_id=business_id if verified else None, label="Каб")
    db.add(acc)
    stores = []
    for cid in campaigns:
        st = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="yandex",
                              store_key=str(uuid.uuid4()), external_store_id=cid, label=f"Store {cid}",
                              source="api", status=store_status)
        db.add(st); stores.append(st)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=uid, marketplace="yandex", status="connected",
        verification_status="verified" if verified else "unverified", scopes=["feedbacks"],
        marketplace_account_id=acc.id, workspace_id=wid)
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("y-token"),
                         verification_status="verified"))
    await db.commit()
    return uid, acc, stores, conn


async def _state(db, conn, store, dt):
    st = ApiSyncState(marketplace_connection_id=conn.id, marketplace_account_id=conn.marketplace_account_id,
                      marketplace_store_id=store.id, data_type=dt, status="pending")
    st._owner_user_id = conn.user_id
    db.add(st); await db.commit()
    return st


class _StubYx:
    def __init__(self, *, mappings=None, prices=None, stocks=None, orders=None, returns=None):
        self._mappings = mappings or {"result": {"offerMappings": [], "paging": {}}}
        self._prices = prices or {"result": {"offers": [], "paging": {}}}
        self._stocks = stocks or {"result": {"warehouses": [], "paging": {}}}
        self._orders = orders or {"orders": [], "paging": {}}
        self._returns = returns or {"result": {"returns": [], "paging": {}}}

    async def offer_mappings(self, *, token, business_id, page_token=None, limit=200):
        return self._mappings

    async def campaign_prices(self, *, token, campaign_id, page_token=None, limit=500):
        return self._prices

    async def campaign_stocks(self, *, token, campaign_id, page_token=None, limit=200):
        return self._stocks

    async def campaign_orders(self, *, token, campaign_id, from_date, to_date, page_token=None, limit=50):
        return self._orders

    async def campaign_returns(self, *, token, campaign_id, page_token=None, limit=50):
        return self._returns

    async def list_campaigns(self, *, token):
        return []


def _use(monkeypatch, stub):
    monkeypatch.setattr(yx, "yandex_client", stub)


def _map(offer_id, market_sku=None, name="Товар"):
    return {"offer": {"offerId": offer_id, "name": name}, "mapping": ({"marketSku": market_sku} if market_sku else {})}


# ── Products / identity ──────────────────────────────────────────────────────────
# 10. one business Product
def test_one_business_product(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "products"))
    _use(monkeypatch, _StubYx(mappings={"result": {"offerMappings": [_map("OF-1", "555")], "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    prods = _run(db.execute(select(Product))).scalars().all()
    assert len(prods) == 1 and prods[0].external_product_id == "555" and prods[0].sku == "OF-1"


# 11. one offer in two campaigns → one Product, two Placements
def test_offer_two_campaigns_two_placements(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, campaigns=("111", "222")))
    stub = _StubYx(mappings={"result": {"offerMappings": [_map("OF-1", "555")], "paging": {}}})
    _use(monkeypatch, stub)
    for store in stores:
        st = _run(_state(db, conn, store, "products"))
        _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(Product))).scalar_one() == 1
    placements = _run(db.execute(select(ProductPlacement))).scalars().all()
    assert len(placements) == 2
    assert {p.marketplace_store_id for p in placements} == {stores[0].id, stores[1].id}


# 12. SKU account-scoped: another account's OF-1 is not reused
def test_sku_account_scoped(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    other = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=str(uuid.uuid4()),
                               marketplace="yandex", identity_status="verified")
    db.add(other)
    db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="X", marketplace="yandex",
                   sku="OF-1", marketplace_account_id=other.id, external_product_id="999"))
    _run(db.commit())
    st = _run(_state(db, conn, stores[0], "products"))
    _use(monkeypatch, _StubYx(mappings={"result": {"offerMappings": [_map("OF-1", "555")], "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    mine = _run(db.execute(select(Product).where(Product.marketplace_account_id == acc.id))).scalars().all()
    assert len(mine) == 1 and mine[0].external_product_id == "555"


# 13. ambiguous SKU (no marketSku, duplicate offerId already ambiguous) does not pick first
def test_ambiguous_sku_not_first(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    # two products share SKU OF-1 in this account → resolver must be AMBIGUOUS, not pick one
    for ext in ("A", "B"):
        db.add(Product(id=str(uuid.uuid4()), user_id=uid, name=ext, marketplace="yandex",
                       sku="OF-1", marketplace_account_id=acc.id, external_product_id=ext))
    _run(db.commit())
    st = _run(_state(db, conn, stores[0], "prices"))
    _use(monkeypatch, _StubYx(prices={"result": {"offers": [
        {"id": "OF-1", "price": {"value": "10"}}], "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.product_id is None and row.link_status == "unassigned"


# 14. card is account-level, not duplicated across campaigns
def test_card_not_duplicated_across_campaigns(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, campaigns=("111", "222")))
    _use(monkeypatch, _StubYx(mappings={"result": {"offerMappings": [_map("OF-1", "555")], "paging": {}}}))
    for store in stores:
        st = _run(_state(db, conn, store, "cards"))
        _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(ImportedCardContentRow))).scalar_one() == 1


# ── Prices / stocks ──────────────────────────────────────────────────────────────
# 15. campaign A price does not leak into B
def test_price_campaign_isolated(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, campaigns=("111", "222")))
    a = _run(_state(db, conn, stores[0], "prices"))
    _use(monkeypatch, _StubYx(prices={"result": {"offers": [{"id": "OF-1", "price": {"value": "10"}}], "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, a, "tok")); _run(db.commit())
    rows = _run(db.execute(select(ImportedProductRow))).scalars().all()
    assert len(rows) == 1 and rows[0].marketplace_store_id == stores[0].id


# 16. repeat snapshot replaces, not sums
def test_stock_repeat_replaces(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "stocks"))
    _use(monkeypatch, _StubYx(stocks={"result": {"warehouses": [
        {"warehouseId": 1, "offers": [{"offerId": "OF-1", "stocks": [{"type": "AVAILABLE", "count": 10}]}]}],
        "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    st.cursor = None; _run(db.commit())
    _use(monkeypatch, _StubYx(stocks={"result": {"warehouses": [
        {"warehouseId": 1, "offers": [{"offerId": "OF-1", "stocks": [{"type": "AVAILABLE", "count": 4}]}]}],
        "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    rows = _run(db.execute(select(ImportedProductRow))).scalars().all()
    assert len(rows) == 1 and rows[0].stock == 4


# stock sums sellable buckets across warehouses ONCE
def test_stock_sum_across_warehouses(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "stocks"))
    _use(monkeypatch, _StubYx(stocks={"result": {"warehouses": [
        {"warehouseId": 1, "offers": [{"offerId": "OF-1", "stocks": [{"type": "AVAILABLE", "count": 5},
                                                                     {"type": "DEFECT", "count": 99}]}]},
        {"warehouseId": 2, "offers": [{"offerId": "OF-1", "stocks": [{"type": "FIT", "count": 3}]}]}],
        "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.stock == 8   # 5 (AVAILABLE) + 3 (FIT); DEFECT excluded


# 17/18. price keeps stock, stock keeps price
def test_price_and_stock_coexist(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    sst = _run(_state(db, conn, stores[0], "stocks"))
    _use(monkeypatch, _StubYx(stocks={"result": {"warehouses": [
        {"warehouseId": 1, "offers": [{"offerId": "OF-1", "stocks": [{"type": "AVAILABLE", "count": 7}]}]}],
        "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, sst, "tok")); _run(db.commit())
    pst = _run(_state(db, conn, stores[0], "prices"))
    _use(monkeypatch, _StubYx(prices={"result": {"offers": [{"id": "OF-1", "price": {"value": "50"}}], "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, pst, "tok")); _run(db.commit())
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.stock == 7 and row.price == Decimal("50")


# 19. unknown = NULL
def test_unknown_price_null(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "prices"))
    _use(monkeypatch, _StubYx(prices={"result": {"offers": [
        {"id": "A", "price": {"value": "12.30"}}, {"id": "B", "price": {}}], "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    by = {r.external_row_id: r.price for r in _run(db.execute(select(ImportedProductRow))).scalars().all()}
    assert by["A"] == pytest.approx(12.30) and by["B"] is None


# ── Orders / returns ─────────────────────────────────────────────────────────────
# 20. order is not sale
def test_order_not_sale(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "orders"))
    _use(monkeypatch, _StubYx(orders={"orders": [{"id": "O-1", "status": "PROCESSING",
        "substatus": "STARTED", "creationDate": "01-07-2026 10:00:00",
        "items": [{"offerId": "OF-1", "count": 2}], "itemsTotal": "1500"}], "paging": {}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "order" and op.quantity == 2 and op.amount == Decimal("1500")
    assert op.external_operation_id == "order:111:O-1"


# 21. campaign namespace prevents collision (same order id in two campaigns)
def test_order_namespace_no_collision(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, campaigns=("111", "222")))
    for store, cid in zip(stores, ("111", "222")):
        st = _run(_state(db, conn, store, "orders"))
        _use(monkeypatch, _StubYx(orders={"orders": [{"id": "O-1", "status": "DELIVERED",
            "creationDate": "01-07-2026 10:00:00", "items": []}], "paging": {}}))
        _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    ids = {o.external_operation_id for o in _run(db.execute(select(MarketplaceOperation))).scalars().all()}
    assert ids == {"order:111:O-1", "order:222:O-1"}


# 22. Decimal with sign
def test_amount_decimal(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "orders"))
    _use(monkeypatch, _StubYx(orders={"orders": [{"id": "O-2", "status": "DELIVERED",
        "creationDate": "01-07-2026 10:00:00", "items": [], "itemsTotal": "999.99"}], "paging": {}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert isinstance(op.amount, Decimal) and op.amount == Decimal("999.99")


# 23. repeat updates, does not duplicate (status change)
def test_order_repeat_updates(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "orders"))
    _use(monkeypatch, _StubYx(orders={"orders": [{"id": "O-1", "status": "PROCESSING",
        "creationDate": "01-07-2026 10:00:00", "items": []}], "paging": {}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    st.cursor = None; _run(db.commit())
    _use(monkeypatch, _StubYx(orders={"orders": [{"id": "O-1", "status": "DELIVERED",
        "creationDate": "01-07-2026 10:00:00", "items": []}], "paging": {}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    ops = _run(db.execute(select(MarketplaceOperation))).scalars().all()
    assert len(ops) == 1 and ops[0].status == "DELIVERED"


# 24/25. return only from RETURN endpoint; a non-purchase never becomes a return
def test_return_from_returns_endpoint(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "returns"))
    _use(monkeypatch, _StubYx(returns={"result": {"returns": [{"id": "R-1", "orderId": "O-1",
        "creationDate": "03-07-2026 09:00:00", "items": [{"offerId": "OF-1", "count": 1}]}], "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "return" and op.external_operation_id == "return:111:R-1"
    assert op.external_parent_id == "O-1" and op.amount is None   # no official refund amount


def test_order_never_becomes_return(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "orders"))
    _use(monkeypatch, _StubYx(orders={"orders": [{"id": "O-9", "status": "CANCELLED",
        "substatus": "USER_CHANGED_MIND", "creationDate": "01-07-2026 10:00:00", "items": []}], "paging": {}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "order"   # a cancelled order is still an order, never a return


# 26. no PII
def test_no_pii(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "orders"))
    _use(monkeypatch, _StubYx(orders={"orders": [{"id": "O-1", "status": "DELIVERED",
        "creationDate": "01-07-2026 10:00:00", "buyer": {"firstName": "Иван", "phone": "+7999"},
        "items": [{"offerId": "OF-1", "count": 1}]}], "paging": {}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    text = " ".join(str(getattr(op, c.name)) for c in MarketplaceOperation.__table__.columns)
    assert "Иван" not in text and "+7999" not in text


# ── Sync / cursor ────────────────────────────────────────────────────────────────
# 27/28. cursor after commit; failure keeps it
def test_cursor_after_commit_and_failure(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "prices"))
    _use(monkeypatch, _StubYx(prices={"result": {"offers": [{"id": "A", "price": {"value": "1"}}],
                                                  "paging": {"nextPageToken": "P2"}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert json.loads(st.cursor)["page_token"] == "P2"

    class _Boom(_StubYx):
        async def campaign_prices(self, *, token, campaign_id, page_token=None, limit=500):
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "boom")
    _use(monkeypatch, _Boom())
    with pytest.raises(ExecutionError):
        _run(yx.fetch_and_persist_page(db, st, "tok"))
    _run(db.rollback())
    _run(db.refresh(st))   # rollback expired the row; reload before reading in sync test code
    assert json.loads(st.cursor)["page_token"] == "P2"


# 29. restart continues from the persisted cursor
def test_restart_continues(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores[0], "prices"))
    seen = {}
    class _Track(_StubYx):
        async def campaign_prices(self, *, token, campaign_id, page_token=None, limit=500):
            seen["pt"] = page_token
            return {"result": {"offers": [{"id": "B", "price": {"value": "2"}}], "paging": {}}}
    st.cursor = json.dumps({"page_token": "P2"}); _run(db.commit())
    _use(monkeypatch, _Track())
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert seen["pt"] == "P2"


# ── Scheduler ────────────────────────────────────────────────────────────────────
def _sched_stub(monkeypatch, stub):
    monkeypatch.setattr(api_sync.yandex_ingest, "yandex_client", stub)


# 33. flag false → 0 calls
def test_flag_off_zero_calls(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", False)
    called = {"n": 0}
    class _Track(_StubYx):
        async def offer_mappings(self, *, token, business_id, page_token=None, limit=200):
            called["n"] += 1; return self._mappings
    _sched_stub(monkeypatch, _Track())
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is False and called["n"] == 0


def test_scheduler_runs_yandex(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    stub = _StubYx(
        mappings={"result": {"offerMappings": [_map("OF-1", "555")], "paging": {}}},
        prices={"result": {"offers": [{"id": "OF-1", "price": {"value": "10"}}], "paging": {}}},
        stocks={"result": {"warehouses": [{"warehouseId": 1, "offers": [
            {"offerId": "OF-1", "stocks": [{"type": "AVAILABLE", "count": 3}]}]}], "paging": {}}},
        orders={"orders": [{"id": "O-1", "status": "DELIVERED", "creationDate": "01-07-2026 10:00:00",
                            "items": [{"offerId": "OF-1", "count": 1}]}], "paging": {}})
    _sched_stub(monkeypatch, stub)
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is True and out["connections"] == 1
    assert _run(db.execute(select(func.count()).select_from(Product))).scalar_one() == 1
    ops = _run(db.execute(select(MarketplaceOperation))).scalars().all()
    assert any(o.operation_type == "order" for o in ops)
    states = _run(db.execute(select(ApiSyncState))).scalars().all()
    # finance parked unsupported; everything else synced/running, none failed
    fin = [s for s in states if s.data_type == "finance"]
    assert fin and fin[0].status == "unsupported"
    assert not any(s.status == "failed" for s in states)


# 35. archived store not synced
def test_archived_store_no_sync(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, store_status="archived"))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    _sched_stub(monkeypatch, _StubYx())
    _run(api_sync.run_api_sync_once(db))
    assert _run(db.execute(select(func.count()).select_from(ApiSyncState))).scalar_one() == 0


# 9/36. unmapped store → 0 calls, parked 'unmapped'; unsupported never synced
def test_unmapped_store_parked(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    stores[0].external_store_id = None; _run(db.commit())   # keyless / unmapped
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    called = {"n": 0}
    class _Track(_StubYx):
        async def offer_mappings(self, *, token, business_id, page_token=None, limit=200):
            called["n"] += 1; return self._mappings
        async def campaign_prices(self, *, token, campaign_id, page_token=None, limit=500):
            called["n"] += 1; return self._prices
    _sched_stub(monkeypatch, _Track())
    _run(api_sync.run_api_sync_once(db))
    states = _run(db.execute(select(ApiSyncState))).scalars().all()
    assert called["n"] == 0
    assert states and all(s.status in ("unmapped", "unsupported") for s in states)


# 30/31. AUTH pauses; one campaign failing does not stop another
def test_auth_pauses_and_isolation(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, campaigns=("111", "222")))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)

    class _Split(_StubYx):
        async def offer_mappings(self, *, token, business_id, page_token=None, limit=200):
            return {"result": {"offerMappings": [_map("OF-1", "555")], "paging": {}}}
        async def campaign_prices(self, *, token, campaign_id, page_token=None, limit=500):
            if campaign_id == "111":
                raise ExecutionError(ExecutionError.AUTH, "auth")
            return {"result": {"offers": [{"id": "OF-1", "price": {"value": "10"}}], "paging": {}}}
        async def campaign_stocks(self, *, token, campaign_id, page_token=None, limit=200):
            return {"result": {"warehouses": [], "paging": {}}}
        async def campaign_orders(self, *, token, campaign_id, from_date, to_date, page_token=None, limit=50):
            return {"orders": [], "paging": {}}
        async def campaign_returns(self, *, token, campaign_id, page_token=None, limit=50):
            return {"result": {"returns": [], "paging": {}}}
    _sched_stub(monkeypatch, _Split())
    _run(api_sync.run_api_sync_once(db))
    # campaign 222 prices synced despite 111 prices AUTH-paused
    store_by_cid = {s.external_store_id: s for s in stores}
    paused = _run(db.execute(select(ApiSyncState).where(
        ApiSyncState.marketplace_store_id == store_by_cid["111"].id,
        ApiSyncState.data_type == "prices"))).scalars().first()
    ok = _run(db.execute(select(ApiSyncState).where(
        ApiSyncState.marketplace_store_id == store_by_cid["222"].id,
        ApiSyncState.data_type == "prices"))).scalars().first()
    assert paused.status == "paused" and paused.last_safe_error_code == "AUTH"
    assert ok.status == "synced"


# 34. CSV row untouched
def test_csv_unchanged(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    csv = ImportedProductRow(id=str(uuid.uuid4()), import_id="csv", user_id=uid, marketplace="yandex",
                             sku="CSV", price=7.0, source="csv",
                             marketplace_account_id=acc.id, marketplace_store_id=stores[0].id)
    db.add(csv); _run(db.commit())
    st = _run(_state(db, conn, stores[0], "prices"))
    _use(monkeypatch, _StubYx(prices={"result": {"offers": [{"id": "OF-1", "price": {"value": "10"}}], "paging": {}}}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.get(ImportedProductRow, csv.id)).price == 7.0


# 37/38. WB and Ozon providers still run through the same scheduler
def test_wb_ozon_regression_registry():
    assert api_sync._PROVIDERS["wildberries"] is not None
    assert api_sync._PROVIDERS["ozon"] is not None
    assert api_sync._PROVIDERS["yandex"] is not None


# 40. single alembic head
def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["plp1a2b3c4d01"]
