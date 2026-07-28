"""PULT-LAUNCH-2.5D-Yandex-A — proven campaign price EVIDENCE ingest (read-only, feature OFF).

No network: yandex_client is stubbed with official-shaped fixtures. The writer only ever WRITES
append-only MarketplacePriceObservation rows; it never changes price, never touches promos, never
computes revenue/subsidy/commission-base, and never invents a value Yandex did not prove. Money is
Decimal, never float; unknown is NULL, never 0; currency is `RUR`→`RUB` (ISO 4217) via an explicit
adapter, never a hidden default.

A (GET /v2/campaigns/{campaignId}/offer-prices → observation_kind='catalog'):
  price.value→buyer_price, price.discountBase→catalog_price, price.currencyId→currency (proven only).
  Store-scoped by campaignId — one campaign's price never lands on another store.
"""
import asyncio
import json
import uuid
from decimal import Decimal

import pytest
from alembic.config import Config
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
from services.marketplace.ingest import yandex as yx
from services.marketplace.yandex_client import yandex_client
import tasks.api_sync as api_sync

HEAD = "wcb1a2b3c4d01"

_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, business_id="BIZ-1", campaigns=("111",), products=(("111", "111"),)):
    """A verified Yandex cabinet, one store per campaignId, and (optionally) resolvable products.
    products: iterable of (campaignId, external_product_id) placed in that campaign's store."""
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace="yandex",
                             identity_status="verified", external_account_id=business_id, label="Каб")
    db.add(acc)
    stores = {}
    for cid in campaigns:
        st = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="yandex",
                              store_key=str(uuid.uuid4()), external_store_id=cid, label=f"Store {cid}",
                              source="api", status="active")
        db.add(st); stores[cid] = st
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=uid, marketplace="yandex", status="connected",
        verification_status="verified", scopes=["feedbacks"], marketplace_account_id=acc.id, workspace_id=wid)
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("y-token"), verification_status="verified"))
    for cid, ext in products:
        prod = Product(id=str(uuid.uuid4()), user_id=uid, name="N", marketplace="yandex", sku=ext,
                       marketplace_account_id=acc.id, external_product_id=ext)
        db.add(prod)
        db.add(ProductPlacement(id=str(uuid.uuid4()), product_id=prod.id, marketplace_store_id=stores[cid].id,
                                marketplace_account_id=acc.id, status="active", source="api"))
    await db.commit()
    return uid, acc, stores, conn


async def _state(db, conn, store, dt="price_observations"):
    st = ApiSyncState(marketplace_connection_id=conn.id, marketplace_account_id=conn.marketplace_account_id,
                      marketplace_store_id=store.id, data_type=dt, status="pending")
    st._owner_user_id = conn.user_id
    db.add(st); await db.commit()
    return st


def _obs(db, **where):
    q = select(MPO)
    for k, v in where.items():
        q = q.where(getattr(MPO, k) == v)
    return _run(db.execute(q)).scalars().all()


def _offer(ext, value=None, disc=None, currency="RUR", market_sku=None):
    price = {}
    if value is not None:
        price["value"] = value
    if disc is not None:
        price["discountBase"] = disc
    if currency is not None:
        price["currencyId"] = currency
    o = {"id": ext, "price": price}
    if market_sku is not None:
        o["marketSku"] = market_sku
    return o


def _prices(offers, next_token=None):
    return {"result": {"offers": offers, "paging": ({"nextPageToken": next_token} if next_token else {})}}


class _Stub:
    """Yandex client double: campaign_prices keyed by campaignId, optionally multi-page."""
    def __init__(self, by_campaign):
        # by_campaign: {campaignId: response OR [page, page, ...]}
        self._by = by_campaign
        self._calls = {}

    async def campaign_prices(self, *, token, campaign_id, page_token=None, limit=500):
        v = self._by.get(str(campaign_id), _prices([]))
        if isinstance(v, list):
            i = self._calls.get(str(campaign_id), 0)
            self._calls[str(campaign_id)] = i + 1
            return v[min(i, len(v) - 1)]
        return v


def _use(monkeypatch, stub):
    monkeypatch.setattr(yx, "yandex_client", stub)


def _drain(db, st, monkeypatch, stub, *, max_steps=40):
    _use(monkeypatch, stub)
    for _ in range(max_steps):
        res = _run(yx.fetch_and_persist_page(db, st, "tok"))
        _run(db.commit())
        if res["done"]:
            return res
    raise AssertionError("did not finish")


# ══ A — MAPPING ══════════════════════════════════════════════════════════════════

def test_value_discountbase_currency_mapping(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("111", "1500.50", "2000", "RUR")])}))
    r = _obs(db, observation_kind="catalog")[0]
    assert r.buyer_price == Decimal("1500.50")           # value -> buyer_price
    assert r.catalog_price == Decimal("2000")            # discountBase -> catalog_price
    assert r.currency == "RUB" and r.currency_status == "proven"   # RUR -> RUB
    assert r.resolution_status == "resolved" and r.product_id is not None
    assert r.missing_fields == []


def test_money_is_decimal_not_float(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("111", "1500.55", "2000.10")])}))
    r = _obs(db, observation_kind="catalog")[0]
    assert isinstance(r.buyer_price, Decimal) and r.buyer_price == Decimal("1500.55")


def test_missing_discountbase_null(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("111", "1500", disc=None)])}))
    r = _obs(db, observation_kind="catalog")[0]
    assert r.catalog_price is None and "discountBase" in r.missing_fields


def test_missing_currency_unknown_no_rub(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("111", "1500", "2000", currency=None)])}))
    r = _obs(db, observation_kind="catalog")[0]
    assert r.currency is None and r.currency_status == "unknown"     # no RUB default
    assert "currencyId" in r.missing_fields


def test_currency_rub_and_usd_pass_through(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, products=(("111", "A"), ("111", "B"))))
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("A", "1", "2", "RUB"),
                                                       _offer("B", "1", "2", "USD")])}))
    cur = {r.external_product_id: r.currency for r in _obs(db, observation_kind="catalog")}
    assert cur["A"] == "RUB" and cur["B"] == "USD"


def test_other_money_and_flags_null(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("111", "1500", "2000")])}))
    r = _obs(db, observation_kind="catalog")[0]
    assert r.seller_promo_price is None and r.club_buyer_price is None and r.provider_min_price is None
    assert r.auto_action_enabled is None and r.participation_status is None
    assert r.expected_seller_revenue is None and r.seller_revenue_status == "unknown"
    assert r.marketplace_subsidy is None and r.subsidy_status == "unknown"   # no subsidy by subtraction
    assert r.commission_base is None and r.commission_base_status == "unknown"


def test_unknown_response_fields_do_not_break(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    o = _offer("111", "1500", "2000"); o["brandNewField"] = {"x": 1}; o["price"]["vat"] = 7
    _drain(db, st, monkeypatch, _Stub({"111": _prices([o])}))
    assert len(_obs(db, observation_kind="catalog")) == 1


# ══ ISOLATION ════════════════════════════════════════════════════════════════════

def test_two_campaigns_one_business_not_mixed(monkeypatch):
    db = _run(_new_db())
    uid, acc, stores, conn = _run(_seed(db, campaigns=("111", "222"), products=()))
    # ONE business-level Product (offerId "A") placed in BOTH campaign stores — the real Yandex shape
    prod = Product(id=str(uuid.uuid4()), user_id=uid, name="N", marketplace="yandex", sku="A",
                   marketplace_account_id=acc.id, external_product_id="A")
    db.add(prod)
    for cid in ("111", "222"):
        db.add(ProductPlacement(id=str(uuid.uuid4()), product_id=prod.id,
                                marketplace_store_id=stores[cid].id, marketplace_account_id=acc.id,
                                status="active", source="api"))
    _run(db.commit())
    st1 = _run(_state(db, conn, stores["111"]))
    st2 = _run(_state(db, conn, stores["222"]))
    _drain(db, st1, monkeypatch, _Stub({"111": _prices([_offer("A", "10", "20")]),
                                        "222": _prices([_offer("A", "99", "199")])}))
    _drain(db, st2, monkeypatch, _Stub({"111": _prices([_offer("A", "10", "20")]),
                                        "222": _prices([_offer("A", "99", "199")])}))
    r1 = _obs(db, marketplace_store_id=stores["111"].id)
    r2 = _obs(db, marketplace_store_id=stores["222"].id)
    # same offerId "A" in two campaigns → two store-scoped rows, prices never copied across
    assert len(r1) == 1 and r1[0].buyer_price == Decimal("10")
    assert len(r2) == 1 and r2[0].buyer_price == Decimal("99")


def test_two_accounts_isolated(monkeypatch):
    db = _run(_new_db())
    _, accA, storesA, connA = _run(_seed(db, business_id="A", campaigns=("111",), products=(("111", "X"),)))
    _, accB, storesB, connB = _run(_seed(db, business_id="B", campaigns=("111",), products=(("111", "X"),)))
    stA = _run(_state(db, connA, storesA["111"]))
    stB = _run(_state(db, connB, storesB["111"]))
    _drain(db, stA, monkeypatch, _Stub({"111": _prices([_offer("X", "1", "2")])}))
    _drain(db, stB, monkeypatch, _Stub({"111": _prices([_offer("X", "3", "4")])}))
    a = _obs(db, marketplace_account_id=accA.id)
    b = _obs(db, marketplace_account_id=accB.id)
    assert len(a) == 1 and a[0].buyer_price == Decimal("1")
    assert len(b) == 1 and b[0].buyer_price == Decimal("3")


def test_unassigned_gets_no_foreign_product(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, products=(("111", "KNOWN"),)))
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("STRANGER", "5", "9")])}))
    r = _obs(db, external_product_id="STRANGER")[0]
    assert r.resolution_status == "unassigned" and r.product_id is None


def test_product_without_placement_stays_unassigned(monkeypatch):
    # a Product exists for the account but is NOT placed in this store → resolver must not link it
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, campaigns=("111", "222"),
                                                             products=(("222", "P"),)))
    st = _run(_state(db, conn, stores["111"]))     # sync store 111, where P has no placement
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("P", "5", "9")])}))
    r = _obs(db, marketplace_store_id=stores["111"].id)[0]
    # placement FK is store-scoped; P is placed only in 222, so an observation in 111 cannot resolve to
    # it (the composite FK would reject a resolved row) → unassigned, product_id NULL
    assert r.resolution_status == "unassigned" and r.product_id is None


def test_repeat_within_run_no_duplicate(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    _use(monkeypatch, _Stub({"111": _prices([_offer("111", "1500", "2000")], next_token="P2")}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 1
    run_id = json.loads(st.cursor)["run_id"]
    st.cursor = json.dumps({"run_id": run_id, "page_token": None}); _run(db.commit())   # rewind same run
    _use(monkeypatch, _Stub({"111": _prices([_offer("111", "1500", "2000")])}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 1


def test_new_run_appends_new_version(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("111", "1500", "2000")])}))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("111", "1400", "2000")])}))
    rows = _obs(db, observation_kind="catalog")
    assert len(rows) == 2 and len({r.ingest_run_id for r in rows}) == 2


# ══ PAGINATION ═══════════════════════════════════════════════════════════════════

def test_full_pagination(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, products=(("111", "A"), ("111", "B"))))
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": [
        _prices([_offer("A", "10", "20")], next_token="P2"),
        _prices([_offer("B", "30", "40")]),
    ]}))
    exts = {r.external_product_id for r in _obs(db, observation_kind="catalog")}
    assert exts == {"A", "B"}


def test_no_next_token_ends(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    res = _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("111", "1", "2")])}))
    assert res["done"] is True and st.cursor is None


def test_repeat_token_fails_closed(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))
    # every page returns the SAME nextPageToken → Yandex not advancing → fail closed
    _use(monkeypatch, _Stub({"111": _prices([_offer("111", "1", "2")], next_token="STUCK")}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # page1: None -> STUCK
    with pytest.raises(ExecutionError):
        _run(yx.fetch_and_persist_page(db, st, "tok"))                   # page2: STUCK == STUCK


def test_malformed_payload_no_crash_no_rows(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["111"]))

    class _Bad(_Stub):
        async def campaign_prices(self, *, token, campaign_id, page_token=None, limit=500):
            return {"result": {"offers": "not-a-list"}}   # malformed
    _drain(db, st, monkeypatch, _Bad({}))
    assert _obs(db, observation_kind="catalog") == []


def test_mid_run_error_keeps_prior_observations(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    # run 1 — clean
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Stub({"111": _prices([_offer("111", "1500", "2000")])}))
    assert len(_obs(db, observation_kind="catalog")) == 1
    # run 2 — client raises; append-only keeps prior row, run not complete
    st.cursor = None; _run(db.commit())

    class _Boom(_Stub):
        async def campaign_prices(self, *, token, campaign_id, page_token=None, limit=500):
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "boom")
    _use(monkeypatch, _Boom({}))
    with pytest.raises(ExecutionError):
        _run(yx.fetch_and_persist_page(db, st, "tok"))
    _run(db.rollback())
    assert len(_obs(db, observation_kind="catalog")) == 1   # prior observation untouched


# ══ SAFETY ═══════════════════════════════════════════════════════════════════════

def test_flag_off_zero_calls_zero_rows(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    monkeypatch.setattr(api_sync.settings, "api_data_sync_enabled", False)

    class _Explode:
        def __getattr__(self, name):
            raise AssertionError(f"yandex_client called while flag OFF: {name}")
    monkeypatch.setattr(api_sync.yandex_ingest, "yandex_client", _Explode())
    out = _run(api_sync.run_api_sync_once(db))
    assert out["enabled"] is False
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 0


def test_only_read_endpoint_used(monkeypatch):
    # Guard whose ONLY method is campaign_prices; any other access (price/promo writes, feedback,
    # tariffs) raises — so the writer can never reach a write or a promo/commission endpoint.
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))

    class _Guard:
        async def campaign_prices(self, *, token, campaign_id, page_token=None, limit=500):
            return _prices([_offer("111", "1500", "2000")])

        def __getattr__(self, name):
            raise AssertionError(f"forbidden Yandex call: {name}")
    st = _run(_state(db, conn, stores["111"]))
    _drain(db, st, monkeypatch, _Guard())
    assert len(_obs(db, observation_kind="catalog")) == 1


def test_finance_stays_unsupported():
    assert "finance" in yx.UNSUPPORTED and "price_observations" not in yx.UNSUPPORTED


def test_stop_auto_promotion_contained():
    from services.marketplace import executor
    assert "stop_auto_promotion" in executor._CONTAINED_ACTIONS


def test_secrets_and_ids_not_logged(monkeypatch, caplog):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, business_id="BIZSECRET",
                                                              campaigns=("9988776655",),
                                                              products=(("9988776655", "OFF7"),)))
    st = _run(_state(db, conn, stores["9988776655"]))
    with caplog.at_level("DEBUG"):
        _drain(db, st, monkeypatch, _Stub({"9988776655": _prices([_offer("OFF7", "1500", "2000")])}))
    blob = "\n".join(r.getMessage() for r in caplog.records
                     if not r.name.startswith(("sqlalchemy", "aiosqlite")))
    for secret in ("y-token", "BIZSECRET", "9988776655", "OFF7"):
        assert secret not in blob


def test_single_alembic_head_unchanged():
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == [HEAD]


# ══ CLIENT — endpoint path (no network) ══════════════════════════════════════════

def test_client_campaign_prices_is_get_offer_prices(monkeypatch):
    calls = []

    async def _fake(method, path, *, token, auth_header="Authorization", params=None, json=None):
        calls.append({"method": method, "path": path, "auth": auth_header})
        return {"result": {"offers": [], "paging": {}}}
    monkeypatch.setattr(yandex_client._api, "request", _fake)
    _run(yandex_client.campaign_prices(token="t", campaign_id="42"))
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "/v2/campaigns/42/offer-prices"
    assert calls[0]["auth"] == "Api-Key"
