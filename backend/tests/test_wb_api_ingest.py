"""PULT-LAUNCH-1.4.5E — Wildberries API ingestion.

No network: the WB client is stubbed with official-shaped fixtures. The tests hold the honesty
rules — nmID is identity, a repeat never duplicates, the cursor moves only on commit, API rows are
store-aware and isolated from CSV totals, and the whole path makes ZERO calls while the master
switch is off.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401 — register tables
from models.api_credential import ApiCredential
from models.api_sync_state import ApiSyncState
from models.imported_card_content import ImportedCardContentRow
from models.imported_product import ImportedProductRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
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


async def _seed(db, *, marketplace="wildberries", store_status="active", verified=True):
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace=marketplace,
                             identity_status="verified", label="Каб")
    db.add(acc)
    store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id,
                             marketplace=marketplace, store_key="primary", label="Магазин",
                             source="manual", status=store_status)
    db.add(store)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace, status="connected",
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


def _card(nm, vendor, title="Товар", photos=None):
    return {"nmID": nm, "vendorCode": vendor, "title": title, "description": "d",
            "brand": "B", "subjectName": "Кат", "characteristics": {"Цвет": "синий"},
            "photos": photos if photos is not None else ["u1", "u2"]}


def _cards_response(cards, *, total=None):
    last = cards[-1] if cards else {}
    return {"cards": cards, "cursor": {"updatedAt": "2026-07-01T00:00:00Z",
                                       "nmID": last.get("nmID"),
                                       "total": total if total is not None else len(cards)}}


class _StubWB:
    """Stands in for wb_client. Pages are handed out in order; each call returns the next."""

    def __init__(self, card_pages=None, price_pages=None, raise_on=None):
        self._card_pages = list(card_pages or [])
        self._price_pages = list(price_pages or [])
        self.card_calls = 0
        self.price_calls = 0
        self._raise = raise_on   # an ExecutionError to raise on the first card call

    async def list_cards(self, *, token, cursor=None, limit=100):
        self.card_calls += 1
        if self._raise is not None:
            raise self._raise
        return self._card_pages.pop(0) if self._card_pages else _cards_response([])

    async def list_prices(self, *, token, offset=0, limit=1000):
        self.price_calls += 1
        return self._price_pages.pop(0) if self._price_pages else []

    # E2 data types: safe empty defaults so the expanded scheduler completes in E1 tests.
    async def list_orders(self, *, token, date_from, flag=0):
        return []

    async def list_sales(self, *, token, date_from, flag=0):
        return []

    async def finance_sales_report_detailed(self, *, token, date_from, date_to):
        return []

    async def create_warehouse_remains_report(self, *, token):
        return "task"

    async def warehouse_remains_status(self, *, token, task_id):
        return "done"

    async def download_warehouse_remains(self, *, token, task_id):
        return []

    # PULT-LAUNCH-2.5D-WB — calendar reads default to empty so existing full-run tests stay neutral.
    async def list_promotions(self, *, token, start_date_time, end_date_time,
                              all_promo=False, limit=1000, offset=0):
        return []

    async def promotion_details(self, *, token, promotion_ids):
        return []

    async def promotion_nomenclatures(self, *, token, promotion_id, in_action=True,
                                     limit=1000, offset=0):
        return []


def _use_stub(monkeypatch, stub):
    monkeypatch.setattr(wb_ingest, "wb_client", stub)


# ── Identity ───────────────────────────────────────────────────────────────────
def test_nm_links_one_product(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "card_content"))
    _use_stub(monkeypatch, _StubWB(card_pages=[_cards_response([_card("100", "VC-1")])]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())

    prods = _run(db.execute(select(Product).where(Product.marketplace_account_id == acc.id))).scalars().all()
    assert len(prods) == 1
    assert prods[0].external_product_id == "100" and prods[0].sku == "VC-1"


def test_repeat_does_not_create_product(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "card_content"))
    stub = _StubWB(card_pages=[_cards_response([_card("100", "VC-1")]),
                               _cards_response([_card("100", "VC-1")])])
    _use_stub(monkeypatch, stub)
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    prods = _run(db.execute(select(Product))).scalars().all()
    assert len(prods) == 1
    cards = _run(db.execute(select(ImportedCardContentRow))).scalars().all()
    assert len(cards) == 1   # idempotent by nmID


def test_one_placement(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "card_content"))
    _use_stub(monkeypatch, _StubWB(card_pages=[_cards_response([_card("100", "VC-1")])]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    pls = _run(db.execute(select(ProductPlacement))).scalars().all()
    assert len(pls) == 1 and pls[0].marketplace_store_id == store.id


def test_same_sku_other_account_not_mixed(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    # a product with the same vendorCode in ANOTHER account must not be reused
    other = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=str(uuid.uuid4()),
                               marketplace="wildberries", identity_status="verified")
    db.add(other)
    db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="X", marketplace="wildberries",
                   sku="VC-1", marketplace_account_id=other.id, external_product_id="999"))
    _run(db.commit())
    st = _run(_state(db, conn, store, "card_content"))
    _use_stub(monkeypatch, _StubWB(card_pages=[_cards_response([_card("100", "VC-1")])]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    mine = _run(db.execute(select(Product).where(Product.marketplace_account_id == acc.id))).scalars().all()
    assert len(mine) == 1 and mine[0].external_product_id == "100"


def test_ambiguous_sku_not_first_pick(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    # two products in THIS account share the vendorCode and neither carries the incoming nmID
    for _ in range(2):
        db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="X", marketplace="wildberries",
                       sku="DUP", marketplace_account_id=acc.id, external_product_id=None))
    _run(db.commit())
    st = _run(_state(db, conn, store, "card_content"))
    _use_stub(monkeypatch, _StubWB(card_pages=[_cards_response([_card("777", "DUP")])]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    # a NEW product is created for the nmID (the ambiguity is on SKU, but nmID is a clean new id)
    row = _run(db.execute(select(ImportedCardContentRow))).scalars().first()
    assert row.external_row_id == "777"
    # the card is linked to its own nmID product, never silently to one of the two DUP rows
    assert row.product_id is not None
    linked = _run(db.get(Product, row.product_id))
    assert linked.external_product_id == "777"


def test_ambiguous_without_nm_stays_unassigned(monkeypatch):
    """A price row (no card, no new Product) whose SKU is ambiguous is left unassigned."""
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "prices"))
    _use_stub(monkeypatch, _StubWB(price_pages=[[{"nmID": 500, "sizes": [{"price": 1200}]}]]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.external_row_id == "500" and row.product_id is None and row.link_status == "unassigned"


# ── Idempotency ─────────────────────────────────────────────────────────────────
def test_repeat_page_zero_duplicates(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "prices"))
    page = [{"nmID": 1, "sizes": [{"price": 100}]}, {"nmID": 2, "sizes": [{"price": 200}]}]
    _use_stub(monkeypatch, _StubWB(price_pages=[page, page]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    n = _run(db.execute(select(func.count()).select_from(ImportedProductRow))).scalar_one()
    assert n == 2


def test_cursor_moves_only_after_commit(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "card_content"))
    # a full page (== limit) so `done` is False and a cursor is set
    cards = [_card(str(i), f"V{i}") for i in range(wb_ingest._CARD_PAGE)]
    _use_stub(monkeypatch, _StubWB(card_pages=[_cards_response(cards, total=wb_ingest._CARD_PAGE)]))
    res = _run(wb_ingest.fetch_and_persist_page(db, st, "tok"))
    assert res["done"] is False
    assert st.cursor is not None   # persisted in the same session as the rows
    _run(db.commit())
    assert json.loads(st.cursor)["nmID"] is not None


def test_failed_commit_does_not_advance_cursor(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "prices"))
    st.cursor = json.dumps({"offset": 0}); _run(db.commit())
    # a transport error mid-page: nothing persists, cursor stays put after rollback
    _use_stub(monkeypatch, _StubWB(price_pages=[], raise_on=None))
    class _Boom(_StubWB):
        async def list_prices(self, *, token, offset=0, limit=1000):
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "boom")
    _use_stub(monkeypatch, _Boom())
    with pytest.raises(ExecutionError):
        _run(wb_ingest.fetch_and_persist_page(db, st, "tok"))
    _run(db.rollback())
    assert json.loads(st.cursor)["offset"] == 0
    assert _run(db.execute(select(func.count()).select_from(ImportedProductRow))).scalar_one() == 0


def test_restart_resumes_from_cursor(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "prices"))
    st.cursor = json.dumps({"offset": 1000}); _run(db.commit())
    seen = {}
    class _Rec(_StubWB):
        async def list_prices(self, *, token, offset=0, limit=1000):
            seen["offset"] = offset
            return []
    _use_stub(monkeypatch, _Rec())
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert seen["offset"] == 1000   # resumed from the persisted cursor, not 0


# ── Store / account / source ────────────────────────────────────────────────────
def test_rows_carry_account_store_source(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "prices"))
    _use_stub(monkeypatch, _StubWB(price_pages=[[{"nmID": 1, "sizes": [{"price": 100}]}]]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.marketplace_account_id == acc.id
    assert row.marketplace_store_id == store.id
    assert row.source == "api"


def test_csv_rows_untouched_by_api_sync(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    csv_row = ImportedProductRow(id=str(uuid.uuid4()), import_id="csv-imp", user_id=uid,
                                 marketplace="wildberries", sku="CSV-1", price=999.0, source="csv",
                                 marketplace_account_id=acc.id, marketplace_store_id=store.id)
    db.add(csv_row); _run(db.commit())
    st = _run(_state(db, conn, store, "prices"))
    _use_stub(monkeypatch, _StubWB(price_pages=[[{"nmID": 1, "sizes": [{"price": 100}]}]]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    kept = _run(db.get(ImportedProductRow, csv_row.id))
    assert kept.source == "csv" and kept.price == 999.0 and kept.sku == "CSV-1"


def test_api_rows_excluded_from_user_product_count(monkeypatch):
    from services.finance_aggregator import _active_products_count
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    db.add(ImportedProductRow(id=str(uuid.uuid4()), import_id="csv", user_id=uid,
                              marketplace="wildberries", sku="CSV-1", source="csv",
                              marketplace_account_id=acc.id, marketplace_store_id=store.id))
    _run(db.commit())
    st = _run(_state(db, conn, store, "prices"))
    _use_stub(monkeypatch, _StubWB(price_pages=[[{"nmID": 1, "sizes": [{"price": 100}]},
                                                 {"nmID": 2, "sizes": [{"price": 200}]}]]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    # 2 API rows + 1 CSV row exist, but the user total counts ONLY the CSV one until 1.4.5H
    assert _run(_active_products_count(uid, db)) == 1


# ── Data honesty ────────────────────────────────────────────────────────────────
def test_unknown_price_stays_null_not_zero(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "prices"))
    _use_stub(monkeypatch, _StubWB(price_pages=[[{"nmID": 7, "sizes": [{}]}]]))   # no price field
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    row = _run(db.execute(select(ImportedProductRow))).scalars().first()
    assert row.price is None   # never coerced to 0


# ── Scheduler + flag ────────────────────────────────────────────────────────────
def test_flag_off_makes_zero_calls(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", False)
    stub = _StubWB(card_pages=[_cards_response([_card("1", "V")])])
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", stub)
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is False
    assert stub.card_calls == 0 and stub.price_calls == 0
    assert _run(db.execute(select(func.count()).select_from(ApiSyncState))).scalar_one() == 0


def test_flag_on_runs_and_persists(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    stub = _StubWB(card_pages=[_cards_response([_card("1", "V1")])],
                   price_pages=[[{"nmID": 1, "sizes": [{"price": 100}]}]])
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", stub)
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is True and out["connections"] == 1
    assert _run(db.execute(select(func.count()).select_from(ImportedCardContentRow))).scalar_one() == 1
    assert _run(db.execute(select(func.count()).select_from(ImportedProductRow))).scalar_one() == 1
    states = _run(db.execute(select(ApiSyncState))).scalars().all()
    # cards/prices/orders/sales/finance sync in one pass; stocks defers to a second tick (running).
    assert {s.status for s in states} <= {"synced", "running"}
    assert not any(s.status in ("paused", "failed") for s in states)


def test_unverified_connection_not_synced(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, verified=False))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    stub = _StubWB(card_pages=[_cards_response([_card("1", "V")])])
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", stub)
    out = _run(api_sync.run_api_sync_once(db))
    assert out["connections"] == 0 and stub.card_calls == 0


def test_archived_store_gets_no_data(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, store_status="archived"))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    stub = _StubWB(card_pages=[_cards_response([_card("1", "V")])])
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", stub)
    _run(api_sync.run_api_sync_once(db))
    assert stub.card_calls == 0
    assert _run(db.execute(select(func.count()).select_from(ImportedCardContentRow))).scalar_one() == 0


def test_auth_error_pauses_connection(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    class _Auth(_StubWB):
        async def list_cards(self, *, token, cursor=None, limit=100):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def list_prices(self, *, token, offset=0, limit=1000):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def list_orders(self, *, token, date_from, flag=0):
            raise ExecutionError(ExecutionError.AUTH, "auth")
        async def list_sales(self, *, token, date_from, flag=0):
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
    assert states and all(s.status == "paused" for s in states)
    assert all(s.last_safe_error_code == "AUTH" for s in states)


def test_backoff_open_makes_zero_calls(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    # a paused state with next_run_at in the future must not be called
    for dt in wb_ingest.DATA_TYPES:
        st = _run(_state(db, conn, store, dt))
        st.status = "paused"; st.next_run_at = datetime.utcnow() + timedelta(hours=1)
    _run(db.commit())
    stub = _StubWB(card_pages=[_cards_response([_card("1", "V")])])
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", stub)
    _run(api_sync.run_api_sync_once(db))
    assert stub.card_calls == 0 and stub.price_calls == 0


def test_one_connection_failure_does_not_stop_another(monkeypatch):
    db = _run(_new_db())
    uid1, acc1, store1, conn1 = _run(_seed(db))
    uid2, acc2, store2, conn2 = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", True)
    # give conn2 a DIFFERENT token so only conn1's token is the bad one
    cred2 = _run(db.execute(select(ApiCredential).where(ApiCredential.connection_id == conn2.id))).scalars().first()
    cred2.secret_enc = credential_vault.encrypt("good-token-2")
    _run(db.commit())
    bad_token = "wb-token"   # conn1's plaintext token

    class _Split(_StubWB):
        async def list_cards(self, *, token, cursor=None, limit=100):
            self.card_calls += 1
            if token == bad_token:
                raise ExecutionError(ExecutionError.AUTH, "auth")
            return _cards_response([_card("1", "V")])
        async def list_prices(self, *, token, offset=0, limit=1000):
            if token == bad_token:
                raise ExecutionError(ExecutionError.AUTH, "auth")
            return [{"nmID": 1, "sizes": [{"price": 100}]}]
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", _Split())
    _run(api_sync.run_api_sync_once(db))
    # conn2 (good token) still produced rows despite conn1 failing
    got = _run(db.execute(select(ImportedCardContentRow)
                          .where(ImportedCardContentRow.marketplace_account_id == acc2.id))).scalars().all()
    assert len(got) == 1


# ── Migration ───────────────────────────────────────────────────────────────────
def test_alembic_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["csr1a2b3c4d01"]
