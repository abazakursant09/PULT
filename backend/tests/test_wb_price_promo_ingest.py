"""PULT-LAUNCH-2.5D-WB-A+B — proven price/promotion EVIDENCE ingest (read-only, feature OFF).

No network: wb_client is stubbed with official-shaped fixtures. The writers only ever WRITE append-only
MarketplacePriceObservation rows; they never change a product's promotion state, never compute
revenue/subsidy/commission-base, and never invent a value WB did not prove. Money is Decimal, never
float; unknown is NULL, never 0; currency is never defaulted to RUB.

A (GET /api/v2/list/goods/filter → observation_kind='catalog'):
  price→catalog_price, discountedPrice→buyer_price, clubDiscountedPrice→club_buyer_price,
  currencyIsoCode4217→currency (proven only).

B (GET /api/v1/calendar/promotions[/details|/nomenclatures] → observation_kind='promotion'):
  ONLY regular (non-auto) promotions; only inAction=true proves participation; planPrice→buyer_price;
  seller_promo_price stays NULL; the numeric promotion id is identity, never the name.
"""
import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.api_credential import ApiCredential
from models.api_sync_state import ApiSyncState
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_price_observation import MarketplacePriceObservation as MPO
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.user import User
from models.workspace import Workspace
from services.marketplace import credential_vault
from services.marketplace.errors import ExecutionError
from services.marketplace.ingest import wb as wb_ingest
from services.marketplace.wb_client import wb_client
import tasks.api_sync as api_sync

REV = "mfd1a2b3c4d01"
PRIOR = "ozp1a2b3c4d01"

_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, label="cab", make_product=True, nm="111"):
    """A verified WB cabinet + primary store, and (optionally) one resolvable product+placement."""
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace="wildberries",
                             identity_status="verified", label=label)
    db.add(acc)
    store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="wildberries",
                             store_key="primary", label="Магазин", source="manual", status="active")
    db.add(store)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries", status="connected",
        verification_status="verified", scopes=["prices"], marketplace_account_id=acc.id, workspace_id=wid)
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="prices",
                         secret_enc=credential_vault.encrypt("wb-token"), verification_status="verified"))
    if make_product:
        prod = Product(id=str(uuid.uuid4()), user_id=uid, name="N", marketplace="wildberries", sku=nm,
                       marketplace_account_id=acc.id, external_product_id=nm)
        db.add(prod)
        db.add(ProductPlacement(id=str(uuid.uuid4()), product_id=prod.id, marketplace_store_id=store.id,
                                marketplace_account_id=acc.id, status="active", source="api"))
    await db.commit()
    return uid, acc, store, conn


async def _state(db, conn, store, dt):
    st = ApiSyncState(marketplace_connection_id=conn.id, marketplace_account_id=conn.marketplace_account_id,
                      marketplace_store_id=store.id, data_type=dt, status="pending")
    st._owner_user_id = conn.user_id
    db.add(st)
    await db.commit()
    return st


def _obs(db, **where):
    q = select(MPO)
    for k, v in where.items():
        q = q.where(getattr(MPO, k) == v)
    return _run(db.execute(q)).scalars().all()


class _Stub:
    """Configurable wb_client double for the two observation writers only."""
    def __init__(self, *, prices=None, promotions=None, details=None, nomenclatures=None):
        # prices: list[goods] OR list-of-pages (list[list]); promotions: list[promotion]
        self._prices = prices if prices is not None else []
        self._promotions = promotions if promotions is not None else []
        self._details = details or []
        # {promotion_id(str): [page, page, ...]} consumed in order per (promotion, offset)
        self._noms = nomenclatures or {}
        self._noms_calls = {}

    async def list_prices(self, *, token, offset=0, limit=1000):
        if self._prices and isinstance(self._prices[0], list):
            idx = offset // max(1, limit)
            return self._prices[idx] if idx < len(self._prices) else []
        return self._prices if offset == 0 else []

    async def list_promotions(self, *, token, start_date_time, end_date_time,
                              all_promo=False, limit=1000, offset=0):
        return self._promotions if offset == 0 else []

    async def promotion_details(self, *, token, promotion_ids):
        want = {str(p) for p in promotion_ids}
        return [d for d in self._details if str(d.get("id")) in want]

    async def promotion_nomenclatures(self, *, token, promotion_id, in_action=True, limit=1000, offset=0):
        pages = self._noms.get(str(promotion_id), [[]])
        i = self._noms_calls.get(str(promotion_id), 0)
        page = pages[min(i, len(pages) - 1)]
        self._noms_calls[str(promotion_id)] = i + 1
        return page


def _use(monkeypatch, stub):
    monkeypatch.setattr(wb_ingest, "wb_client", stub)


def _drain(db, st, monkeypatch, stub, *, kind, max_steps=60):
    _use(monkeypatch, stub)
    for _ in range(max_steps):
        res = _run(wb_ingest.fetch_and_persist_page(db, st, "tok"))
        _run(db.commit())
        if res["done"]:
            return res
    raise AssertionError(f"{kind} did not finish")


def _good(nm, price=None, disc=None, club=None, currency="RUB", **extra):
    size = {}
    if price is not None:
        size["price"] = price
    if disc is not None:
        size["discountedPrice"] = disc
    if club is not None:
        size["clubDiscountedPrice"] = club
    g = {"nmID": nm, "sizes": [size]}
    if currency is not None:
        g["currencyIsoCode4217"] = currency
    g.update(extra)
    return g


# ══ A — CATALOG PRICE OBSERVATIONS ═══════════════════════════════════════════════

def test_catalog_three_price_mapping(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "1500.50", "1200", "1100")]), kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.catalog_price == Decimal("1500.50")     # price -> catalog_price
    assert r.buyer_price == Decimal("1200")          # discountedPrice -> buyer_price
    assert r.club_buyer_price == Decimal("1100")     # clubDiscountedPrice -> club_buyer_price
    assert r.currency == "RUB" and r.currency_status == "proven"
    assert r.resolution_status == "resolved" and r.product_id is not None
    assert r.missing_fields == []


def test_change_only_unchanged_run_dedups(monkeypatch):
    # PULT-LAUNCH-2.5E-1: a second identical WB pass must NOT append a row (change-only wiring).
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "1500.50", "1200", "1100")]), kind="prices")
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "1500.50", "1200", "1100")]), kind="prices")
    assert len(_obs(db, observation_kind="catalog")) == 1


def test_change_only_changed_run_appends(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "1500.50", "1200", "1100")]), kind="prices")
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "1500.50", "1190", "1100")]), kind="prices")
    assert len(_obs(db, observation_kind="catalog")) == 2


def test_catalog_money_is_decimal_not_float(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "1500.55", "1200.10", "1100.99")]), kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert isinstance(r.catalog_price, Decimal)
    assert r.catalog_price == Decimal("1500.55") and r.buyer_price == Decimal("1200.10")  # exact, no float drift


def test_missing_club_price_is_null(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "1500", "1200", club=None)]), kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.club_buyer_price is None and "clubDiscountedPrice" in r.missing_fields


def test_missing_currency_unknown_no_rub(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "1500", "1200", "1100", currency=None)]), kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.currency is None and r.currency_status == "unknown"
    assert "currencyIsoCode4217" in r.missing_fields


def test_catalog_never_revenue_subsidy_commission(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "1500", "1200", "1100")]), kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.expected_seller_revenue is None and r.seller_revenue_status == "unknown"
    assert r.marketplace_subsidy is None and r.subsidy_status == "unknown"   # no subsidy by subtraction
    assert r.commission_base is None and r.commission_base_status == "unknown"
    assert r.seller_promo_price is None
    assert r.auto_action_enabled is None                                     # WB: always NULL
    assert r.provider_min_price is None


def test_catalog_unknown_fields_do_not_break(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    good = _good(111, "1500", "1200", "1100", brandNewWbField={"x": 1}, editableSizePrice=True)
    _drain(db, st, monkeypatch, _Stub(prices=[good]), kind="prices")
    assert len(_obs(db, observation_kind="catalog")) == 1


def test_catalog_unassigned_gets_no_foreign_product(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(999, "50", "40", "35")]), kind="prices")
    r = _obs(db, external_product_id="999")[0]
    assert r.resolution_status == "unassigned" and r.product_id is None


def test_two_stores_accounts_not_mixed(monkeypatch):
    db = _run(_new_db())
    _, accA, storeA, connA = _run(_seed(db, label="A", nm="111"))
    _, accB, storeB, connB = _run(_seed(db, label="B", nm="222"))
    stA = _run(_state(db, connA, storeA, "price_observations"))
    stB = _run(_state(db, connB, storeB, "price_observations"))
    _drain(db, stA, monkeypatch, _Stub(prices=[_good(111, "1", "1", "1")]), kind="prices")
    _drain(db, stB, monkeypatch, _Stub(prices=[_good(222, "2", "2", "2")]), kind="prices")
    a = _obs(db, marketplace_store_id=storeA.id)
    b = _obs(db, marketplace_store_id=storeB.id)
    assert len(a) == 1 and a[0].external_product_id == "111" and a[0].marketplace_account_id == accA.id
    assert len(b) == 1 and b[0].external_product_id == "222" and b[0].marketplace_account_id == accB.id


def test_repeat_within_run_no_duplicate(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    monkeypatch.setattr(wb_ingest, "_OBS_PAGE", 1)     # force multi-page so run_id persists
    _use(monkeypatch, _Stub(prices=[[_good(111, "1500", "1200", "1100")], []]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 1
    run_id = json.loads(st.cursor)["run_id"]
    st.cursor = json.dumps({"run_id": run_id, "offset": 0}); _run(db.commit())  # rewind, same run
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 1


def test_prices_repeat_page_fails_closed(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    monkeypatch.setattr(wb_ingest, "_OBS_PAGE", 1)

    class _Same(_Stub):   # WB ignores offset and returns the same page → must fail closed, never loop
        async def list_prices(self, *, token, offset=0, limit=1000):
            return [_good(111, "1500", "1200", "1100")]
    _use(monkeypatch, _Same())
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # offset 0 -> 1
    with pytest.raises(ExecutionError):
        _run(wb_ingest.fetch_and_persist_page(db, st, "tok"))                   # offset 1 -> same first id


# ══ B — PROMOTION PARTICIPATION OBSERVATIONS ═════════════════════════════════════

def test_participating_only_from_nomenclatures(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "promotions"))
    stub = _Stub(
        promotions=[{"id": 42, "name": "ХИТЫ", "type": "regular"}],
        details=[{"id": 42, "startDateTime": "2026-07-01T00:00:00Z", "endDateTime": "2026-07-31T00:00:00Z"}],
        nomenclatures={"42": [[{"id": 111, "inAction": True, "planPrice": "1000", "currencyCode": "RUB"}]]})
    _drain(db, st, monkeypatch, stub, kind="promotions")
    rows = _obs(db, observation_kind="promotion")
    assert len(rows) == 1
    r = rows[0]
    assert r.participation_status == "active"
    assert r.promotion_id == "42" and r.promotion_key == "42" and r.promotion_type == "wb_calendar"
    assert r.buyer_price == Decimal("1000")          # planPrice -> buyer_price
    assert r.seller_promo_price is None              # NOT the WB promo slot
    assert r.provider_valid_from is not None and r.provider_valid_to is not None
    assert st.coverage_complete is True


def test_auto_promotion_excluded_no_nomenclatures_call(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "promotions"))

    class _Guard(_Stub):
        async def promotion_nomenclatures(self, *a, **k):
            raise AssertionError("auto promotion must NOT trigger a nomenclatures call")
    stub = _Guard(promotions=[{"id": 7, "name": "AUTO", "type": "auto"}])
    _drain(db, st, monkeypatch, stub, kind="promotions")
    assert _obs(db, observation_kind="promotion") == []
    assert st.coverage_complete is True              # empty regular set = clean coverage


def test_candidate_inaction_false_creates_nothing(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "promotions"))
    stub = _Stub(
        promotions=[{"id": 9, "type": "regular"}], details=[{"id": 9}],
        nomenclatures={"9": [[{"id": 111, "inAction": False, "planPrice": "1000"}]]})
    _drain(db, st, monkeypatch, stub, kind="promotions")
    assert _obs(db, observation_kind="promotion") == []   # candidate ≠ participation, nothing written


def test_full_pagination_across_pages(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    prod2 = Product(id=str(uuid.uuid4()), user_id="x", name="N2", marketplace="wildberries", sku="222",
                    marketplace_account_id=acc.id, external_product_id="222")
    db.add(prod2)
    db.add(ProductPlacement(id=str(uuid.uuid4()), product_id=prod2.id, marketplace_store_id=store.id,
                            marketplace_account_id=acc.id, status="active", source="api"))
    _run(db.commit())
    st = _run(_state(db, conn, store, "promotions"))
    monkeypatch.setattr(wb_ingest, "_OBS_PAGE", 1)
    stub = _Stub(promotions=[{"id": 5, "type": "regular"}], details=[{"id": 5}],
                 nomenclatures={"5": [[{"id": 111, "inAction": True, "planPrice": "10"}],
                                      [{"id": 222, "inAction": True, "planPrice": "20"}], []]})
    _drain(db, st, monkeypatch, stub, kind="promotions")
    exts = {r.external_product_id for r in _obs(db, observation_kind="promotion")}
    assert exts == {"111", "222"} and st.coverage_complete is True


def test_empty_last_page_completes(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "promotions"))
    stub = _Stub(promotions=[{"id": 3, "type": "regular"}], details=[{"id": 3}],
                 nomenclatures={"3": [[]]})
    res = _drain(db, st, monkeypatch, stub, kind="promotions")
    assert res["done"] is True and _obs(db, observation_kind="promotion") == []
    assert st.coverage_complete is True


def test_nomenclatures_repeat_page_fails_closed(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "promotions"))
    monkeypatch.setattr(wb_ingest, "_OBS_PAGE", 1)
    # nomenclatures always returns the same single item regardless of offset → fail closed
    stub = _Stub(promotions=[{"id": 3, "type": "regular"}], details=[{"id": 3}],
                 nomenclatures={"3": [[{"id": 111, "inAction": True, "planPrice": "10"}]]})
    _use(monkeypatch, stub)
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # LIST -> noms
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # noms offset 0 -> 1
    with pytest.raises(ExecutionError):
        _run(wb_ingest.fetch_and_persist_page(db, st, "tok"))                   # offset 1 -> same first id
    assert st.coverage_complete is False


def test_mid_page_error_writes_no_negative_keeps_prior(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    # run 1 — clean active row
    st = _run(_state(db, conn, store, "promotions"))
    ok = _Stub(promotions=[{"id": 8, "type": "regular"}], details=[{"id": 8}],
               nomenclatures={"8": [[{"id": 111, "inAction": True, "planPrice": "10"}]]})
    _drain(db, st, monkeypatch, ok, kind="promotions")
    assert len(_obs(db, observation_kind="promotion")) == 1
    # run 2 — nomenclatures raises mid-pass; append-only keeps the prior proven row, coverage stays false
    st.cursor = None; st.coverage_complete = False; _run(db.commit())

    class _Boom(_Stub):
        async def promotion_nomenclatures(self, *a, **k):
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "boom")
    _use(monkeypatch, _Boom(promotions=[{"id": 8, "type": "regular"}], details=[{"id": 8}]))
    _run(wb_ingest.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # LIST
    with pytest.raises(ExecutionError):
        _run(wb_ingest.fetch_and_persist_page(db, st, "tok"))                   # noms boom
    _run(db.rollback())
    assert len(_obs(db, observation_kind="promotion")) == 1                     # prior active untouched
    assert st.coverage_complete is False
    assert _obs(db, participation_status="not_participating") == []             # never written


def test_action_id_is_identity_not_name(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "promotions"))
    stub = _Stub(
        promotions=[{"id": 100, "name": "СКИДКИ", "type": "regular"},
                    {"id": 200, "name": "СКИДКИ", "type": "regular"}],
        details=[{"id": 100}, {"id": 200}],
        nomenclatures={"100": [[{"id": 111, "inAction": True, "planPrice": "10"}]],
                       "200": [[{"id": 111, "inAction": True, "planPrice": "20"}]]})
    _drain(db, st, monkeypatch, stub, kind="promotions")
    keys = {r.promotion_key for r in _obs(db, observation_kind="promotion")}
    assert keys == {"100", "200"}      # same name, two distinct promotions by id


# ══ SAFETY ═══════════════════════════════════════════════════════════════════════

def test_flag_off_zero_calls_zero_rows(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", False)

    class _Explode:
        def __getattr__(self, name):
            raise AssertionError(f"wb_client called while flag OFF: {name}")
    monkeypatch.setattr(api_sync.wb_ingest, "wb_client", _Explode())
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is False
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 0


def test_no_write_endpoints_called(monkeypatch):
    # Guard whose ONLY methods are the four reads the writers may use; any other access raises —
    # so set_price/set_discount/set_auto_promotion/upload/participation can never be reached.
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))

    class _Guard:
        async def list_prices(self, *, token, offset=0, limit=1000):
            return [_good(111, "1500", "1200", "1100")] if offset == 0 else []

        async def list_promotions(self, *, token, start_date_time, end_date_time,
                                  all_promo=False, limit=1000, offset=0):
            return [{"id": 1, "type": "regular"}] if offset == 0 else []

        async def promotion_details(self, *, token, promotion_ids):
            return [{"id": 1}]

        async def promotion_nomenclatures(self, *, token, promotion_id, in_action=True, limit=1000, offset=0):
            return [{"id": 111, "inAction": True, "planPrice": "1000"}] if offset == 0 else []

        def __getattr__(self, name):
            raise AssertionError(f"forbidden WB call: {name}")

    stp = _run(_state(db, conn, store, "price_observations"))
    _drain(db, stp, monkeypatch, _Guard(), kind="prices")
    stm = _run(_state(db, conn, store, "promotions"))
    _drain(db, stm, monkeypatch, _Guard(), kind="promotions")
    assert len(_obs(db, observation_kind="catalog")) == 1
    assert len(_obs(db, observation_kind="promotion")) == 1


def test_stop_auto_promotion_stays_unsupported():
    from services.marketplace import executor
    assert "stop_auto_promotion" in executor._CONTAINED_ACTIONS


def test_secrets_and_nmid_not_logged(monkeypatch, caplog):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="9876543210"))
    st = _run(_state(db, conn, store, "price_observations"))
    with caplog.at_level("DEBUG"):
        _drain(db, st, monkeypatch,
               _Stub(prices=[_good(9876543210, "1500", "1200", "1100")]), kind="prices")
    # Only OUR code's logs (SQLAlchemy/aiosqlite DEBUG echo of the INSERT is a harness artifact, not a
    # log our writer emits).
    blob = "\n".join(r.getMessage() for r in caplog.records
                     if not r.name.startswith(("sqlalchemy", "aiosqlite")))
    for secret in ("wb-token", "9876543210"):        # token + distinctive nmID never logged
        assert secret not in blob


def test_catalog_and_promotion_coexist_one_run_id():
    # §5 — the schema lets a catalog AND a promotion observation share ONE ingest_run_id (observation_kind
    # is in the run-uniqueness key), so a run's evidence never self-collides.
    from sqlalchemy import create_engine
    eng = create_engine("sqlite://")
    c = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    Base.metadata.create_all(c)
    base = dict(marketplace_account_id="a", marketplace_store_id="s", product_id=None,
                external_product_id="111", resolution_status="unassigned", source="api",
                currency_status="unknown", seller_revenue_status="unknown",
                commission_base_status="unknown", subsidy_status="unknown",
                fetched_at=datetime(2026, 7, 28), last_verified_at=datetime(2026, 7, 28),
                missing_fields=[], created_at=datetime(2026, 7, 28))
    c.execute(MPO.__table__.insert().values(
        id="c1", ingest_run_id="RUN", observation_kind="catalog", promotion_id=None,
        promotion_key="__none__", promotion_type=None, participation_status=None, **base))
    c.execute(MPO.__table__.insert().values(
        id="p1", ingest_run_id="RUN", observation_kind="promotion", promotion_id="42",
        promotion_key="42", promotion_type="wb_calendar", participation_status="active", **base))
    n = c.execute(sa.select(sa.func.count()).select_from(MPO.__table__)
                  .where(MPO.__table__.c.ingest_run_id == "RUN")).scalar()
    assert n == 2
    c.close()


# ══ CLIENT — endpoint paths (no network) ═════════════════════════════════════════

def _capture(monkeypatch):
    calls = []

    async def _fake(method, path, *, token, auth_header="Authorization", extra_headers=None,
                    json=None, params=None):
        calls.append({"method": method, "path": path, "params": params})
        return {"data": {"promotions": [], "nomenclatures": []}}
    # the calendar sub-client is lazily created; force it, then patch its request
    monkeypatch.setattr(wb_client._calendar(), "request", _fake)
    return calls


def test_client_list_promotions_path(monkeypatch):
    calls = _capture(monkeypatch)
    _run(wb_client.list_promotions(token="t", start_date_time="a", end_date_time="b"))
    assert calls[0]["method"] == "GET" and calls[0]["path"] == "/api/v1/calendar/promotions"
    assert calls[0]["params"]["allPromo"] == "false"


def test_client_nomenclatures_path(monkeypatch):
    calls = _capture(monkeypatch)
    _run(wb_client.promotion_nomenclatures(token="t", promotion_id=42))
    assert calls[0]["method"] == "GET" and calls[0]["path"] == "/api/v1/calendar/promotions/nomenclatures"
    assert calls[0]["params"]["inAction"] == "true" and calls[0]["params"]["promotionID"] == 42


def test_client_details_path(monkeypatch):
    calls = _capture(monkeypatch)
    _run(wb_client.promotion_details(token="t", promotion_ids=[1, 2]))
    assert calls[0]["method"] == "GET" and calls[0]["path"] == "/api/v1/calendar/promotions/details"


# ══ MIGRATION ypo1a2b3c4d01 ══════════════════════════════════════════════════════

def _cfg(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "wcb_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    import db_migrations as dbm
    return dbm._alembic_config(), f"sqlite:///{tmp}"


def _cols(sync_url):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return {col["name"]: col for col in sa.inspect(c).get_columns("marketplace_price_observations")}
    finally:
        eng.dispose()


def test_single_alembic_head():
    from alembic.config import Config
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == [REV]


def test_migration_adds_nullable_column(monkeypatch):
    cfg, sync_url = _cfg(monkeypatch)
    command.upgrade(cfg, REV)
    assert _cols(sync_url)["club_buyer_price"]["nullable"] is True


def test_migration_upgrade_downgrade_reupgrade(monkeypatch):
    cfg, sync_url = _cfg(monkeypatch)
    command.upgrade(cfg, REV)
    assert "club_buyer_price" in _cols(sync_url)
    command.downgrade(cfg, PRIOR)
    assert "club_buyer_price" not in _cols(sync_url)
    command.upgrade(cfg, REV)                    # re-upgrade must succeed
    assert "club_buyer_price" in _cols(sync_url)


def _insert_min(sync_url, *, club):
    """A minimal catalog observation via raw SQL (FKs off on a fresh sqlite connection). Each insert
    uses a distinct ingest_run_id so the run-uniqueness constraint never masks the CHECK under test."""
    eng = sa.create_engine(sync_url)
    try:
        with eng.begin() as c:
            c.execute(sa.text(
                "INSERT INTO marketplace_price_observations "
                "(id, ingest_run_id, marketplace_account_id, marketplace_store_id, external_product_id, "
                " resolution_status, observation_kind, promotion_key, currency_status, seller_revenue_status, "
                " commission_base_status, subsidy_status, source, fetched_at, last_verified_at, "
                " missing_fields, created_at, club_buyer_price) "
                "VALUES (:id,:run,'a','s','E','unassigned','catalog','__none__','unknown','unknown',"
                " 'unknown','unknown','api','2026-07-28 00:00:00','2026-07-28 00:00:00','[]',"
                " '2026-07-28 00:00:00',:club)"),
                {"id": str(uuid.uuid4()), "run": str(uuid.uuid4()), "club": club})
    finally:
        eng.dispose()


def test_migration_check_rejects_negative_allows_zero(monkeypatch):
    cfg, sync_url = _cfg(monkeypatch)
    command.upgrade(cfg, REV)
    _insert_min(sync_url, club=0)                      # zero is a real, allowed value
    _insert_min(sync_url, club=None)                   # NULL allowed (old rows)
    with pytest.raises(Exception):                     # negative rejected by ck_price_obs_club_nonneg
        _insert_min(sync_url, club=-1)


def test_model_omitting_club_yields_null(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(999, "50", "40", club=None)]), kind="prices")
    assert _obs(db, external_product_id="999")[0].club_buyer_price is None


def test_club_price_keeps_decimal_and_zero(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, nm="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=[_good(111, "50", "40", "0")]), kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.club_buyer_price == Decimal("0")
