"""Disconnecting a shop and connecting it back — and why automation must not come back with it.

Two defects are pinned here.

B1. Reconnecting was IMPOSSIBLE. `DELETE` set `status='revoked'`, and the reuse branch of the
    connect route updated scopes/label/client-id but never touched `status` — the value was
    assigned in exactly one place, inside the create-a-new-row branch. So the seller's new key was
    stored and encrypted while the row stayed revoked forever: the card said "Отключён" with no
    buttons, and the automation gate went on refusing it. There was no way out through the UI and
    none through the API either.

    The reason it survived to production is visible in this file's absence until now: no test ever
    disconnected and reconnected.

B1-safety. A rule left `enabled` through a disconnect is armed and waiting. Reconnect the shop —
    or merely rotate the key — and automatic publishing resumes on its own, under credentials the
    seller never approved it for. Automation is therefore switched off on BOTH ends: at disconnect
    and again whenever credentials are written. Consent and mode survive, because the seller really
    did consent and re-signing the whole agreement for a key rotation would punish good hygiene.
"""
import asyncio
import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  register tables
from models.user import User
from models.workspace import Workspace
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from schemas.marketplace import ConnectionCreate
from services.marketplace import credential_vault
from services.marketplace.review_automation_gate import CONSENT_VERSION as _CV
from routers.connections import create_connection, delete_connection

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
    user = User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True)
    db.add(user)
    db.add(Workspace(id=str(uuid.uuid4()), owner_user_id=uid))
    await db.commit()
    return user


def _body(mp, token="key-1", client_id=None):
    return ConnectionCreate(marketplace=mp, token=token, scope="feedbacks",
                            ozon_client_id=client_id)


async def _rule(db, user_id, connection_id, *, enabled=True, mode="auto"):
    rule = AutomationRule(
        id=str(uuid.uuid4()), user_id=user_id, contour="reputation",
        action_type="publish_review_response", mode=mode, enabled=enabled, guard={},
        connection_id=connection_id, consent_at=datetime.utcnow(), consent_version=_CV,
    )
    db.add(rule)
    await db.commit()
    return rule


async def _reload(db, model, row_id):
    return (await db.execute(select(model).where(model.id == row_id))).scalars().first()


# ── B1: disconnect → reconnect ──────────────────────────────────────────────

def test_wildberries_reconnects_onto_the_same_row():
    """The defect in one test: disconnect, connect again, and the shop must work."""
    async def go():
        db = await _new_db()
        user = await _user(db)
        first = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        await delete_connection(connection_id=first.id, current_user=user, db=db)
        revoked = await _reload(db, MarketplaceConnection, first.id)
        assert revoked.status == "revoked"

        again = await create_connection(body=_body("wildberries", token="key-2"),
                                        current_user=user, db=db)
        rows = (await db.execute(select(MarketplaceConnection))).scalars().all()
        conn = await _reload(db, MarketplaceConnection, first.id)
        await db.close()
        return again, rows, conn

    again, rows, conn = _run(go())
    assert conn.status == "connected"
    assert again.id == conn.id                 # same row…
    assert len(rows) == 1                      # …and no second connection was minted


def test_ozon_reconnects_with_its_client_id():
    async def go():
        db = await _new_db()
        user = await _user(db)
        first = await create_connection(body=_body("ozon", client_id="CID"),
                                        current_user=user, db=db)
        await delete_connection(connection_id=first.id, current_user=user, db=db)
        await create_connection(body=_body("ozon", token="key-2", client_id="CID"),
                                current_user=user, db=db)
        conn = await _reload(db, MarketplaceConnection, first.id)
        await db.close()
        return conn
    conn = _run(go())
    assert conn.status == "connected" and conn.ozon_client_id == "CID"


def test_yandex_reconnects():
    async def go():
        db = await _new_db()
        user = await _user(db)
        first = await create_connection(body=_body("yandex"), current_user=user, db=db)
        await delete_connection(connection_id=first.id, current_user=user, db=db)
        await create_connection(body=_body("yandex", token="key-2"), current_user=user, db=db)
        conn = await _reload(db, MarketplaceConnection, first.id)
        await db.close()
        return conn
    assert _run(go()).status == "connected"


def test_the_new_key_is_stored_and_unverified():
    """A key that has never been checked must not inherit the old key's green tick."""
    async def go():
        db = await _new_db()
        user = await _user(db)
        first = await create_connection(body=_body("wildberries", token="old"),
                                        current_user=user, db=db)
        cred = (await db.execute(select(ApiCredential))).scalars().first()
        cred.verification_status = "verified"
        cred.verified_at = datetime.utcnow()
        await db.commit()

        await delete_connection(connection_id=first.id, current_user=user, db=db)
        await create_connection(body=_body("wildberries", token="brand-new"),
                                current_user=user, db=db)
        fresh = (await db.execute(select(ApiCredential))).scalars().first()
        secret = credential_vault.decrypt(fresh.secret_enc)
        await db.close()
        return fresh, secret
    fresh, secret = _run(go())
    assert secret == "brand-new"
    assert fresh.verification_status == "unverified"
    assert fresh.verified_at is None


def test_reconnecting_keeps_one_marketplace_account():
    """Identity survives the round trip — the cabinet is the same cabinet."""
    async def go():
        db = await _new_db()
        user = await _user(db)
        first = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        before = (await _reload(db, MarketplaceConnection, first.id)).marketplace_account_id
        await delete_connection(connection_id=first.id, current_user=user, db=db)
        await create_connection(body=_body("wildberries", token="k2"), current_user=user, db=db)
        after = (await _reload(db, MarketplaceConnection, first.id)).marketplace_account_id
        await db.close()
        return before, after
    before, after = _run(go())
    assert before is not None and before == after


# ── B1-safety: automation must never come back on its own ───────────────────

def test_disconnecting_switches_automation_off():
    async def go():
        db = await _new_db()
        user = await _user(db)
        conn = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        rule = await _rule(db, user.id, conn.id, enabled=True)
        await delete_connection(connection_id=conn.id, current_user=user, db=db)
        out = await _reload(db, AutomationRule, rule.id)
        await db.close()
        return out
    assert _run(go()).enabled is False


def test_automation_stays_off_after_reconnect():
    """The dangerous case: it was ON before the disconnect. It must not resume by itself."""
    async def go():
        db = await _new_db()
        user = await _user(db)
        conn = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        rule = await _rule(db, user.id, conn.id, enabled=True)
        await delete_connection(connection_id=conn.id, current_user=user, db=db)
        await create_connection(body=_body("wildberries", token="k2"), current_user=user, db=db)
        out = await _reload(db, AutomationRule, rule.id)
        await db.close()
        return out
    assert _run(go()).enabled is False


def test_replacing_a_key_alone_also_disarms_automation():
    """No disconnect involved — just a rotation. The rule would otherwise keep publishing with a
    key the seller never approved it for."""
    async def go():
        db = await _new_db()
        user = await _user(db)
        conn = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        rule = await _rule(db, user.id, conn.id, enabled=True)
        await create_connection(body=_body("wildberries", token="rotated"),
                                current_user=user, db=db)
        out = await _reload(db, AutomationRule, rule.id)
        await db.close()
        return out
    assert _run(go()).enabled is False


def test_consent_and_mode_survive():
    """Only `enabled` is dropped. Re-signing consent for a key rotation would punish hygiene."""
    async def go():
        db = await _new_db()
        user = await _user(db)
        conn = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        rule = await _rule(db, user.id, conn.id, enabled=True, mode="auto")
        await delete_connection(connection_id=conn.id, current_user=user, db=db)
        await create_connection(body=_body("wildberries", token="k2"), current_user=user, db=db)
        out = await _reload(db, AutomationRule, rule.id)
        await db.close()
        return out
    out = _run(go())
    assert out.consent_at is not None and out.consent_version == _CV
    assert out.mode == "auto"
    assert out.enabled is False


def test_another_sellers_automation_is_untouched():
    async def go():
        db = await _new_db()
        mine = await _user(db)
        theirs = await _user(db)
        my_conn = await create_connection(body=_body("wildberries"), current_user=mine, db=db)
        their_conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=theirs.id,
                                           marketplace="wildberries", status="connected",
                                           scopes=["feedbacks"])
        db.add(their_conn)
        await db.commit()
        their_rule = await _rule(db, theirs.id, their_conn.id, enabled=True)
        await delete_connection(connection_id=my_conn.id, current_user=mine, db=db)
        out = await _reload(db, AutomationRule, their_rule.id)
        await db.close()
        return out
    assert _run(go()).enabled is True


def test_the_seller_can_switch_it_back_on_themselves():
    """Disarming must not be a trap: after reconnecting, enabling again has to work."""
    async def go():
        db = await _new_db()
        user = await _user(db)
        conn = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        rule = await _rule(db, user.id, conn.id, enabled=True)
        await delete_connection(connection_id=conn.id, current_user=user, db=db)
        await create_connection(body=_body("wildberries", token="k2"), current_user=user, db=db)

        row = await _reload(db, AutomationRule, rule.id)
        row.enabled = True                      # what the seller's own click does
        await db.commit()
        out = await _reload(db, AutomationRule, rule.id)
        conn_row = await _reload(db, MarketplaceConnection, conn.id)
        await db.close()
        return out, conn_row
    out, conn_row = _run(go())
    assert out.enabled is True
    assert conn_row.status == "connected"       # …and the gate no longer blocks it


# ── ownership and idempotency ───────────────────────────────────────────────

def test_another_sellers_connection_cannot_be_disconnected():
    async def go():
        db = await _new_db()
        mine = await _user(db)
        theirs = await _user(db)
        their_conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=theirs.id,
                                           marketplace="wildberries", status="connected",
                                           scopes=["feedbacks"])
        db.add(their_conn)
        await db.commit()
        try:
            await delete_connection(connection_id=their_conn.id, current_user=mine, db=db)
            raised = None
        except HTTPException as e:
            raised = e
        still = await _reload(db, MarketplaceConnection, their_conn.id)
        await db.close()
        return raised, still
    raised, still = _run(go())
    assert raised is not None and raised.status_code == 404
    assert still.status == "connected"          # untouched


def test_another_sellers_connection_cannot_be_overwritten():
    """Connecting is scoped by user_id, so an identical marketplace makes a SEPARATE row."""
    async def go():
        db = await _new_db()
        mine = await _user(db)
        theirs = await _user(db)
        their_conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=theirs.id,
                                           marketplace="wildberries", status="connected",
                                           scopes=["feedbacks"])
        db.add(their_conn)
        await db.commit()
        out = await create_connection(body=_body("wildberries"), current_user=mine, db=db)
        untouched = await _reload(db, MarketplaceConnection, their_conn.id)
        await db.close()
        return out, untouched
    out, untouched = _run(go())
    assert out.id != untouched.id
    assert untouched.user_id != out.id


def test_connecting_twice_never_makes_a_duplicate():
    async def go():
        db = await _new_db()
        user = await _user(db)
        a = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        b = await create_connection(body=_body("wildberries", token="k2"),
                                    current_user=user, db=db)
        conns = (await db.execute(select(MarketplaceConnection))).scalars().all()
        creds = (await db.execute(select(ApiCredential))).scalars().all()
        await db.close()
        return a, b, conns, creds
    a, b, conns, creds = _run(go())
    assert a.id == b.id
    assert len(conns) == 1
    assert len(creds) == 1                      # the scope's credential is replaced, not appended


def test_disconnecting_is_allowed_even_with_a_broken_key():
    """Leaving must never depend on the key working — that would strand the seller precisely when
    the key is the problem."""
    async def go():
        db = await _new_db()
        user = await _user(db)
        conn = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        cred = (await db.execute(select(ApiCredential))).scalars().first()
        cred.verification_status = "invalid_credentials"
        await db.commit()
        await delete_connection(connection_id=conn.id, current_user=user, db=db)
        out = await _reload(db, MarketplaceConnection, conn.id)
        await db.close()
        return out
    assert _run(go()).status == "revoked"


def test_disconnecting_twice_is_harmless():
    async def go():
        db = await _new_db()
        user = await _user(db)
        conn = await create_connection(body=_body("wildberries"), current_user=user, db=db)
        await delete_connection(connection_id=conn.id, current_user=user, db=db)
        await delete_connection(connection_id=conn.id, current_user=user, db=db)
        out = await _reload(db, MarketplaceConnection, conn.id)
        await db.close()
        return out
    assert _run(go()).status == "revoked"


def test_the_public_client_id_is_returned_but_the_secret_is_not():
    """The Ozon Client-Id is the public half and is needed to prefill a key replacement. The API
    key must never come back out."""
    async def go():
        db = await _new_db()
        user = await _user(db)
        out = await create_connection(body=_body("ozon", token="secret-key", client_id="CID"),
                                      current_user=user, db=db)
        await db.close()
        return out
    out = _run(go())
    assert out.ozon_client_id == "CID"
    assert "secret-key" not in out.model_dump_json()
