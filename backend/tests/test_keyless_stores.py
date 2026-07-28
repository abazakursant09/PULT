"""PULT-LAUNCH-1.4.1 — keyless cabinets & stores.

Calls the router functions directly (the established connection-test style) against an
in-memory SQLite whose CHECK/UNIQUE constraints from PULT-LAUNCH-1.3 do the enforcing.
No credentials, no CSV, no API.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401 — register tables
from models.user import User
from models.workspace import Workspace
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
import routers.marketplace_accounts as mod
from routers.marketplace_accounts import (
    create_account, create_store, list_accounts, set_store_status,
)
from schemas.marketplace_store import AccountCreate, StoreCreate, StoreStatusUpdate

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


async def _stores(db, account_id):
    return list((await db.execute(
        select(MarketplaceStore).where(MarketplaceStore.marketplace_account_id == account_id)
    )).scalars().all())


# 1-2. WB / Ozon create exactly one primary store
@pytest.mark.parametrize("mp", ["wildberries", "ozon"])
def test_single_store_marketplace_creates_one_primary(mp):
    db = _run(_new_db()); user = _run(_user(db))
    out = _run(create_account(AccountCreate(marketplace=mp, label="Кабинет"), db=db, user=user))
    stores = _run(_stores(db, out.id))
    assert len(stores) == 1
    assert stores[0].store_key == "primary"
    assert stores[0].status == "active" and stores[0].source == "manual"
    assert stores[0].external_store_id is None


# 3. Yandex creates no store automatically
def test_yandex_creates_no_store():
    db = _run(_new_db()); user = _run(_user(db))
    out = _run(create_account(AccountCreate(marketplace="yandex", label="Я"), db=db, user=user))
    assert _run(_stores(db, out.id)) == []


# 4. Yandex allows several stores
def test_yandex_multiple_stores():
    db = _run(_new_db()); user = _run(_user(db))
    acc = _run(create_account(AccountCreate(marketplace="yandex", label="Я"), db=db, user=user))
    _run(create_store(acc.id, StoreCreate(label="FBS"), db=db, user=user))
    _run(create_store(acc.id, StoreCreate(label="DBS"), db=db, user=user))
    stores = _run(_stores(db, acc.id))
    assert len(stores) == 2
    assert len({s.store_key for s in stores}) == 2 and "primary" not in {s.store_key for s in stores}


# 5. Second WB/Ozon store -> 409
@pytest.mark.parametrize("mp", ["wildberries", "ozon"])
def test_second_single_store_conflicts(mp):
    db = _run(_new_db()); user = _run(_user(db))
    acc = _run(create_account(AccountCreate(marketplace=mp, label="K"), db=db, user=user))
    with pytest.raises(HTTPException) as e:
        _run(create_store(acc.id, StoreCreate(label="second"), db=db, user=user))
    assert e.value.status_code == 409


# 6-7. Two distinct WB / Ozon accounts in one workspace
@pytest.mark.parametrize("mp", ["wildberries", "ozon"])
def test_two_accounts_same_marketplace(mp):
    db = _run(_new_db()); user = _run(_user(db))
    a = _run(create_account(AccountCreate(marketplace=mp, label="A"), db=db, user=user))
    b = _run(create_account(AccountCreate(marketplace=mp, label="B"), db=db, user=user))
    assert a.id != b.id
    assert len(_run(_stores(db, a.id))) == 1 and len(_run(_stores(db, b.id))) == 1


# 8-9. No reuse by label or marketplace
def test_no_reuse_by_label_or_marketplace():
    db = _run(_new_db()); user = _run(_user(db))
    a = _run(create_account(AccountCreate(marketplace="yandex", label="Same"), db=db, user=user))
    b = _run(create_account(AccountCreate(marketplace="yandex", label="Same"), db=db, user=user))
    assert a.id != b.id
    accs = _run(db.execute(select(MarketplaceAccount))).scalars().all()
    assert len(accs) == 2


# 10. Foreign account is invisible
def test_foreign_account_not_accessible():
    db = _run(_new_db()); owner = _run(_user(db)); intruder = _run(_user(db))
    acc = _run(create_account(AccountCreate(marketplace="yandex", label="Y"), db=db, user=owner))
    with pytest.raises(HTTPException) as e:
        _run(create_store(acc.id, StoreCreate(label="x"), db=db, user=intruder))
    assert e.value.status_code == 404


# 11. Foreign store is invisible
def test_foreign_store_not_accessible():
    db = _run(_new_db()); owner = _run(_user(db)); intruder = _run(_user(db))
    acc = _run(create_account(AccountCreate(marketplace="wildberries", label="W"), db=db, user=owner))
    store = _run(_stores(db, acc.id))[0]
    with pytest.raises(HTTPException) as e:
        _run(set_store_status(store.id, StoreStatusUpdate(status="archived"), db=db, user=intruder))
    assert e.value.status_code == 404


# 12. Empty label rejected
@pytest.mark.parametrize("label", ["", "   "])
def test_empty_label_rejected(label):
    db = _run(_new_db()); user = _run(_user(db))
    with pytest.raises(HTTPException) as e:
        _run(create_account(AccountCreate(marketplace="ozon", label=label), db=db, user=user))
    assert e.value.status_code == 422


# 13. Short codes wb / ym rejected
@pytest.mark.parametrize("mp", ["wb", "ym"])
def test_short_codes_rejected(mp):
    db = _run(_new_db()); user = _run(_user(db))
    with pytest.raises(HTTPException) as e:
        _run(create_account(AccountCreate(marketplace=mp, label="K"), db=db, user=user))
    assert e.value.status_code == 422


# 14. Unknown marketplace rejected
def test_unknown_marketplace_rejected():
    db = _run(_new_db()); user = _run(_user(db))
    with pytest.raises(HTTPException) as e:
        _run(create_account(AccountCreate(marketplace="megamarket", label="K"), db=db, user=user))
    assert e.value.status_code == 422


# 15-16. Archive keeps the store; restore returns it to active; idempotent
def test_archive_and_restore_keep_store():
    db = _run(_new_db()); user = _run(_user(db))
    acc = _run(create_account(AccountCreate(marketplace="wildberries", label="W"), db=db, user=user))
    store = _run(_stores(db, acc.id))[0]

    r = _run(set_store_status(store.id, StoreStatusUpdate(status="archived"), db=db, user=user))
    assert r.status == "archived"
    assert len(_run(_stores(db, acc.id))) == 1                      # not deleted
    # idempotent
    r2 = _run(set_store_status(store.id, StoreStatusUpdate(status="archived"), db=db, user=user))
    assert r2.status == "archived"
    r3 = _run(set_store_status(store.id, StoreStatusUpdate(status="active"), db=db, user=user))
    assert r3.status == "active"
    # store_key / external_store_id untouched
    fresh = _run(_stores(db, acc.id))[0]
    assert fresh.store_key == "primary" and fresh.external_store_id is None


# 17. A primary-store failure rolls back the whole account
def test_primary_store_failure_rolls_back_account(monkeypatch):
    db = _run(_new_db()); user = _run(_user(db))

    def _bad_store(account, label):
        # empty store_key violates ck_store_key_clean -> IntegrityError at commit
        return MarketplaceStore(
            id=str(uuid.uuid4()), marketplace_account_id=account.id,
            marketplace=account.marketplace, store_key="", label=label,
            source="manual", status="active",
        )
    monkeypatch.setattr(mod, "_new_store", _bad_store)

    with pytest.raises(HTTPException) as e:
        _run(create_account(AccountCreate(marketplace="wildberries", label="W"), db=db, user=user))
    assert e.value.status_code == 409
    assert _run(db.execute(select(MarketplaceAccount))).scalars().first() is None   # no empty cabinet


# 18. Responses carry no credentials/secrets
def test_response_has_no_credentials():
    db = _run(_new_db()); user = _run(_user(db))
    acc = _run(create_account(AccountCreate(marketplace="wildberries", label="W"), db=db, user=user))
    listed = _run(list_accounts(include_stores=True, db=db, user=user))
    keys = set(acc.model_dump().keys()) | set(listed[0].model_dump().keys())
    for banned in ("token", "secret", "secret_enc", "credential", "api_key", "password"):
        assert not any(banned in k for k in keys)
    assert listed[0].stores and set(listed[0].stores[0].model_dump().keys()).isdisjoint(
        {"token", "secret_enc", "credential"})


# 19. DB constraints stay active (second primary store rejected at the DB, surfaced as 409)
def test_db_constraints_active():
    db = _run(_new_db()); user = _run(_user(db))
    acc = _run(create_account(AccountCreate(marketplace="ozon", label="O"), db=db, user=user))
    # raw second primary insert must raise IntegrityError (UNIQUE(account, store_key))
    async def _raw():
        db.add(MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id,
                                marketplace="ozon", store_key="primary", label="dup",
                                source="manual", status="active"))
        await db.commit()
    with pytest.raises(IntegrityError):
        _run(_raw())
    _run(db.rollback())


# 20. Alembic head unchanged
def test_alembic_head_unchanged():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["ozp1a2b3c4d01"]


# has_connection flag + list shape
def test_list_accounts_shape_and_no_cross_workspace():
    db = _run(_new_db()); user = _run(_user(db)); other = _run(_user(db))
    _run(create_account(AccountCreate(marketplace="wildberries", label="Mine"), db=db, user=user))
    _run(create_account(AccountCreate(marketplace="yandex", label="Theirs"), db=db, user=other))
    mine = _run(list_accounts(include_stores=True, db=db, user=user))
    assert len(mine) == 1 and mine[0].label == "Mine"
    assert mine[0].has_connection is False
    assert mine[0].stores is not None and len(mine[0].stores) == 1
