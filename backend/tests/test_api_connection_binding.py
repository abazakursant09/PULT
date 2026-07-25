"""PULT-LAUNCH-1.4.5D — binding an API key to a chosen cabinet, honestly.

The seller creates a cabinet on the Stores screen (often CSV-only) and later connects an API key to
THAT cabinet. The rules under test:

  * the key attaches to the chosen Account and mints no second one;
  * a store is never created or duplicated by connecting a key;
  * saving a key is not connecting — "API подключён" only after a real verify succeeds;
  * the same external cabinet cannot be bound twice (Ozon Client-Id, Yandex businessId), and the
    same WB token cannot be reused across cabinets — WB has no seller id, so a keyed fingerprint is
    its only guard, and the fingerprint never leaves the server;
  * disconnect deletes the credentials and keeps the cabinet, its stores and its data.

Router functions are called directly against an in-memory SQLite whose real UNIQUE constraints do
the enforcing. `verify` is exercised by stubbing the runner and the Yandex resolver, so no network
is touched — a real positive verify is a separate live smoke.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401 — register tables
from models.api_credential import ApiCredential
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.user import User
from models.workspace import Workspace
from schemas.marketplace import ConnectionCreate, VerifyRequest
import routers.connections as conn_mod
from routers.connections import create_connection, delete_connection, verify_connection_scope
from routers.marketplace_accounts import create_account, list_accounts
from schemas.marketplace_store import AccountCreate

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _user(db):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=str(uuid.uuid4()), owner_user_id=uid))
    await db.commit()
    return await db.get(User, uid)


async def _cabinet(db, user, marketplace="wildberries", label="Кабинет"):
    """A CSV-only cabinet, created the keyless way the seller would."""
    return await create_account(AccountCreate(marketplace=marketplace, label=label), db=db, user=user)


def _body(mp, *, account_id=None, token="key-1", client_id=None, scope="feedbacks"):
    return ConnectionCreate(marketplace=mp, token=token, scope=scope,
                            ozon_client_id=client_id, marketplace_account_id=account_id)


# ── a stub verify: no network, controllable outcome ────────────────────────────
class _Result:
    def __init__(self, outcome):
        self.outcome = type("O", (), {"value": outcome})()
        self.retry_after_seconds = None


def _stub_verify(monkeypatch, *, outcome="verified", business_id="ym-biz-1"):
    async def fake_verify(db, *, user_id, connection_id, scope):
        conn = (await db.execute(
            select(MarketplaceConnection).where(MarketplaceConnection.id == connection_id)
        )).scalars().first()
        cred = (await db.execute(
            select(ApiCredential).where(ApiCredential.connection_id == connection_id,
                                        ApiCredential.scope == scope)
        )).scalars().first()
        if cred is not None:
            cred.verification_status = outcome
            conn.verification_status = outcome
            await db.commit()
        return conn, cred, _Result(outcome)

    async def fake_business_id(*, token):
        return business_id

    monkeypatch.setattr(conn_mod.verification_runner, "verify_credential", fake_verify)
    monkeypatch.setattr(conn_mod.yandex_client, "resolve_business_id", fake_business_id)


async def _stores(db, account_id):
    return list((await db.execute(
        select(MarketplaceStore).where(MarketplaceStore.marketplace_account_id == account_id)
    )).scalars().all())


async def _creds(db, connection_id):
    return list((await db.execute(
        select(ApiCredential).where(ApiCredential.connection_id == connection_id)
    )).scalars().all())


async def _conn_row(db, account_id):
    return (await db.execute(
        select(MarketplaceConnection).where(MarketplaceConnection.marketplace_account_id == account_id)
    )).scalars().first()


# 1-3. account_id binds to the existing cabinet, no second Account, no new/duplicate Store
def test_binds_to_existing_cabinet_without_duplicating():
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user))                       # WB CSV-cabinet: has one primary store
    stores_before = _run(_stores(db, cab.id))
    accounts_before = len((_run(db.execute(select(MarketplaceAccount)))).scalars().all())

    _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))

    assert _run(_conn_row(db, cab.id)).marketplace_account_id == cab.id
    accounts_after = len((_run(db.execute(select(MarketplaceAccount)))).scalars().all())
    assert accounts_after == accounts_before          # no second Account minted
    assert [s.id for s in _run(_stores(db, cab.id))] == [s.id for s in stores_before]  # store untouched


# 4. foreign / missing cabinet → the SAME 404
def test_foreign_and_missing_cabinet_same_404():
    db = _run(_new_db())
    owner = _run(_user(db)); stranger = _run(_user(db))
    cab = _run(_cabinet(db, owner))
    with pytest.raises(HTTPException) as foreign:
        _run(create_connection(_body("wildberries", account_id=cab.id), current_user=stranger, db=db))
    with pytest.raises(HTTPException) as missing:
        _run(create_connection(_body("wildberries", account_id=str(uuid.uuid4())), current_user=stranger, db=db))
    assert foreign.value.status_code == missing.value.status_code == 404
    assert foreign.value.detail == missing.value.detail


# 5. marketplace mismatch → 422
def test_marketplace_mismatch_422():
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user, marketplace="ozon"))
    with pytest.raises(HTTPException) as e:
        _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))
    assert e.value.status_code == 422


# 6. a second connection SEQUENTIALLY reuses the one row (one connection per cabinet, by design)
def test_second_connect_same_cabinet_reuses_not_duplicates():
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user, marketplace="yandex"))
    a = _run(create_connection(_body("yandex", account_id=cab.id), current_user=user, db=db))
    b = _run(create_connection(_body("yandex", account_id=cab.id, token="key-2"),
                               current_user=user, db=db))
    assert a.id == b.id
    conns = (_run(db.execute(select(MarketplaceConnection)
                             .where(MarketplaceConnection.marketplace_account_id == cab.id)))).scalars().all()
    assert len(conns) == 1


# 7. the DB itself refuses a SECOND connection row on one cabinet — the net behind the race 409
def test_uq_mp_conn_account_blocks_a_second_row():
    from sqlalchemy.exc import IntegrityError
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user, marketplace="yandex"))
    _run(create_connection(_body("yandex", account_id=cab.id), current_user=user, db=db))
    # what a lost race would attempt: a second row for the same account. uq_mp_conn_account rejects
    # it, which is exactly what the route turns into a safe 409.
    db.add(MarketplaceConnection(id=str(uuid.uuid4()), user_id=str(user.id),
                                 marketplace="yandex", status="connected", scopes=[],
                                 marketplace_account_id=cab.id))
    with pytest.raises(IntegrityError):
        _run(db.flush())
    _run(db.rollback())


# 7. reconnect with the SAME key reuses the row (not a 409, not a second connection)
def test_reconnect_same_key_reuses_row():
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user))
    c1 = _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))
    c2 = _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))
    assert c1.id == c2.id  # same connection id returned
    conns = (_run(db.execute(select(MarketplaceConnection)
                             .where(MarketplaceConnection.marketplace_account_id == cab.id)))).scalars().all()
    assert len(conns) == 1


# 8-11. has_connection tracks verified, not existence
def _has_connection(db, user, account_id):
    accounts = _run(list_accounts(include_stores=True, db=db, user=user))
    return next(a.has_connection for a in accounts if a.id == account_id)


def test_has_connection_only_after_successful_verify(monkeypatch):
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user))
    conn = _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))

    assert _has_connection(db, user, cab.id) is False   # saved, not verified

    _stub_verify(monkeypatch, outcome="verified")
    _run(verify_connection_scope(conn.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    assert _has_connection(db, user, cab.id) is True


def test_has_connection_false_on_failed_verify(monkeypatch):
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user))
    conn = _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))
    _stub_verify(monkeypatch, outcome="revoked")
    _run(verify_connection_scope(conn.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    assert _has_connection(db, user, cab.id) is False


def test_has_connection_false_when_revoked(monkeypatch):
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user))
    conn = _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))
    _stub_verify(monkeypatch, outcome="verified")
    _run(verify_connection_scope(conn.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    assert _has_connection(db, user, cab.id) is True
    _run(delete_connection(conn.id, current_user=user, db=db))
    assert _has_connection(db, user, cab.id) is False


# 12-13. Ozon external id captured only on verify; a repeat → safe 409
def test_ozon_client_id_captured_on_verify(monkeypatch):
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user, marketplace="ozon"))
    conn = _run(create_connection(_body("ozon", account_id=cab.id, client_id="ozon-123"),
                                  current_user=user, db=db))
    # not written at save time
    acc = _run(db.get(MarketplaceAccount, cab.id))
    assert acc.external_account_id is None

    _stub_verify(monkeypatch, outcome="verified")
    _run(verify_connection_scope(conn.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    acc = (_run(db.execute(select(MarketplaceAccount).where(MarketplaceAccount.id == cab.id)))).scalars().first()
    assert acc.external_account_id == "ozon-123"


def test_ozon_duplicate_external_id_conflict(monkeypatch):
    db = _run(_new_db()); user = _run(_user(db))
    cab_a = _run(_cabinet(db, user, marketplace="ozon", label="A"))
    cab_b = _run(_cabinet(db, user, marketplace="ozon", label="B"))
    _stub_verify(monkeypatch, outcome="verified")
    a = _run(create_connection(_body("ozon", account_id=cab_a.id, client_id="dup"), current_user=user, db=db))
    _run(verify_connection_scope(a.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    b = _run(create_connection(_body("ozon", account_id=cab_b.id, client_id="dup", token="key-2"),
                               current_user=user, db=db))
    with pytest.raises(HTTPException) as e:
        _run(verify_connection_scope(b.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    assert e.value.status_code == 409


# 14-15. Yandex businessId captured on verify; a repeat → safe 409
def test_yandex_business_id_captured_on_verify(monkeypatch):
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user, marketplace="yandex"))
    conn = _run(create_connection(_body("yandex", account_id=cab.id), current_user=user, db=db))
    assert (_run(db.get(MarketplaceAccount, cab.id))).external_account_id is None
    _stub_verify(monkeypatch, outcome="verified", business_id="ym-777")
    _run(verify_connection_scope(conn.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    acc = (_run(db.execute(select(MarketplaceAccount).where(MarketplaceAccount.id == cab.id)))).scalars().first()
    assert acc.external_account_id == "ym-777"


def test_yandex_duplicate_business_id_conflict(monkeypatch):
    db = _run(_new_db()); user = _run(_user(db))
    cab_a = _run(_cabinet(db, user, marketplace="yandex", label="A"))
    cab_b = _run(_cabinet(db, user, marketplace="yandex", label="B"))
    _stub_verify(monkeypatch, outcome="verified", business_id="same-biz")
    a = _run(create_connection(_body("yandex", account_id=cab_a.id), current_user=user, db=db))
    _run(verify_connection_scope(a.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    b = _run(create_connection(_body("yandex", account_id=cab_b.id, token="key-2"),
                               current_user=user, db=db))
    with pytest.raises(HTTPException) as e:
        _run(verify_connection_scope(b.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    assert e.value.status_code == 409


# 16. WB never writes an external_account_id (no INN, no made-up id)
def test_wb_never_sets_external_account_id(monkeypatch):
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user, marketplace="wildberries"))
    conn = _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))
    _stub_verify(monkeypatch, outcome="verified")
    _run(verify_connection_scope(conn.id, VerifyRequest(scope="feedbacks"), current_user=user, db=db))
    acc = (_run(db.execute(select(MarketplaceAccount).where(MarketplaceAccount.id == cab.id)))).scalars().first()
    assert acc.external_account_id is None


# 17. the same WB token on a second cabinet → 409 via the fingerprint
def test_wb_same_token_two_cabinets_conflict():
    db = _run(_new_db()); user = _run(_user(db))
    cab_a = _run(_cabinet(db, user, marketplace="wildberries", label="A"))
    cab_b = _run(_cabinet(db, user, marketplace="wildberries", label="B"))
    _run(create_connection(_body("wildberries", account_id=cab_a.id, token="same-wb-token"),
                           current_user=user, db=db))
    with pytest.raises(HTTPException) as e:
        _run(create_connection(_body("wildberries", account_id=cab_b.id, token="same-wb-token"),
                               current_user=user, db=db))
    assert e.value.status_code == 409


# 18. the fingerprint is never returned by the API and is HMAC (not a bare hash of the token)
def test_fingerprint_never_exposed_and_is_hmac():
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user, marketplace="wildberries"))
    out = _run(create_connection(_body("wildberries", account_id=cab.id, token="tok"),
                                 current_user=user, db=db))
    assert not hasattr(out, "credential_fingerprint")
    assert "credential_fingerprint" not in out.model_dump()
    # stored, but keyed — not a plain sha256 of the token, and not the token
    import hashlib
    conn = (_run(db.execute(select(MarketplaceConnection)
                            .where(MarketplaceConnection.marketplace_account_id == cab.id)))).scalars().first()
    assert conn.credential_fingerprint
    assert conn.credential_fingerprint != hashlib.sha256(b"tok").hexdigest()
    assert conn.credential_fingerprint != "tok"


# 19-20. disconnect removes credentials, keeps the cabinet and its data
def test_disconnect_removes_credentials_keeps_data():
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user, marketplace="wildberries"))
    conn = _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))
    # a product + placement + store already exist for this cabinet
    store = _run(_stores(db, cab.id))[0]
    p = Product(id=str(uuid.uuid4()), user_id=str(user.id), name="Товар",
                marketplace="wildberries", sku="SKU-1", marketplace_account_id=cab.id)
    db.add(p)
    db.add(ProductPlacement(id=str(uuid.uuid4()), product_id=p.id, marketplace_store_id=store.id,
                            marketplace_account_id=cab.id, status="active", source="csv"))
    _run(db.commit())

    assert len(_run(_creds(db, conn.id))) == 1
    _run(delete_connection(conn.id, current_user=user, db=db))

    assert _run(_creds(db, conn.id)) == []                                   # credentials gone
    assert _run(db.get(MarketplaceAccount, cab.id)) is not None              # account kept
    assert len(_run(_stores(db, cab.id))) == 1                               # store kept
    assert _run(db.get(Product, p.id)) is not None                          # product kept
    conn_row = _run(db.get(MarketplaceConnection, conn.id))
    assert conn_row.status == "revoked" and conn_row.credential_fingerprint is None


# 21. reconnect after disconnect reuses the same Account and Store
def test_reconnect_reuses_same_account():
    db = _run(_new_db()); user = _run(_user(db))
    cab = _run(_cabinet(db, user, marketplace="wildberries"))
    conn = _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))
    _run(delete_connection(conn.id, current_user=user, db=db))
    again = _run(create_connection(_body("wildberries", account_id=cab.id), current_user=user, db=db))
    assert again.id == conn.id
    assert _run(_conn_row(db, cab.id)).marketplace_account_id == cab.id
    assert len(_run(_stores(db, cab.id))) == 1


# 22. the legacy path (no account_id) still works and still mints its own account
def test_legacy_create_without_account_id_still_works():
    db = _run(_new_db()); user = _run(_user(db))
    out = _run(create_connection(_body("wildberries"), current_user=user, db=db))
    row = (_run(db.execute(select(MarketplaceConnection)
                           .where(MarketplaceConnection.id == out.id)))).scalars().first()
    assert row.marketplace_account_id is not None
