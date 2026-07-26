"""PULT-LAUNCH-1.4.5G — Yandex campaign → store mapping (backend endpoints).

A Yandex cabinet (businessId) holds many campaign stores (campaignId). The seller maps each campaign
to a MarketplaceStore explicitly — never by name, never silently. Router functions are called
directly against in-memory SQLite whose real UNIQUE constraints do the enforcing; `list_campaigns`
is stubbed, so no network is touched.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.api_credential import ApiCredential
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_store import MarketplaceStore
from models.user import User
from models.workspace import Workspace
from schemas.marketplace import CampaignLinkRequest
from services.marketplace import credential_vault
import routers.connections as conn_mod
from routers.connections import list_connection_campaigns, link_connection_campaign

_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, business_id="BIZ-1", verified=True, with_store=True, store_campaign=None):
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace="yandex",
                             identity_status="verified" if verified else "unverified",
                             external_account_id=business_id if verified else None, label="Каб")
    db.add(acc)
    store = None
    if with_store:
        store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id,
                                 marketplace="yandex", store_key=str(uuid.uuid4()),
                                 external_store_id=store_campaign, label="Магазин",
                                 source="manual", status="active")
        db.add(store)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=uid, marketplace="yandex", status="connected",
        verification_status="verified" if verified else "unverified", scopes=["feedbacks"],
        marketplace_account_id=acc.id, workspace_id=wid)
    db.add(conn)
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("y-token"),
                         verification_status="verified"))
    await db.commit()
    return await db.get(User, uid), acc, store, conn


def _stub_campaigns(monkeypatch, campaigns):
    async def _list(*, token):
        return campaigns
    monkeypatch.setattr(conn_mod.yandex_client, "list_campaigns", _list)


_C1 = {"campaign_id": "111", "business_id": "BIZ-1", "label": "Основной", "placement_type": "FBS"}
_C2 = {"campaign_id": "222", "business_id": "BIZ-1", "label": "Экспресс", "placement_type": "Express"}


# 1. list_campaigns surfaces business/campaign ids
def test_list_campaigns_returns_ids(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db))
    _stub_campaigns(monkeypatch, [_C1, _C2])
    out = _run(list_connection_campaigns(conn.id, current_user=user, db=db))
    got = {c.campaign_id: c for c in out}
    assert set(got) == {"111", "222"}
    assert got["111"].business_id == "BIZ-1" and got["111"].link_state == "unlinked"


# 2. existing store linked explicitly
def test_link_existing_store(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db))
    _stub_campaigns(monkeypatch, [_C1])
    out = _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="111", store_id=store.id),
                                        current_user=user, db=db))
    assert out.linked_store_id == store.id and out.created_store is False
    assert _run(db.get(MarketplaceStore, store.id)).external_store_id == "111"


# 3. new store created only on explicit action
def test_link_creates_new_store(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db, with_store=False))
    _stub_campaigns(monkeypatch, [_C1])
    out = _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="111", new_store_label="Новый"),
                                        current_user=user, db=db))
    assert out.created_store is True
    s = _run(db.get(MarketplaceStore, out.linked_store_id))
    assert s.external_store_id == "111" and s.label == "Новый" and s.store_key != "primary"


# 4. name never auto-links: providing neither id is rejected (no silent store pick)
def test_name_never_autolinks(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db))
    _stub_campaigns(monkeypatch, [_C1])
    with pytest.raises(HTTPException) as ei:
        _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="111"),
                                      current_user=user, db=db))
    assert ei.value.status_code == 422
    # store was NOT silently linked
    assert _run(db.get(MarketplaceStore, store.id)).external_store_id is None


# 5. one campaignId cannot link twice (to a second store)
def test_campaign_cannot_link_twice(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db, store_campaign="111"))
    other = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="yandex",
                             store_key=str(uuid.uuid4()), external_store_id=None, label="Второй",
                             source="manual", status="active")
    db.add(other); _run(db.commit())
    _stub_campaigns(monkeypatch, [_C1])
    with pytest.raises(HTTPException) as ei:
        _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="111", store_id=other.id),
                                      current_user=user, db=db))
    assert ei.value.status_code == 409


# 6. one store cannot link to two campaigns
def test_store_cannot_link_two_campaigns(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db, store_campaign="111"))
    _stub_campaigns(monkeypatch, [_C1, _C2])
    with pytest.raises(HTTPException) as ei:
        _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="222", store_id=store.id),
                                      current_user=user, db=db))
    assert ei.value.status_code == 409


# 7. foreign account/store → 404 (same as missing)
def test_foreign_store_404(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db))
    _other_user, other_acc, other_store, _oc = _run(_seed(db, business_id="BIZ-2"))
    _stub_campaigns(monkeypatch, [_C1])
    with pytest.raises(HTTPException) as ei:
        _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="111", store_id=other_store.id),
                                      current_user=user, db=db))
    assert ei.value.status_code == 404


def test_foreign_connection_404(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db))
    other_user, _oa, _os, other_conn = _run(_seed(db, business_id="BIZ-2"))
    _stub_campaigns(monkeypatch, [_C1])
    with pytest.raises(HTTPException) as ei:
        _run(list_connection_campaigns(other_conn.id, current_user=user, db=db))
    assert ei.value.status_code == 404


# 8. campaign of a different cabinet → 422
def test_campaign_other_cabinet_422(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db))
    _stub_campaigns(monkeypatch, [{"campaign_id": "999", "business_id": "OTHER", "label": "Чужой"}])
    with pytest.raises(HTTPException) as ei:
        _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="999", store_id=store.id),
                                      current_user=user, db=db))
    assert ei.value.status_code == 422


def test_unknown_campaign_404(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db))
    _stub_campaigns(monkeypatch, [_C1])
    with pytest.raises(HTTPException) as ei:
        _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="777", store_id=store.id),
                                      current_user=user, db=db))
    assert ei.value.status_code == 404


# not verified → 409, non-yandex → 422
def test_unverified_connection_409(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db, verified=False))
    _stub_campaigns(monkeypatch, [_C1])
    with pytest.raises(HTTPException) as ei:
        _run(list_connection_campaigns(conn.id, current_user=user, db=db))
    assert ei.value.status_code == 409


def test_non_yandex_422(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db))
    conn.marketplace = "wildberries"; _run(db.commit())
    with pytest.raises(HTTPException) as ei:
        _run(list_connection_campaigns(conn.id, current_user=user, db=db))
    assert ei.value.status_code == 422


# idempotent relink of the same campaign to the same store
def test_relink_same_is_idempotent(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db, store_campaign="111"))
    _stub_campaigns(monkeypatch, [_C1])
    out = _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="111", store_id=store.id),
                                        current_user=user, db=db))
    assert out.linked_store_id == store.id
    assert _run(db.execute(select(func.count()).select_from(MarketplaceStore))).scalar_one() == 1


# keyless store keeps its store_key (history stable) after linking
def test_link_keeps_store_key(monkeypatch):
    db = _run(_new_db()); user, acc, store, conn = _run(_seed(db))
    key_before = store.store_key
    _stub_campaigns(monkeypatch, [_C1])
    _run(link_connection_campaign(conn.id, CampaignLinkRequest(campaign_id="111", store_id=store.id),
                                  current_user=user, db=db))
    assert _run(db.get(MarketplaceStore, store.id)).store_key == key_before
