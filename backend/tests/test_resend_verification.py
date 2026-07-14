"""Registration swallows a mail-delivery failure and returns 201, and the verification link is
delivered only by email — so a seller whose first mail never arrived had no way back in. POST
/auth/resend-verification closes that dead-end: fresh token, re-sent mail, neutral response, no
enumeration, never returns the token. These tests prove the lifecycle and the neutrality.
"""
import asyncio
import uuid
from datetime import datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from rate_limit import limit_auth
import models  # registers tables
from models.user import User
from routers import auth as auth_router
from routers.auth import hash_password

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _client(db):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[limit_auth] = lambda: None    # limiter itself tested elsewhere
    return TestClient(app)


async def _seed(db, *, verified: bool, deleted: bool = False, token="old-token"):
    email = f"{uuid.uuid4()}@example.com"
    db.add(User(id=str(uuid.uuid4()), email=email, name="S",
                hashed_password=hash_password("pw"), is_verified=verified,
                verification_token=None if verified else token,
                deleted_at=(datetime.utcnow() if deleted else None)))
    await db.commit()
    return email


_NEUTRAL = "Если аккаунт с таким email существует и не подтверждён"


def test_resend_for_unverified_user_mints_new_token_and_sends():
    db = _run(_new_db())
    email = _run(_seed(db, verified=False, token="old-token"))
    c = _client(db)

    with patch("routers.auth.send_verification_email") as send:
        r = c.post("/api/auth/resend-verification", json={"email": email})

    assert r.status_code == 200
    assert _NEUTRAL in r.json()["message"]
    assert "token" not in r.json()                         # never returned
    send.assert_called_once()

    # Token was rotated (old one invalidated), and it is NOT the message.
    user = _run(db.execute(select(User).where(User.email == email))).scalar_one()
    assert user.verification_token and user.verification_token != "old-token"
    assert user.verification_token not in r.json()["message"]


def test_response_is_neutral_for_unknown_email():
    db = _run(_new_db())
    c = _client(db)
    with patch("routers.auth.send_verification_email") as send:
        r = c.post("/api/auth/resend-verification", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert _NEUTRAL in r.json()["message"]
    send.assert_not_called()                               # no mail, but same message


def test_already_verified_user_gets_no_mail_but_same_message():
    db = _run(_new_db())
    email = _run(_seed(db, verified=True))
    c = _client(db)
    with patch("routers.auth.send_verification_email") as send:
        r = c.post("/api/auth/resend-verification", json={"email": email})
    assert r.status_code == 200
    assert _NEUTRAL in r.json()["message"]
    send.assert_not_called()


def test_deleted_account_gets_no_mail():
    db = _run(_new_db())
    email = _run(_seed(db, verified=False, deleted=True))
    c = _client(db)
    with patch("routers.auth.send_verification_email") as send:
        r = c.post("/api/auth/resend-verification", json={"email": email})
    assert r.status_code == 200
    send.assert_not_called()


def test_new_token_verifies_and_old_one_fails():
    # End to end: resend, then the fresh token verifies the account, the stale one does not.
    db = _run(_new_db())
    email = _run(_seed(db, verified=False, token="stale"))
    c = _client(db)
    with patch("routers.auth.send_verification_email"):
        c.post("/api/auth/resend-verification", json={"email": email})
    fresh = _run(db.execute(select(User).where(User.email == email))).scalar_one().verification_token

    assert c.get(f"/api/auth/verify-email?token=stale").status_code == 400
    ok = c.get(f"/api/auth/verify-email?token={fresh}")
    assert ok.status_code == 200, ok.text
