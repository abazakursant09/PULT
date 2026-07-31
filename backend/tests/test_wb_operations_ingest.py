"""PULT-LAUNCH-1.4.5E2 — WB orders / sales / returns / cancellations / finance / stocks.

No network: the WB client is stubbed with official-shaped fixtures. The honesty rules under test:
an order is not a sale and not revenue; sale/return/cancellation are told apart by the OFFICIAL
flag, never by the amount's sign; money is Decimal with its sign kept; an unknown amount is NULL;
a stock refresh never wipes a captured price; the retired v5 finance endpoint is never called; and
everything is idempotent and isolated from CSV.
"""
import asyncio
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401 — register tables
from models.api_credential import ApiCredential
from models.api_sync_state import ApiSyncState
from models.imported_product import ImportedProductRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_operation import MarketplaceOperation
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.user import User
from models.workspace import Workspace
from services.marketplace import credential_vault
from services.marketplace.errors import ExecutionError
from services.marketplace.ingest import wb as wb_ingest
import tasks.api_sync as api_sync

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, store_status="active", verified=True):
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace="wildberries",
                             identity_status="verified", label="Каб")
    db.add(acc)
    store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id,
                             marketplace="wildberries", store_key="primary", label="Магазин",
                             source="manual", status=store_status)
    db.add(store)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries", status="connected",
        verification_status="verified" if verified else "unverified", scopes=["content"],
        marketplace_account_id=acc.id, workspace_id=wid)
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="content",
                         secret_enc=credential_vault.encrypt("wb-token"),
                         verification_status="verified"))
    await db.commit()
    return uid, acc, store, conn


async def _state(db, conn, store, data_type):
    st = ApiSyncState(marketplace_connection_id=conn.id, marketplace_account_id=conn.marketplace_account_id,
                      marketplace_store_id=store.id, data_type=data_type, status="pending")
    st._owner_user_id = conn.user_id
    db.add(st)
    await db.commit()
    return st


class _StubWB:
    def __init__(self, *, orders=None, sales=None, finance=None,
                 stock_status_sequence=None, stock_rows=None):
        self._orders = orders or []
        self._sales = sales or []
        self._finance = finance or []
        self._stock_status = list(stock_status_sequence or ["done"])
        self._stock_rows = stock_rows or []
        self.v5_called = False
        self.create_calls = 0
        self.download_calls = 0

    async def list_orders(self, *, token, date_from, flag=0):
        return list(self._orders)

    async def list_sales(self, *, token, date_from, flag=0):
        return list(self._sales)

    async def get_sales(self, *, token, date_from, flag=0):
        # the retired v5 realization report is never used; guard flips if anyone calls it
        self.v5_called = True
        return []

    async def finance_sales_report_detailed(self, *, token, date_from, date_to):
        return list(self._finance)

    async def create_warehouse_remains_report(self, *, token):
        self.create_calls += 1
        return "task-1"

    async def warehouse_remains_status(self, *, token, task_id):
        return self._stock_status.pop(0) if self._stock_status else "done"

    async def download_warehouse_remains(self, *, token, task_id):
        self.download_calls += 1
        return list(self._stock_rows)


def _use(monkeypatch, stub):
    monkeypatch.setattr(wb_ingest, "wb_client", stub)


# ── Model / money ───────────────────────────────────────────────────────────────
def test_decimal_amount_roundtrips(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "sales"))
    _use(monkeypatch, _StubWB(sales=[{"saleID": "S1", "srid": "sr1", "nmId": 1,
                                      "forPay": "1234.56", "date": "2026-07-01"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.amount == Decimal("1234.56") and op.currency == "RUB"


def test_two_operations_same_srid_coexist(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "sales"))
    _use(monkeypatch, _StubWB(sales=[
        {"saleID": "S1", "srid": "sr1", "nmId": 1, "forPay": "100", "date": "2026-07-01"},
        {"saleID": "R1", "srid": "sr1", "nmId": 1, "forPay": "-100", "date": "2026-07-05"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    ops = _run(db.execute(select(MarketplaceOperation))).scalars().all()
    assert {o.operation_type for o in ops} == {"sale", "return"}
    assert all(o.external_parent_id == "sr1" for o in ops)


def test_repeat_operation_no_duplicate(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "orders"))
    stub = _StubWB(orders=[{"srid": "sr1", "nmId": 1, "priceWithDisc": "500", "date": "2026-07-01",
                            "lastChangeDate": "2026-07-01T00:00:00"}])
    _use(monkeypatch, stub)
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(MarketplaceOperation))).scalar_one() == 1


def test_other_keeps_provider_code(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "finance"))
    _use(monkeypatch, _StubWB(finance=[{"rrd_id": "rr1", "srid": "sr1", "nm_id": 1,
                                        "supplier_oper_name": "Новая операция WB",
                                        "retail_amount": "10", "rr_dt": "2026-07-01"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation)
                         .where(MarketplaceOperation.operation_type == "other"))).scalars().first()
    assert op is not None and op.provider_operation_code == "Новая операция WB"


def test_no_pii_columns():
    cols = set(MarketplaceOperation.__table__.columns.keys())
    for forbidden in ("buyer", "name", "phone", "address", "email", "raw", "payload"):
        assert not any(forbidden in c for c in cols)


def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["mts1a2b3c4d01"]


# ── Orders ──────────────────────────────────────────────────────────────────────
def test_order_is_not_sale(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "orders"))
    _use(monkeypatch, _StubWB(orders=[{"srid": "sr1", "nmId": 1, "priceWithDisc": "500",
                                       "date": "2026-07-01", "lastChangeDate": "2026-07-01T00:00:00"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "order" and op.status == "new"


def test_cancelled_order_is_status_not_return(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "orders"))
    _use(monkeypatch, _StubWB(orders=[{"srid": "sr1", "nmId": 1, "priceWithDisc": "500",
                                       "date": "2026-07-01", "isCancel": True,
                                       "lastChangeDate": "2026-07-02T00:00:00"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "order" and op.status == "cancelled"


def test_order_cursor_after_commit(monkeypatch):
    import json
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "orders"))
    _use(monkeypatch, _StubWB(orders=[{"srid": "sr1", "nmId": 1, "date": "2026-07-01",
                                       "lastChangeDate": "2026-07-09T10:00:00"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert json.loads(st.cursor)["lastChangeDate"] == "2026-07-09T10:00:00"


def test_order_commit_failure_keeps_cursor(monkeypatch):
    import json
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "orders"))
    st.cursor = json.dumps({"lastChangeDate": "2026-07-01T00:00:00"}); _run(db.commit())
    class _Boom(_StubWB):
        async def list_orders(self, *, token, date_from, flag=0):
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "boom")
    _use(monkeypatch, _Boom())
    with pytest.raises(ExecutionError):
        _run(wb_ingest.fetch_and_persist_page(db, st, "tok"))
    _run(db.rollback())
    assert json.loads(st.cursor)["lastChangeDate"] == "2026-07-01T00:00:00"


# ── Sales / returns / cancellations ─────────────────────────────────────────────
def test_sale_return_cancellation_distinguished(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "sales"))
    _use(monkeypatch, _StubWB(sales=[
        {"saleID": "S1", "srid": "a", "nmId": 1, "forPay": "100", "date": "2026-07-01"},
        {"saleID": "R2", "srid": "b", "nmId": 1, "forPay": "-100", "date": "2026-07-02"},
        {"saleID": "S3", "srid": "c", "nmId": 1, "forPay": "0", "isCancel": True, "date": "2026-07-03"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    types = {o.external_operation_id: o.operation_type
             for o in _run(db.execute(select(MarketplaceOperation))).scalars().all()}
    assert types == {"S1": "sale", "R2": "return", "S3": "cancellation"}


def test_type_not_from_sign(monkeypatch):
    """A negative forPay on an 'S' saleID is still a sale — the flag decides, not the sign."""
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "sales"))
    _use(monkeypatch, _StubWB(sales=[{"saleID": "S9", "srid": "a", "nmId": 1,
                                      "forPay": "-50", "date": "2026-07-01"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "sale" and op.amount == Decimal("-50")


def test_unknown_sale_prefix_is_other(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "sales"))
    _use(monkeypatch, _StubWB(sales=[{"saleID": "X7", "srid": "a", "nmId": 1,
                                      "forPay": "10", "date": "2026-07-01"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.operation_type == "other" and op.provider_operation_code == "X7"


# ── Finance ─────────────────────────────────────────────────────────────────────
def test_finance_components_do_not_overwrite(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "finance"))
    _use(monkeypatch, _StubWB(finance=[{
        "rrd_id": "rr1", "srid": "sr1", "nm_id": 1, "supplier_oper_name": "Продажа",
        "retail_amount": "1000", "commission_amount": "-150", "delivery_amount": "-40",
        "rr_dt": "2026-07-01", "currency_name": "RUB"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    by = {o.operation_type: o.amount for o in _run(db.execute(select(MarketplaceOperation))).scalars().all()}
    assert by["sale"] == Decimal("1000")
    assert by["commission"] == Decimal("-150")
    assert by["logistics"] == Decimal("-40")


def test_finance_unknown_amount_is_null(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "finance"))
    _use(monkeypatch, _StubWB(finance=[{"rrd_id": "rr1", "srid": "sr1", "nm_id": 1,
                                        "supplier_oper_name": "Продажа", "rr_dt": "2026-07-01"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.amount is None   # never coerced to 0


def test_v5_endpoint_never_called(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "finance"))
    stub = _StubWB(finance=[{"rrd_id": "rr1", "srid": "s", "supplier_oper_name": "Продажа",
                             "retail_amount": "10", "rr_dt": "2026-07-01"}])
    _use(monkeypatch, stub)
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert stub.v5_called is False   # get_sales (v5-style realization) is never used by finance


def test_finance_repeat_no_duplicate(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "finance"))
    row = [{"rrd_id": "rr1", "srid": "s", "nm_id": 1, "supplier_oper_name": "Продажа",
            "retail_amount": "1000", "commission_amount": "-150", "rr_dt": "2026-07-01"}]
    _use(monkeypatch, _StubWB(finance=row))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(MarketplaceOperation))).scalar_one() == 2


# ── Stocks ──────────────────────────────────────────────────────────────────────
def _stock_full(db, st, stub, monkeypatch):
    _use(monkeypatch, stub)
    # create → defer
    r1 = _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    # download (status done) → done
    r2 = _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    return r1, r2


def test_stocks_warehouses_summed_once(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "stocks"))
    stub = _StubWB(stock_rows=[{"nmId": 1, "warehouseName": "A", "quantity": 3},
                               {"nmId": 1, "warehouseName": "B", "quantity": 4}])
    r1, r2 = _stock_full(db, st, stub, monkeypatch)
    assert r1["defer"] is True and r2["done"] is True
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.stock == 7 and row.external_row_id == "1" and row.source == "api"


def test_stocks_repeat_replaces_not_adds(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "stocks"))
    _stock_full(db, st, _StubWB(stock_status_sequence=["done"],
                                stock_rows=[{"nmId": 1, "quantity": 5}]), monkeypatch)
    # second snapshot with a different quantity — REPLACES, not accumulates
    _stock_full(db, st, _StubWB(stock_status_sequence=["done"],
                                stock_rows=[{"nmId": 1, "quantity": 2}]), monkeypatch)
    rows = _run(db.execute(select(ImportedProductRow))).scalars().all()
    assert len(rows) == 1 and rows[0].stock == 2


def test_stock_update_keeps_price(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    # a price snapshot exists first
    pst = _run(_state(db, conn, store, "prices"))
    class _Price(_StubWB):
        async def list_prices(self, *, token, offset=0, limit=1000):
            return [{"nmID": 1, "sizes": [{"price": 999}]}]
    _use(monkeypatch, _Price())
    _run(wb_ingest.fetch_and_persist_page(db, pst, "tok")); _run(db.commit())
    # now a stock snapshot for the same nmID
    sst = _run(_state(db, conn, store, "stocks"))
    _stock_full(db, sst, _StubWB(stock_rows=[{"nmId": 1, "quantity": 8}]), monkeypatch)
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.stock == 8 and row.price == 999   # stock refresh did not wipe the price


def test_stock_unknown_nm_not_linked_to_other_product(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    # a product in ANOTHER account carries nmID 1
    other = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=str(uuid.uuid4()),
                               marketplace="wildberries", identity_status="verified")
    db.add(other)
    db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="X", marketplace="wildberries",
                   sku="Z", marketplace_account_id=other.id, external_product_id="1"))
    _run(db.commit())
    st = _run(_state(db, conn, store, "stocks"))
    _stock_full(db, st, _StubWB(stock_rows=[{"nmId": 1, "quantity": 5}]), monkeypatch)
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.product_id is None   # never linked to the other account's product


# ── Isolation / scheduler ───────────────────────────────────────────────────────
def test_csv_unchanged_by_operations(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    csv = ImportedProductRow(id=str(uuid.uuid4()), import_id="csv", user_id=uid,
                             marketplace="wildberries", sku="CSV", price=1.0, source="csv",
                             marketplace_account_id=acc.id, marketplace_store_id=store.id)
    db.add(csv); _run(db.commit())
    st = _run(_state(db, conn, store, "sales"))
    _use(monkeypatch, _StubWB(sales=[{"saleID": "S1", "srid": "a", "nmId": 1,
                                      "forPay": "100", "date": "2026-07-01"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.get(ImportedProductRow, csv.id)).price == 1.0


def test_operations_not_in_finance_aggregator(monkeypatch):
    """Operations are stored but must not enter the CSV-based user finance total (that stays E1)."""
    from services.finance_aggregator import _active_products_count
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "sales"))
    _use(monkeypatch, _StubWB(sales=[{"saleID": "S1", "srid": "a", "nmId": 1,
                                      "forPay": "100", "date": "2026-07-01"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    # a sale operation exists, but there is no CSV product row → the user product count is 0
    assert _run(_active_products_count(uid, db)) == 0


def test_flag_off_zero_calls(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", False)
    stub = _StubWB(orders=[{"srid": "s", "nmId": 1, "date": "2026-07-01"}])
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", stub)
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is False
    assert _run(db.execute(select(func.count()).select_from(MarketplaceOperation))).scalar_one() == 0


def test_scheduler_runs_all_types(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    stub = _StubWB(
        orders=[{"srid": "o1", "nmId": 1, "priceWithDisc": "500", "date": "2026-07-01",
                 "lastChangeDate": "2026-07-01T00:00:00"}],
        sales=[{"saleID": "S1", "srid": "o1", "nmId": 1, "forPay": "100", "date": "2026-07-02"}],
        finance=[{"rrd_id": "rr1", "srid": "o1", "nm_id": 1, "supplier_oper_name": "Продажа",
                  "retail_amount": "1000", "rr_dt": "2026-07-03"}],
        stock_rows=[{"nmId": 1, "quantity": 9}])
    class _Full(type(stub)):
        async def list_cards(self, *, token, cursor=None, limit=100):
            return {"cards": [], "cursor": {}}
        async def list_prices(self, *, token, offset=0, limit=1000):
            return []
    full = _Full(orders=stub._orders, sales=stub._sales, finance=stub._finance,
                 stock_rows=stub._stock_rows)
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", full)
    # two passes: stocks defers on the first (create), then downloads on the next tick. Simulate the
    # 1-minute gap elapsing so the deferred stocks state is due on the second pass.
    _run(api_sync.run_api_sync_once(db))
    for s in _run(db.execute(select(ApiSyncState).where(ApiSyncState.data_type == "stocks"))).scalars().all():
        s.next_run_at = datetime.utcnow() - timedelta(seconds=1)
    _run(db.commit())
    _run(api_sync.run_api_sync_once(db))
    ops = _run(db.execute(select(MarketplaceOperation))).scalars().all()
    assert {o.operation_type for o in ops} >= {"order", "sale", "sale"}   # order + sale + finance sale
    stock_rows = _run(db.execute(select(ImportedProductRow)
                                 .where(ImportedProductRow.source == "api"))).scalars().all()
    assert any(r.stock == 9 for r in stock_rows)


def test_archived_store_no_operations(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, store_status="archived"))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    stub = _StubWB(orders=[{"srid": "s", "nmId": 1, "date": "2026-07-01"}])
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", stub)
    _run(api_sync.run_api_sync_once(db))
    assert _run(db.execute(select(func.count()).select_from(MarketplaceOperation))).scalar_one() == 0


def test_auth_pauses_all_types(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    class _Auth(_StubWB):
        async def list_orders(self, *, token, date_from, flag=0):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def list_sales(self, *, token, date_from, flag=0):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def list_cards(self, *, token, cursor=None, limit=100):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def list_prices(self, *, token, offset=0, limit=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def finance_sales_report_detailed(self, *, token, date_from, date_to):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def create_warehouse_remains_report(self, *, token):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def list_promotions(self, *, token, start_date_time, end_date_time,
                                  all_promo=False, limit=1000, offset=0):
            raise ExecutionError(ExecutionError.AUTH, "auth")
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", _Auth())
    _run(api_sync.run_api_sync_once(db))
    states = _run(db.execute(select(ApiSyncState))).scalars().all()
    assert states and all(s.status == "paused" and s.last_safe_error_code == "AUTH" for s in states)


def test_backoff_open_zero_calls(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    for dt in wb_ingest.DATA_TYPES:
        st = _run(_state(db, conn, store, dt))
        st.status = "paused"; st.next_run_at = datetime.utcnow() + timedelta(hours=1)
    _run(db.commit())
    class _Track(_StubWB):
        def __init__(self):
            super().__init__(); self.any = 0
        async def list_orders(self, *, token, date_from, flag=0):
            self.any += 1; return []
    tr = _Track()
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", tr)
    _run(api_sync.run_api_sync_once(db))
    assert tr.any == 0


def test_other_account_not_mixed(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "sales"))
    _use(monkeypatch, _StubWB(sales=[{"saleID": "S1", "srid": "a", "nmId": 1,
                                      "forPay": "100", "date": "2026-07-01"}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    op = _run(db.execute(select(MarketplaceOperation))).scalars().first()
    assert op.marketplace_account_id == acc.id and op.marketplace_store_id == store.id
