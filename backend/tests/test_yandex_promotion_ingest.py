"""PULT-LAUNCH-2.5D-Yandex-B3 — account-level promotion evidence (read-only, feature OFF).

Two axes tested: (1) the DB invariants of the append-only parent + child tables (composite FKs, CHECK
matrix, UNIQUE, CASCADE) against SQLite with foreign_keys ON — the enforcement PostgreSQL gives in
production; (2) the read-only writer that turns getPromos + getPromoOffers into account-level evidence,
attributing campaignIds to stores ONLY for PARTIALLY_AUTO, never fanning AUTO/MANUAL out to a store,
keeping every provider_status verbatim, and failing closed on a broken page or a token cycle.
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
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
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
from models.marketplace_promotion_observation import (
    MarketplacePromotionObservation as PO,
    MarketplacePromotionStoreEvidence as SE,
    PARTICIPATION_STATUSES, ATTRIBUTION_STATUSES, RESOLUTION_STATUSES,
    CURRENCY_STATUSES, MAPPING_STATUSES,
)
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.user import User
from models.workspace import Workspace
from services.marketplace import credential_vault
from services.marketplace.errors import ExecutionError
from services.marketplace.ingest import yandex as yx
import tasks.api_sync as api_sync

HEAD = "eco1a2b3c4d01"
PRIOR = "wcb1a2b3c4d01"
NOW = datetime(2026, 7, 28, 12, 0, 0)

_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


# ══ DB-CONSTRAINT tests (sync sqlite, FK ON) ═════════════════════════════════════

def _fk_engine():
    eng = create_engine("sqlite://")

    @event.listens_for(eng, "connect")
    def _fk(dbapi_con, _rec):
        dbapi_con.execute("PRAGMA foreign_keys=ON")

    return eng


@pytest.fixture
def conn():
    eng = _fk_engine()
    c = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    Base.metadata.create_all(c)
    c.execute(text("INSERT INTO users(id,email,name,hashed_password) VALUES('u1','a@b.c','A','x')"))
    c.execute(text("INSERT INTO workspaces(id,owner_user_id,created_at) VALUES('ws1','u1',CURRENT_TIMESTAMP)"))
    c.execute(text("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status) "
                   "VALUES('accY','ws1','yandex','verified')"))
    c.execute(text("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status) "
                   "VALUES('accO','ws1','ozon','verified')"))
    c.execute(text("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status) "
                   "VALUES('accY2','ws1','yandex','verified')"))
    _store(c, "s1", "accY", "c1")
    _store(c, "s2", "accY2", "c9")     # store of ANOTHER yandex account
    c.execute(text("INSERT INTO products(id,user_id,name,marketplace,sku,marketplace_account_id) "
                   "VALUES('p1','u1','N','yandex','OF-1','accY')"))
    c.execute(text("INSERT INTO products(id,user_id,name,marketplace,sku,marketplace_account_id) "
                   "VALUES('pO','u1','N','ozon','OF-1','accO')"))
    yield c
    c.close()


def _store(c, sid, acc, cid):
    c.execute(text(
        "INSERT INTO marketplace_stores"
        "(id,marketplace_account_id,marketplace,store_key,external_store_id,label,source,status,created_at,updated_at) "
        "VALUES(:id,:a,'yandex',:k,:cid,'S','api','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
        {"id": sid, "a": acc, "k": sid, "cid": cid})


def _prow(**over):
    base = dict(id=str(uuid.uuid4()), ingest_run_id="r1", marketplace_account_id="accY",
                marketplace="yandex", product_id="p1", external_product_id="OF-1",
                resolution_status="resolved", promotion_id="PR1", promotion_type="yandex_promo",
                provider_status="AUTO", participation_status="active", auto_participation=True,
                attribution_status="account_wide", currency_status="unknown", source="api",
                provider_dataset="promos", fetched_at=NOW, last_verified_at=NOW,
                missing_fields=[], created_at=NOW)
    base.update(over)
    return base


def _ins_parent(conn, **over):
    conn.execute(PO.__table__.insert().values(**_prow(**over)))


def _child(**over):
    base = dict(id=str(uuid.uuid4()), promotion_observation_id="OBS1", marketplace_account_id="accY",
                external_store_id="c1", marketplace_store_id="s1", mapping_status="mapped", created_at=NOW)
    base.update(over)
    return base


def test_valid_parent_and_child(conn):
    _ins_parent(conn, id="OBS1")
    conn.execute(SE.__table__.insert().values(**_child()))
    assert conn.execute(select(func.count()).select_from(PO.__table__)).scalar() == 1
    assert conn.execute(select(func.count()).select_from(SE.__table__)).scalar() == 1


def test_product_other_account_blocked(conn):
    with pytest.raises(IntegrityError):     # pO belongs to accO, not accY
        _ins_parent(conn, product_id="pO")


def test_resolved_without_product_blocked(conn):
    with pytest.raises(IntegrityError):
        _ins_parent(conn, resolution_status="resolved", product_id=None)


def test_unassigned_with_product_blocked(conn):
    with pytest.raises(IntegrityError):
        _ins_parent(conn, resolution_status="unassigned", product_id="p1")


def test_unassigned_without_product_ok(conn):
    _ins_parent(conn, resolution_status="unassigned", product_id=None)
    assert conn.execute(select(func.count()).select_from(PO.__table__)).scalar() == 1


def test_exact_stores_requires_partially_auto(conn):
    with pytest.raises(IntegrityError):     # exact_stores only from PARTIALLY_AUTO
        _ins_parent(conn, provider_status="AUTO", attribution_status="exact_stores")
    _ins_parent(conn, provider_status="PARTIALLY_AUTO", attribution_status="exact_stores")  # ok


def test_provider_status_empty_or_too_long_blocked(conn):
    with pytest.raises(IntegrityError):
        _ins_parent(conn, provider_status="")
    with pytest.raises(IntegrityError):
        _ins_parent(conn, provider_status=" PADDED ")     # not trimmed
    with pytest.raises(IntegrityError):
        _ins_parent(conn, provider_status="X" * 65)       # exceeds 64


def test_unknown_provider_status_stored_verbatim(conn):
    _ins_parent(conn, provider_status="SOME_FUTURE_STATUS", participation_status="unknown",
                auto_participation=None, attribution_status="unresolved")
    r = conn.execute(select(PO.__table__.c.provider_status)).scalar()
    assert r == "SOME_FUTURE_STATUS"


def test_fixed_provenance_enforced(conn):
    for bad in (dict(marketplace="ozon"), dict(source="csv"), dict(provider_dataset="prices"),
                dict(promotion_type="ozon_action")):
        with pytest.raises(IntegrityError):
            _ins_parent(conn, **bad)


def test_negative_price_blocked(conn):
    with pytest.raises(IntegrityError):
        _ins_parent(conn, promo_buyer_price=Decimal("-1"))


def test_currency_proven_requires_currency(conn):
    with pytest.raises(IntegrityError):
        _ins_parent(conn, currency_status="proven", currency=None)
    with pytest.raises(IntegrityError):
        _ins_parent(conn, currency="rub")                 # not uppercase
    _ins_parent(conn, currency_status="proven", currency="RUB")   # ok


def test_period_end_before_start_blocked(conn):
    with pytest.raises(IntegrityError):
        _ins_parent(conn, promotion_start_at=datetime(2026, 7, 10), promotion_end_at=datetime(2026, 7, 1))


def test_duplicate_run_blocked(conn):
    _ins_parent(conn, id="A")
    with pytest.raises(IntegrityError):     # same (account, offer, promo, source, run)
        _ins_parent(conn, id="B")


def test_child_store_other_account_blocked(conn):
    _ins_parent(conn, id="OBS1")
    with pytest.raises(IntegrityError):     # s2 belongs to accY2, evidence is accY
        conn.execute(SE.__table__.insert().values(**_child(marketplace_store_id="s2")))


def test_child_mapping_matrix(conn):
    _ins_parent(conn, id="OBS1")
    with pytest.raises(IntegrityError):     # mapped + NULL store
        conn.execute(SE.__table__.insert().values(**_child(mapping_status="mapped", marketplace_store_id=None)))
    with pytest.raises(IntegrityError):     # unmapped + store present
        conn.execute(SE.__table__.insert().values(**_child(mapping_status="unmapped", marketplace_store_id="s1")))
    conn.execute(SE.__table__.insert().values(**_child(mapping_status="unmapped",
                                                       marketplace_store_id=None, external_store_id="c99")))


def test_duplicate_child_blocked(conn):
    _ins_parent(conn, id="OBS1")
    conn.execute(SE.__table__.insert().values(**_child(external_store_id="c1")))
    with pytest.raises(IntegrityError):     # same (obs, external_store_id)
        conn.execute(SE.__table__.insert().values(**_child(external_store_id="c1", marketplace_store_id=None,
                                                           mapping_status="unmapped")))


def test_parent_delete_cascades_child(conn):
    _ins_parent(conn, id="OBS1")
    conn.execute(SE.__table__.insert().values(**_child()))
    conn.execute(PO.__table__.delete().where(PO.__table__.c.id == "OBS1"))
    assert conn.execute(select(func.count()).select_from(SE.__table__)).scalar() == 0


def test_product_delete_cascades_parent(conn):
    _ins_parent(conn, id="OBS1")
    conn.execute(text("DELETE FROM products WHERE id='p1'"))   # product delete → evidence CASCADE
    assert conn.execute(select(func.count()).select_from(PO.__table__)).scalar() == 0


def test_account_delete_cascades_all(conn):
    _ins_parent(conn, id="OBS1")
    conn.execute(SE.__table__.insert().values(**_child()))
    conn.execute(text("DELETE FROM marketplace_accounts WHERE id='accY'"))
    assert conn.execute(select(func.count()).select_from(PO.__table__)).scalar() == 0
    assert conn.execute(select(func.count()).select_from(SE.__table__)).scalar() == 0


def test_enum_lengths_fit():
    mapping = {"resolution_status": RESOLUTION_STATUSES, "participation_status": PARTICIPATION_STATUSES,
               "attribution_status": ATTRIBUTION_STATUSES, "currency_status": CURRENCY_STATUSES}
    for col, vocab in mapping.items():
        assert max(len(v) for v in vocab) <= PO.__table__.c[col].type.length, col
    assert max(len(v) for v in MAPPING_STATUSES) <= SE.__table__.c["mapping_status"].type.length


# ══ WRITER tests (async, driver) ═════════════════════════════════════════════════

async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, business_id="BIZ-1", campaigns=("c1",), offers=(("OF-1",),),
                store_status="active"):
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace="yandex",
                             identity_status="verified", external_account_id=business_id, label="Каб")
    db.add(acc)
    stores = {}
    for cid in campaigns:
        st = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="yandex",
                              store_key=str(uuid.uuid4()), external_store_id=cid, label=f"S {cid}",
                              source="api", status=store_status)
        db.add(st); stores[cid] = st
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="yandex",
                                 status="connected", verification_status="verified", scopes=["feedbacks"],
                                 marketplace_account_id=acc.id, workspace_id=wid)
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("y-token"), verification_status="verified"))
    for (offer_id,) in offers:
        prod = Product(id=str(uuid.uuid4()), user_id=uid, name="N", marketplace="yandex", sku=offer_id,
                       marketplace_account_id=acc.id, external_product_id=f"sku-{offer_id}")
        db.add(prod)
    await db.commit()
    return uid, acc, stores, conn


async def _state(db, conn, store):
    st = ApiSyncState(marketplace_connection_id=conn.id, marketplace_account_id=conn.marketplace_account_id,
                      marketplace_store_id=store.id, data_type="promotions", status="pending")
    st._owner_user_id = conn.user_id
    db.add(st); await db.commit()
    return st


def _off(offer_id, status, price=None, promo=None, mx=None, campaign_ids=None):
    dp = {}
    if price is not None:
        dp["price"] = price
    if promo is not None:
        dp["promoPrice"] = promo
    if mx is not None:
        dp["maxPromoPrice"] = mx
    o = {"offerId": offer_id, "status": status, "params": {"discountParams": dp}}
    if campaign_ids is not None:
        o["autoParticipatingDetails"] = {"campaignIds": campaign_ids}
    return o


def _page(offers, next_token=None):
    return {"result": {"offers": offers, "paging": ({"nextPageToken": next_token} if next_token else {})}}


class _Promo:
    def __init__(self, *, promos=None, offers=None):
        self._promos = promos if promos is not None else [{"id": "PR1", "name": "P",
                       "period": {"dateTimeFrom": "2026-07-01T00:00:00Z", "dateTimeTo": "2026-07-31T00:00:00Z"}}]
        self._offers = offers or {}
        self._calls = {}

    async def list_promos(self, *, token, business_id):
        return self._promos

    async def list_promo_offers(self, *, token, business_id, promo_id, page_token=None, limit=500):
        v = self._offers.get(str(promo_id), _page([]))
        if isinstance(v, list):
            i = self._calls.get(str(promo_id), 0); self._calls[str(promo_id)] = i + 1
            return v[min(i, len(v) - 1)]
        return v


def _use(monkeypatch, stub):
    monkeypatch.setattr(yx, "yandex_client", stub)


def _drain(db, st, monkeypatch, stub, *, max_steps=60):
    _use(monkeypatch, stub)
    for _ in range(max_steps):
        res = _run(yx.fetch_and_persist_page(db, st, "tok"))
        _run(db.commit())
        if res["done"]:
            return res
    raise AssertionError("did not finish")


def _parents(db, **w):
    q = select(PO)
    for k, v in w.items():
        q = q.where(getattr(PO, k) == v)
    return _run(db.execute(q)).scalars().all()


def _children(db):
    return _run(db.execute(select(SE))).scalars().all()


def test_status_auto(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([_off("OF-1", "AUTO", "1500", "1000", "1200")])}))
    p = _parents(db)[0]
    assert p.provider_status == "AUTO" and p.participation_status == "active"
    assert p.auto_participation is True and p.attribution_status == "account_wide"
    assert p.promo_buyer_price == Decimal("1000") and p.pre_promo_price == Decimal("1500")
    assert p.promo_max_price == Decimal("1200") and p.currency == "RUB" and p.currency_status == "proven"
    assert p.resolution_status == "resolved" and p.product_id is not None
    assert _children(db) == []                      # AUTO never fans out to a store
    assert st.coverage_complete is True


def test_status_partially_auto_children(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, campaigns=("c1", "c2")))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([
        _off("OF-1", "PARTIALLY_AUTO", "1500", "1000", campaign_ids=["c1", "c2", "c9"])])}))
    p = _parents(db)[0]
    assert p.attribution_status == "exact_stores" and p.auto_participation is True
    ch = {c.external_store_id: c for c in _children(db)}
    assert set(ch) == {"c1", "c2", "c9"}
    assert ch["c1"].mapping_status == "mapped" and ch["c1"].marketplace_store_id == stores["c1"].id
    assert ch["c2"].mapping_status == "mapped"
    assert ch["c9"].mapping_status == "unmapped" and ch["c9"].marketplace_store_id is None   # unknown campaign


def test_status_manual(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([_off("OF-1", "MANUAL", "1500", "1000")])}))
    p = _parents(db)[0]
    assert p.participation_status == "active" and p.auto_participation is False
    assert p.attribution_status == "unresolved" and _children(db) == []


def test_status_not_participating(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([_off("OF-1", "NOT_PARTICIPATING")])}))
    p = _parents(db)[0]
    assert p.participation_status == "not_participating" and p.auto_participation is None
    assert p.pre_promo_price is None and p.promo_buyer_price is None and p.currency is None
    assert _children(db) == []


def test_status_renewed_renew_failed_minimum(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db,
                offers=(("OF-1",), ("OF-2",), ("OF-3",))))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([
        _off("OF-1", "RENEWED", "1", "1"),
        _off("OF-2", "RENEW_FAILED"),
        _off("OF-3", "MINIMUM_FOR_PROMOS", "1", "1")])}))
    by = {p.external_product_id: p for p in _parents(db)}
    assert by["OF-1"].participation_status == "active" and by["OF-1"].auto_participation is True
    assert by["OF-2"].participation_status == "unknown" and by["OF-2"].promo_buyer_price is None
    assert by["OF-3"].participation_status == "active" and by["OF-3"].auto_participation is False
    assert st.coverage_complete is False            # RENEW_FAILED normalizes to unknown → not complete


def test_status_unknown_verbatim_coverage_false(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([_off("OF-1", "BRAND_NEW_STATUS")])}))
    p = _parents(db)[0]
    assert p.provider_status == "BRAND_NEW_STATUS" and p.participation_status == "unknown"
    assert p.auto_participation is None and p.attribution_status == "unresolved"
    assert st.coverage_complete is False


def test_partially_auto_without_campaignids_skipped(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, offers=(("OF-1",), ("OF-2",))))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([
        _off("OF-1", "PARTIALLY_AUTO", campaign_ids=[]),        # contract violation → skipped
        _off("OF-2", "AUTO", "1", "1")])}))
    exts = {p.external_product_id for p in _parents(db)}
    assert exts == {"OF-2"}                          # OF-1 not written
    assert st.skipped_rows_count == 1 and st.coverage_complete is False
    assert _children(db) == []


def test_duplicate_campaignid_one_child(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([
        _off("OF-1", "PARTIALLY_AUTO", "1", "1", campaign_ids=["c1", "c1"])])}))
    assert len(_children(db)) == 1


def test_archived_store_is_unmapped(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, campaigns=("c1",), store_status="archived"))
    # add a second active store so the state has a live campaign to sync through
    active = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="yandex",
                              store_key=str(uuid.uuid4()), external_store_id="cA", label="A",
                              source="api", status="active")
    db.add(active); _run(db.commit())
    st = _run(_state(db, conn, active))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([
        _off("OF-1", "PARTIALLY_AUTO", "1", "1", campaign_ids=["c1"])])}))
    ch = _children(db)[0]
    assert ch.external_store_id == "c1" and ch.mapping_status == "unmapped" and ch.marketplace_store_id is None


def test_second_child_error_full_rollback(monkeypatch):
    # PULT-LAUNCH-2.5E-1: the parent + all children are written atomically in change_only.observe_
    # promotion. Force the SECOND child construction to blow up and prove the parent (already flushed)
    # and the first child are rolled back with it — never a parent without its full child set.
    from services.marketplace.ingest import change_only as co
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, campaigns=("c1", "c2")))
    st = _run(_state(db, conn, stores["c1"]))
    calls = {"n": 0}
    real_cls = co.MarketplacePromotionStoreEvidence

    def _boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ExecutionError(ExecutionError.MARKETPLACE_5XX, "child boom")
        return real_cls(*a, **k)
    monkeypatch.setattr(co, "MarketplacePromotionStoreEvidence", _boom)
    _use(monkeypatch, _Promo(offers={"PR1": _page([
        _off("OF-1", "PARTIALLY_AUTO", "1", "1", campaign_ids=["c1", "c2"])])}))
    _run(yx.fetch_and_persist_page(db, st, "tok"))   # LIST
    with pytest.raises(ExecutionError):
        _run(yx.fetch_and_persist_page(db, st, "tok"))   # NOMS: 2nd child boom
    _run(db.rollback())
    assert _parents(db) == [] and _children(db) == []    # parent + all children rolled back


def test_repeat_run_no_duplicate(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    page = _Promo(offers={"PR1": [_page([_off("OF-1", "PARTIALLY_AUTO", "1", "1", campaign_ids=["c1"])], next_token="T2"),
                                  _page([])]})
    _use(monkeypatch, page)
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # LIST
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # NOMS page1 → parent+child, token T2
    assert len(_parents(db)) == 1 and len(_children(db)) == 1
    # rewind to page1 within the SAME run → idempotent upsert, no dupes
    cur = json.loads(st.cursor); cur["ptok"] = None; cur["pidx"] = 0; cur["wpx"] = -1
    st.cursor = json.dumps(cur); _run(db.commit())
    page._calls = {}
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())
    assert len(_parents(db)) == 1 and len(_children(db)) == 1


def test_change_only_unchanged_run_dedups(monkeypatch):
    # PULT-LAUNCH-2.5E-1: an identical second pass writes no new parent (change-only wiring).
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([_off("OF-1", "AUTO", "1", "1")])}))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([_off("OF-1", "AUTO", "1", "1")])}))
    assert len(_parents(db)) == 1


def test_new_run_appends_version(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([_off("OF-1", "AUTO", "1", "1")])}))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([_off("OF-1", "AUTO", "2", "2")])}))
    rows = _parents(db)
    assert len(rows) == 2 and len({r.ingest_run_id for r in rows}) == 2


def test_full_pagination(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, offers=(("OF-1",), ("OF-2",))))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": [
        _page([_off("OF-1", "AUTO", "1", "1")], next_token="T2"),
        _page([_off("OF-2", "AUTO", "2", "2")])]}))
    assert {p.external_product_id for p in _parents(db)} == {"OF-1", "OF-2"}
    assert st.coverage_complete is True


def test_empty_offers_completes(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    res = _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([])}))
    assert res["done"] is True and _parents(db) == [] and st.coverage_complete is True


def test_no_promos_completes(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    res = _drain(db, st, monkeypatch, _Promo(promos=[]))
    assert res["done"] is True and _parents(db) == [] and st.coverage_complete is True


@pytest.mark.parametrize("payload", [
    {}, {"result": {}}, {"result": {"offers": "x"}},
    {"result": {"offers": [], "paging": "y"}}, {"result": {"offers": [], "paging": {"nextPageToken": 5}}},
])
def test_broken_offers_page_fails_closed(monkeypatch, payload):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))

    class _Bad(_Promo):
        async def list_promo_offers(self, *a, **k):
            return payload
    _use(monkeypatch, _Bad())
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # LIST ok
    with pytest.raises(ExecutionError):
        _run(yx.fetch_and_persist_page(db, st, "tok"))
    assert st.coverage_complete is False and _parents(db) == []


def test_token_cycle_a_b_a(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    _use(monkeypatch, _Promo(offers={"PR1": [
        _page([_off("OF-1", "AUTO", "1", "1")], next_token="T1"),
        _page([_off("OF-1", "AUTO", "1", "1")], next_token="T2"),
        _page([_off("OF-1", "AUTO", "1", "1")], next_token="T1")]}))
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # LIST
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # None->T1
    _run(yx.fetch_and_persist_page(db, st, "tok")); _run(db.commit())   # T1->T2
    with pytest.raises(ExecutionError):
        _run(yx.fetch_and_persist_page(db, st, "tok"))                   # T2->T1 cycle


def test_skipped_identity_row(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([
        {"status": "AUTO"},                    # no offerId
        _off("OF-1", "AUTO", "1", "1")])}))
    assert {p.external_product_id for p in _parents(db)} == {"OF-1"}
    assert st.skipped_rows_count == 1 and st.coverage_complete is False


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
    assert _run(db.execute(select(func.count()).select_from(PO))).scalar_one() == 0


def test_only_read_methods_and_no_projection(monkeypatch):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db))

    class _Guard:
        async def list_promos(self, *, token, business_id):
            return [{"id": "PR1", "period": {}}]

        async def list_promo_offers(self, *, token, business_id, promo_id, page_token=None, limit=500):
            return _page([_off("OF-1", "PARTIALLY_AUTO", "1", "1", campaign_ids=["c1"])])

        def __getattr__(self, name):
            raise AssertionError(f"forbidden Yandex call: {name}")
    st = _run(_state(db, conn, stores["c1"]))
    _drain(db, st, monkeypatch, _Guard())
    assert len(_parents(db)) == 1
    # NO projection into MarketplacePriceObservation
    assert _run(db.execute(select(func.count()).select_from(MPO))).scalar_one() == 0


def test_finance_unsupported_and_stop_auto_contained():
    assert "finance" in yx.UNSUPPORTED and "promotions" not in yx.UNSUPPORTED
    from services.marketplace import executor
    assert "stop_auto_promotion" in executor._CONTAINED_ACTIONS


def test_secrets_and_ids_not_logged(monkeypatch, caplog):
    db = _run(_new_db()); uid, acc, stores, conn = _run(_seed(db, business_id="BIZSECRET",
                                                              campaigns=("9988",), offers=(("OFF7",),)))
    st = _run(_state(db, conn, stores["9988"]))
    with caplog.at_level("DEBUG"):
        _drain(db, st, monkeypatch, _Promo(offers={"PR1": _page([
            _off("OFF7", "PARTIALLY_AUTO", "1", "1", campaign_ids=["9988"])])}))
    blob = "\n".join(r.getMessage() for r in caplog.records
                     if not r.name.startswith(("sqlalchemy", "aiosqlite")))
    for secret in ("y-token", "BIZSECRET", "9988", "OFF7"):
        assert secret not in blob


def test_source_guard_new_model_only_in_ingest():
    import glob
    here = os.path.dirname(__file__)
    backend = os.path.dirname(here)
    refs = []
    for path in glob.glob(os.path.join(backend, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, backend).replace("\\", "/")
        if rel.startswith(("models/", "tests/")) or "alembic/versions/" in rel:
            continue
        src = open(path, encoding="utf-8").read()
        if "MarketplacePromotionObservation" in src or "marketplace_promotion_observations" in src:
            refs.append(rel)
    # PULT-LAUNCH-2.5E-1 — the promotion tables are now written from exactly ONE module, the shared
    # change-only writer; yandex.py assembles the evidence and delegates the write to it.
    assert sorted(refs) == ["services/marketplace/ingest/change_only.py"], refs


# ══ MIGRATION ypo1a2b3c4d01 ══════════════════════════════════════════════════════

def _cfg(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "ypo_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    import db_migrations as dbm
    return dbm._alembic_config(), f"sqlite:///{tmp}"


def _tables(sync_url):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return set(sa.inspect(c).get_table_names())
    finally:
        eng.dispose()


def test_single_head():
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == [HEAD]


def test_migration_upgrade_downgrade_reupgrade(monkeypatch):
    cfg, sync_url = _cfg(monkeypatch)
    command.upgrade(cfg, HEAD)
    t = _tables(sync_url)
    assert "marketplace_promotion_observations" in t and "marketplace_promotion_store_evidence" in t
    command.downgrade(cfg, PRIOR)
    t = _tables(sync_url)
    assert "marketplace_promotion_observations" not in t and "marketplace_promotion_store_evidence" not in t
    command.upgrade(cfg, HEAD)                                # re-upgrade must succeed
    assert "marketplace_promotion_store_evidence" in _tables(sync_url)
