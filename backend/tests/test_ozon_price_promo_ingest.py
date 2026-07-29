"""PULT-LAUNCH-2.5D-Ozon-A+B — proven price/promotion EVIDENCE ingest (feature OFF).

No network: the Ozon client is stubbed with official-shaped fixtures. The writers only ever WRITE
append-only MarketplacePriceObservation rows; they never change a product's promotion state, never
compute revenue/subsidy/commission-base, and never invent a value the provider did not prove.

A (POST /v5/product/info/prices → observation_kind='catalog'):
  price→buyer_price, old_price→catalog_price, marketing_seller_price→seller_promo_price,
  min_price→provider_min_price, auto_action_enabled→auto_action_enabled, currency_code→currency
  (proven only). marketing_price is ignored (removed from v5). Absent values stay NULL + named in
  missing_fields; currency is never defaulted to RUB.

B (GET /v1/actions + POST /v1/actions/products → observation_kind='promotion'):
  only /v1/actions/products proves participation_status='active'; the action_id is the sole identity;
  candidates are never turned into a participation row; a repeating/partial page fails closed and
  never writes a negative nor erases a prior run's proven participation.
"""
import asyncio
import json
import os
import tempfile
import uuid
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
from services.marketplace.ingest import ozon as oz
from services.marketplace.ozon_client import ozon_client
import tasks.api_sync as api_sync

REV = "eco1a2b3c4d01"
PRIOR = "mpo1a2b3c4d01"

_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, client_id="cid-1", make_product=True, ext="111", offer="OF-1"):
    """A verified Ozon cabinet + primary store, and (optionally) one resolvable product+placement."""
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace="ozon",
                             identity_status="verified", external_account_id=client_id, label="Каб")
    db.add(acc)
    store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="ozon",
                             store_key="primary", label="Магазин", source="manual", status="active")
    db.add(store)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=uid, marketplace="ozon", status="connected",
        verification_status="verified", scopes=["content"], ozon_client_id=client_id,
        marketplace_account_id=acc.id, workspace_id=wid)
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="content",
                         secret_enc=credential_vault.encrypt("oz-token"), verification_status="verified"))
    if make_product:
        prod = Product(id=str(uuid.uuid4()), user_id=uid, name="N", marketplace="ozon", sku=offer,
                       marketplace_account_id=acc.id, external_product_id=ext)
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
    """Configurable Ozon double for the two observation writers only."""
    def __init__(self, *, prices=None, actions=None, action_products=None):
        self._prices = prices or {"result": {"items": [], "last_id": ""}}
        self._actions = actions if actions is not None else []
        # {action_id: [page_dict, page_dict, ...]} consumed in order per action_id
        self._ap = action_products or {}
        self._ap_calls = {}

    async def product_prices(self, *, token, client_id, last_id="", limit=1000):
        if callable(self._prices):
            return self._prices(last_id)
        return self._prices

    async def list_actions(self, *, token, client_id):
        return self._actions

    async def action_products(self, *, token, client_id, action_id, last_id=0, limit=1000):
        pages = self._ap.get(str(action_id), [{"result": {"products": [], "last_id": ""}}])
        i = self._ap_calls.get(str(action_id), 0)
        page = pages[min(i, len(pages) - 1)]
        self._ap_calls[str(action_id)] = i + 1
        return page


def _use(monkeypatch, stub):
    monkeypatch.setattr(oz, "ozon_client", stub)


def _drain(db, st, monkeypatch, stub, *, kind, max_steps=50):
    """Run fetch_and_persist_page until done (mirrors the scheduler's per-page loop)."""
    _use(monkeypatch, stub)
    for _ in range(max_steps):
        res = _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1"))
        _run(db.commit())
        if res["done"]:
            return res
    raise AssertionError(f"{kind} did not finish")


# ══ A — CATALOG PRICE OBSERVATIONS ═══════════════════════════════════════════════

_FULL_PRICE = {"result": {"items": [{
    "product_id": 111, "offer_id": "OF-1",
    "price": {"price": "1500.50", "old_price": "2000", "min_price": "900",
              "marketing_seller_price": "1300", "marketing_price": "1250",
              "currency_code": "RUB", "auto_action_enabled": True}}], "last_id": ""}}


def test_catalog_mapping_proven_fields(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=_FULL_PRICE), kind="prices")
    rows = _obs(db, observation_kind="catalog")
    assert len(rows) == 1
    r = rows[0]
    assert r.buyer_price == Decimal("1500.50")          # price -> buyer_price
    assert r.catalog_price == Decimal("2000")           # old_price -> catalog_price
    assert r.seller_promo_price == Decimal("1300")      # marketing_seller_price -> seller_promo_price
    assert r.provider_min_price == Decimal("900")       # min_price -> provider_min_price
    assert r.auto_action_enabled is True                # proven boolean
    assert r.currency == "RUB" and r.currency_status == "proven"
    assert r.resolution_status == "resolved" and r.product_id is not None
    assert r.missing_fields == []


def test_marketing_price_is_ignored(monkeypatch):
    # marketing_price is present but marketing_seller_price is ABSENT: seller_promo_price must stay
    # NULL and be flagged missing — the removed field is never used as a fallback.
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    stub = _Stub(prices={"result": {"items": [{
        "product_id": 111, "price": {"price": "1500", "marketing_price": "999"}}], "last_id": ""}})
    _drain(db, st, monkeypatch, stub, kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.seller_promo_price is None
    assert "marketing_seller_price" in r.missing_fields


def test_marketing_seller_price_is_not_revenue(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=_FULL_PRICE), kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.expected_seller_revenue is None and r.seller_revenue_status == "unknown"


def test_subsidy_is_never_computed(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=_FULL_PRICE), kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.marketplace_subsidy is None and r.subsidy_status == "unknown"


def test_commission_base_is_never_proven(monkeypatch):
    # commissions present in the payload must not become a proven commission base.
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    stub = _Stub(prices={"result": {"items": [{
        "product_id": 111, "price": {"price": "1500"},
        "commissions": {"sales_percent": "15", "fbo_fulfillment_amount": "40"}}], "last_id": ""}})
    _drain(db, st, monkeypatch, stub, kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.commission_base is None and r.commission_base_status == "unknown"


def test_missing_currency_stays_unknown_null(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    stub = _Stub(prices={"result": {"items": [{
        "product_id": 111, "price": {"price": "1500"}}], "last_id": ""}})
    _drain(db, st, monkeypatch, stub, kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.currency is None and r.currency_status == "unknown"
    assert "currency_code" in r.missing_fields          # no RUB default


def test_unknown_response_fields_do_not_break(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    stub = _Stub(prices={"result": {"items": [{
        "product_id": 111, "price": {"price": "1500", "some_new_ozon_field": {"x": 1}},
        "totally_unexpected": [1, 2, 3]}], "last_id": ""}})
    _drain(db, st, monkeypatch, stub, kind="prices")
    assert len(_obs(db, observation_kind="catalog")) == 1


def test_repeat_within_run_no_duplicate(monkeypatch):
    # Re-processing the SAME page inside the SAME run (same ingest_run_id) upserts, never duplicates.
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    page1 = {"result": {"items": [{"product_id": 111, "price": {"price": "1500"}}], "last_id": "P2"}}
    _use(monkeypatch, _Stub(prices=page1))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 1
    run_id = json.loads(st.cursor)["run_id"]
    # rewind the cursor to page 1 but keep the SAME run_id, feed the same page again
    st.cursor = json.dumps({"run_id": run_id, "last_id": ""}); _run(db.commit())
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 1


def test_unchanged_run_writes_no_new_row(monkeypatch):
    # PULT-LAUNCH-2.5E-1 change-only: a second pass with IDENTICAL evidence must NOT append a row.
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=_FULL_PRICE), kind="prices")
    _drain(db, st, monkeypatch, _Stub(prices=_FULL_PRICE), kind="prices")
    rows = _obs(db, observation_kind="catalog")
    assert len(rows) == 1                                   # the identical repeat was deduped


def test_changed_run_writes_fresh_row(monkeypatch):
    # A changed buyer price on the next pass → a NEW append-only version (a real change-point).
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch, _Stub(prices=_FULL_PRICE), kind="prices")
    changed = {"result": {"items": [{
        "product_id": 111, "offer_id": "OF-1",
        "price": {"price": "1499.00", "old_price": "2000", "min_price": "900",
                  "marketing_seller_price": "1300", "currency_code": "RUB",
                  "auto_action_enabled": True}}], "last_id": ""}}
    _drain(db, st, monkeypatch, _Stub(prices=changed), kind="prices")
    rows = _obs(db, observation_kind="catalog")
    assert len(rows) == 2 and len({r.ingest_run_id for r in rows}) == 2


def test_unassigned_product_gets_no_foreign_product(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, make_product=True, ext="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    stub = _Stub(prices={"result": {"items": [{"product_id": 999, "price": {"price": "50"}}], "last_id": ""}})
    _drain(db, st, monkeypatch, stub, kind="prices")
    r = _obs(db, external_product_id="999")[0]
    assert r.resolution_status == "unassigned" and r.product_id is None


def test_two_stores_not_mixed(monkeypatch):
    db = _run(_new_db())
    _, accA, storeA, connA = _run(_seed(db, client_id="A", ext="111"))
    _, accB, storeB, connB = _run(_seed(db, client_id="B", ext="222"))
    stA = _run(_state(db, connA, storeA, "price_observations"))
    stB = _run(_state(db, connB, storeB, "price_observations"))
    _drain(db, stA, monkeypatch,
           _Stub(prices={"result": {"items": [{"product_id": 111, "price": {"price": "1"}}], "last_id": ""}}),
           kind="prices")
    _drain(db, stB, monkeypatch,
           _Stub(prices={"result": {"items": [{"product_id": 222, "price": {"price": "2"}}], "last_id": ""}}),
           kind="prices")
    a = _obs(db, marketplace_store_id=storeA.id)
    b = _obs(db, marketplace_store_id=storeB.id)
    assert len(a) == 1 and a[0].external_product_id == "111" and a[0].marketplace_account_id == accA.id
    assert len(b) == 1 and b[0].external_product_id == "222" and b[0].marketplace_account_id == accB.id


def test_catalog_and_promotion_coexist(monkeypatch):
    # §4.3 — a catalog observation and a promotion observation for the same store/product coexist.
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    stp = _run(_state(db, conn, store, "price_observations"))
    _drain(db, stp, monkeypatch, _Stub(prices=_FULL_PRICE), kind="prices")
    stm = _run(_state(db, conn, store, "promotions"))
    stub = _Stub(actions=[{"id": 7, "title": "Акция"}],
                 action_products={"7": [{"result": {"products": [{"id": 111, "action_price": "1200"}],
                                                     "last_id": ""}}]})
    _drain(db, stm, monkeypatch, stub, kind="promotions")
    assert len(_obs(db, observation_kind="catalog")) == 1
    assert len(_obs(db, observation_kind="promotion")) == 1


# ══ B — PROMOTION PARTICIPATION OBSERVATIONS ═════════════════════════════════════

def test_participating_only_from_products(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "promotions"))
    stub = _Stub(actions=[{"id": 42, "title": "X", "date_start": "2026-07-01T00:00:00Z",
                           "date_end": "2026-07-31T00:00:00Z"}],
                 action_products={"42": [{"result": {"products": [{"id": 111, "action_price": "1200"}],
                                                     "last_id": ""}}]})
    _drain(db, st, monkeypatch, stub, kind="promotions")
    rows = _obs(db, observation_kind="promotion")
    assert len(rows) == 1
    r = rows[0]
    assert r.participation_status == "active"
    assert r.promotion_id == "42" and r.promotion_key == "42" and r.promotion_type == "ozon_action"
    assert r.seller_promo_price == Decimal("1200")
    assert r.provider_valid_from is not None and r.provider_valid_to is not None
    assert st.coverage_complete is True


def test_candidate_is_never_participation(monkeypatch):
    # The driver never calls candidates; even a rich candidates feed produces zero participation rows.
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "promotions"))

    class _Guard(_Stub):
        async def action_candidates(self, *a, **k):
            raise AssertionError("driver must NOT call action_candidates")
    stub = _Guard(actions=[{"id": 9}],
                  action_products={"9": [{"result": {"products": [], "last_id": ""}}]})
    _drain(db, st, monkeypatch, stub, kind="promotions")
    assert _obs(db, observation_kind="promotion") == []


def test_full_pagination_across_pages(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    # second product resolvable too
    prod2 = Product(id=str(uuid.uuid4()), user_id="x", name="N2", marketplace="ozon", sku="OF-2",
                    marketplace_account_id=acc.id, external_product_id="222")
    db.add(prod2)
    db.add(ProductPlacement(id=str(uuid.uuid4()), product_id=prod2.id, marketplace_store_id=store.id,
                            marketplace_account_id=acc.id, status="active", source="api"))
    _run(db.commit())
    st = _run(_state(db, conn, store, "promotions"))
    stub = _Stub(actions=[{"id": 5}], action_products={"5": [
        {"result": {"products": [{"id": 111, "action_price": "10"}], "last_id": "CUR1"}},
        {"result": {"products": [{"id": 222, "action_price": "20"}], "last_id": ""}},
    ]})
    _drain(db, st, monkeypatch, stub, kind="promotions")
    exts = {r.external_product_id for r in _obs(db, observation_kind="promotion")}
    assert exts == {"111", "222"}
    assert st.coverage_complete is True


def test_repeating_last_id_fails_closed(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "promotions"))
    # every page returns the SAME cursor token on a non-empty page → must fail closed, not loop
    stub = _Stub(actions=[{"id": 3}], action_products={"3": [
        {"result": {"products": [{"id": 111, "action_price": "10"}], "last_id": "STUCK"}}]})
    _use(monkeypatch, stub)
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())  # enumerate actions
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())  # page1: lid ""->STUCK
    with pytest.raises(ExecutionError):
        _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1"))                  # page2: STUCK==STUCK
    assert st.coverage_complete is False


def test_mid_page_error_writes_no_negative(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "promotions"))

    class _Boom(_Stub):
        async def action_products(self, *, token, client_id, action_id, last_id=0, limit=1000):
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "boom")
    stub = _Boom(actions=[{"id": 1}])
    _use(monkeypatch, stub)
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())   # enumerate actions
    with pytest.raises(ExecutionError):
        _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1"))
    _run(db.rollback())
    assert _obs(db, observation_kind="promotion") == []                          # no not_participating
    assert st.coverage_complete is False


def test_empty_actions_completes_coverage(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "promotions"))
    res = _drain(db, st, monkeypatch, _Stub(actions=[]), kind="promotions")
    assert res["done"] is True and res["count"] == 0
    assert _obs(db, observation_kind="promotion") == []
    assert st.coverage_complete is True


def test_partial_run_keeps_prior_participation(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    # run 1 — clean, writes an active row
    st = _run(_state(db, conn, store, "promotions"))
    ok = _Stub(actions=[{"id": 8}],
               action_products={"8": [{"result": {"products": [{"id": 111, "action_price": "10"}],
                                                   "last_id": ""}}]})
    _drain(db, st, monkeypatch, ok, kind="promotions")
    before = _run(db.execute(select(func.count()).select_from(MPO)
                             .where(MPO.observation_kind == "promotion"))).scalar_one()
    assert before == 1
    # run 2 — fails mid-pass; append-only means the prior proven row is untouched
    st.cursor = None; _run(db.commit())

    class _Boom(_Stub):
        async def action_products(self, *, token, client_id, action_id, last_id=0, limit=1000):
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "boom")
    _use(monkeypatch, _Boom(actions=[{"id": 8}]))
    _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1")); _run(db.commit())
    with pytest.raises(ExecutionError):
        _run(oz.fetch_and_persist_page(db, st, "tok", "cid-1"))
    _run(db.rollback())
    after = _run(db.execute(select(func.count()).select_from(MPO)
                            .where(MPO.observation_kind == "promotion"))).scalar_one()
    assert after == 1


def test_action_id_is_identity_not_title(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "promotions"))
    # two DIFFERENT actions share an identical title — they must remain two distinct promotions
    stub = _Stub(actions=[{"id": 100, "title": "Распродажа"}, {"id": 200, "title": "Распродажа"}],
                 action_products={"100": [{"result": {"products": [{"id": 111, "action_price": "10"}],
                                                       "last_id": ""}}],
                                  "200": [{"result": {"products": [{"id": 111, "action_price": "20"}],
                                                      "last_id": ""}}]})
    _drain(db, st, monkeypatch, stub, kind="promotions")
    keys = {r.promotion_key for r in _obs(db, observation_kind="promotion")}
    assert keys == {"100", "200"}


def test_promotions_other_store_isolated(monkeypatch):
    db = _run(_new_db())
    _, accA, storeA, connA = _run(_seed(db, client_id="A", ext="111"))
    _, accB, storeB, connB = _run(_seed(db, client_id="B", ext="222"))
    stA = _run(_state(db, connA, storeA, "promotions"))
    stB = _run(_state(db, connB, storeB, "promotions"))
    _drain(db, stA, monkeypatch,
           _Stub(actions=[{"id": 1}],
                 action_products={"1": [{"result": {"products": [{"id": 111, "action_price": "10"}],
                                                     "last_id": ""}}]}), kind="promotions")
    _drain(db, stB, monkeypatch,
           _Stub(actions=[{"id": 2}],
                 action_products={"2": [{"result": {"products": [{"id": 222, "action_price": "20"}],
                                                     "last_id": ""}}]}), kind="promotions")
    a = _obs(db, marketplace_store_id=storeA.id, observation_kind="promotion")
    b = _obs(db, marketplace_store_id=storeB.id, observation_kind="promotion")
    assert len(a) == 1 and a[0].promotion_key == "1"
    assert len(b) == 1 and b[0].promotion_key == "2"


# ══ SAFETY — flag OFF, no scheduler, no writes, no secrets ═══════════════════════

def test_flag_off_zero_calls_zero_observations(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", False)

    class _Explode:
        def __getattr__(self, name):
            raise AssertionError(f"provider called while flag OFF: {name}")
    monkeypatch.setattr(api_sync.ozon_ingest, "ozon_client", _Explode())
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is False
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 0


def test_no_write_or_hotsale_or_discount_endpoints_called(monkeypatch):
    # A guard whose ONLY defined methods are the three reads these writers may use. Any other access
    # (set_price, set_auto_promotion, action_candidates, hot-sale, discounts, …) raises.
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))

    class _Guard:
        async def product_prices(self, *, token, client_id, last_id="", limit=1000):
            return {"result": {"items": [{"product_id": 111, "price": {"price": "10"}}], "last_id": ""}}

        async def list_actions(self, *, token, client_id):
            return [{"id": 1}]

        async def action_products(self, *, token, client_id, action_id, last_id=0, limit=1000):
            return {"result": {"products": [{"id": 111, "action_price": "5"}], "last_id": ""}}

        def __getattr__(self, name):
            raise AssertionError(f"forbidden Ozon call: {name}")

    stp = _run(_state(db, conn, store, "price_observations"))
    _drain(db, stp, monkeypatch, _Guard(), kind="prices")
    stm = _run(_state(db, conn, store, "promotions"))
    _drain(db, stm, monkeypatch, _Guard(), kind="promotions")
    assert len(_obs(db, observation_kind="catalog")) == 1
    assert len(_obs(db, observation_kind="promotion")) == 1


def test_no_scheduler_wires_price_or_promo():
    # Retention pre-enable gate: nothing schedules the api sync (or these data types) on a cadence.
    import inspect
    import tasks.scheduler as scheduler
    src = inspect.getsource(scheduler)
    assert "run_api_sync_once" not in src
    assert "price_observations" not in src and "promotions" not in src


def test_keys_and_payload_not_logged(monkeypatch, caplog):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    with caplog.at_level("DEBUG"):
        _drain(db, st, monkeypatch,
               _Stub(prices={"result": {"items": [{"product_id": 111, "price": {"price": "10"}}],
                                        "last_id": ""}}), kind="prices")
    blob = "\n".join(r.getMessage() for r in caplog.records)
    for secret in ("oz-token", "cid-1", "Client-Id", "Api-Key"):
        assert secret not in blob


# ══ CLIENT — endpoint paths (no network) ═════════════════════════════════════════

def _capture_seller(monkeypatch):
    calls = []

    async def _fake(method, path, *, token, auth_header="Authorization", extra_headers=None,
                    json=None, params=None):
        calls.append({"method": method, "path": path, "auth": auth_header,
                      "headers": extra_headers or {}, "json": json})
        return {"result": []}
    monkeypatch.setattr(ozon_client._seller, "request", _fake)
    return calls


def test_client_list_actions_is_get_v1_actions(monkeypatch):
    calls = _capture_seller(monkeypatch)
    _run(ozon_client.list_actions(token="t", client_id="c"))
    assert calls[0]["method"] == "GET" and calls[0]["path"] == "/v1/actions"
    assert calls[0]["auth"] == "Api-Key" and calls[0]["headers"].get("Client-Id") == "c"


def test_client_action_products_posts_products(monkeypatch):
    calls = _capture_seller(monkeypatch)
    _run(ozon_client.action_products(token="t", client_id="c", action_id=42, last_id=0))
    assert calls[0]["method"] == "POST" and calls[0]["path"] == "/v1/actions/products"
    assert calls[0]["json"]["action_id"] == 42


def test_client_action_candidates_posts_candidates(monkeypatch):
    calls = _capture_seller(monkeypatch)
    _run(ozon_client.action_candidates(token="t", client_id="c", action_id=42, last_id=0))
    assert calls[0]["method"] == "POST" and calls[0]["path"] == "/v1/actions/candidates"


# ══ MIGRATION ypo1a2b3c4d01 ══════════════════════════════════════════════════════

def _cfg(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "ozp_test.db")
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


def test_migration_adds_two_nullable_columns(monkeypatch):
    cfg, sync_url = _cfg(monkeypatch)
    command.upgrade(cfg, REV)
    cols = _cols(sync_url)
    assert cols["provider_min_price"]["nullable"] is True
    assert cols["auto_action_enabled"]["nullable"] is True


def test_migration_upgrade_downgrade_reupgrade(monkeypatch):
    cfg, sync_url = _cfg(monkeypatch)
    command.upgrade(cfg, REV)
    assert "provider_min_price" in _cols(sync_url)
    command.downgrade(cfg, PRIOR)
    assert "provider_min_price" not in _cols(sync_url)
    assert "auto_action_enabled" not in _cols(sync_url)
    command.upgrade(cfg, REV)                       # re-upgrade must succeed
    assert "auto_action_enabled" in _cols(sync_url)


def test_omitting_new_columns_yields_null(monkeypatch):
    # Stands in for "old rows get NULL/NULL": a row inserted without the two new columns is NULL/NULL.
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch,
           _Stub(prices={"result": {"items": [{"product_id": 999, "price": {}}], "last_id": ""}}),
           kind="prices")
    r = _obs(db, external_product_id="999")[0]
    assert r.provider_min_price is None and r.auto_action_enabled is None


def test_provider_min_price_keeps_decimal_and_zero(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    _drain(db, st, monkeypatch,
           _Stub(prices={"result": {"items": [{"product_id": 111,
                                               "price": {"price": "10", "min_price": "0"}}], "last_id": ""}}),
           kind="prices")
    r = _obs(db, observation_kind="catalog")[0]
    assert r.provider_min_price == Decimal("0")     # zero is a real, kept value


def test_auto_action_enabled_true_false_null(monkeypatch):
    db = _run(_new_db()); uid, acc, store, conn = _run(_seed(db, ext="111"))
    st = _run(_state(db, conn, store, "price_observations"))
    for flag in (True, False):     # two full passes on ONE state → two append-only runs
        _drain(db, st, monkeypatch,
               _Stub(prices={"result": {"items": [{"product_id": 111,
                                                   "price": {"price": "10", "auto_action_enabled": flag}}],
                                        "last_id": ""}}), kind="prices")
    vals = {r.auto_action_enabled for r in _obs(db, observation_kind="catalog")}
    assert True in vals and False in vals
    # a value the provider never proved stays NULL
    db2 = _run(_new_db()); _, acc2, store2, conn2 = _run(_seed(db2, ext="111"))
    st2 = _run(_state(db2, conn2, store2, "price_observations"))
    _drain(db2, st2, monkeypatch,
           _Stub(prices={"result": {"items": [{"product_id": 111, "price": {"price": "10"}}], "last_id": ""}}),
           kind="prices")
    assert _obs(db2, observation_kind="catalog")[0].auto_action_enabled is None


def test_participation_status_rejects_eligible():
    # PARTICIPATION_STATUSES was NOT extended: a candidate/eligible value cannot be stored — the
    # DB CHECK refuses it, so candidates can never masquerade as participation. FK enforcement is left
    # off here so the ONLY thing that can reject the insert is the participation vocabulary CHECK.
    from datetime import datetime as _dtclass
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError
    eng = create_engine("sqlite://")
    c = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    Base.metadata.create_all(c)

    def _ins(status):
        c.execute(MPO.__table__.insert().values(
            id=str(uuid.uuid4()), ingest_run_id="r", marketplace_account_id="a", marketplace_store_id="s",
            product_id=None, external_product_id="E", resolution_status="unassigned",
            observation_kind="promotion", promotion_id="P", promotion_key="P",
            promotion_type="ozon_action", participation_status=status,
            currency_status="unknown", seller_revenue_status="unknown",
            commission_base_status="unknown", subsidy_status="unknown", source="api",
            fetched_at=_dtclass(2026, 7, 28), last_verified_at=_dtclass(2026, 7, 28),
            missing_fields=[]))

    _ins("active")                       # a valid participation value inserts fine (row shape is OK)
    with pytest.raises(IntegrityError):
        _ins("eligible")                 # the eligible/candidate value is refused by the CHECK
    c.close()
