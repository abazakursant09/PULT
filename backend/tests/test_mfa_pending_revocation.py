"""Password-checked MFA challenges must not survive account revocation."""
import asyncio
from datetime import datetime, timedelta
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from auth_cookie import cookie_name
from config import settings
from database import Base, get_db
from models.mfa_secret import MFASecret
from models.user import User
from routers import auth as auth_router
from routers.mfa import _generate_secret
from services.reset_token import hash_reset_token


@pytest.fixture
def account(monkeypatch):
    loop = asyncio.new_event_loop()
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    uid = str(uuid.uuid4())
    email = f"{uid}@example.com"

    async def seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            db.add(User(id=uid, email=email, name="Synthetic MFA seller",
                        hashed_password=auth_router.hash_password("Passw0rdOk"),
                        is_verified=True, token_version=7,
                        reset_token=hash_reset_token("synthetic-reset"),
                        reset_token_expires=datetime.utcnow() + timedelta(minutes=10)))
            db.add(MFASecret(user_id=uid, secret=_generate_secret(), enabled=True))
            await db.commit()

    loop.run_until_complete(seed())

    async def database():
        async with maker() as db:
            yield db

    # This suite tests revocation, not TOTP mathematics/replay. The real durable
    # claim/replay path is covered in test_mfa_replay.py; record that invalid
    # challenges fail BEFORE reaching the second factor.
    claims = []

    async def claim(*args, **kwargs):
        claims.append(kwargs)
        return True

    monkeypatch.setattr(auth_router, "claim_totp_step", claim)
    monkeypatch.setattr(auth_router, "load_secret", lambda secret: "synthetic")
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield client, maker, loop, uid, email, claims
    loop.run_until_complete(engine.dispose())
    loop.close()


def pending(account):
    client, _, _, _, email, _ = account
    response = client.post("/api/auth/login", json={"email": email, "password": "Passw0rdOk"})
    assert response.status_code == 200
    assert response.json()["mfa_required"] is True
    assert "set-cookie" not in response.headers
    return response.json()["mfa_token"]


def finish(account, token):
    return account[0].post("/api/auth/login/mfa", json={"mfa_token": token, "code": "123456"})


def test_pending_challenge_carries_password_checked_integer_version(account):
    claims = jwt.decode(pending(account), settings.secret_key, algorithms=[settings.algorithm])
    assert claims["ver"] == 7 and type(claims["ver"]) is int


def test_password_reset_revokes_a_previously_password_checked_challenge(account):
    token = pending(account)
    response = account[0].post("/api/auth/reset-password", json={
        "token": "synthetic-reset", "password": "NewPassw0rdOk"})
    assert response.status_code == 200
    refused = finish(account, token)
    assert refused.status_code == 401
    assert "set-cookie" not in refused.headers
    assert account[5] == []


def test_logout_revokes_a_previously_password_checked_challenge(account):
    token = pending(account)
    client, _, _, uid, _, claims = account
    client.cookies.set(cookie_name(), auth_router.create_access_token(uid, 7))
    assert client.post("/api/auth/logout").status_code == 204
    refused = finish(account, token)
    assert refused.status_code == 401
    assert "set-cookie" not in refused.headers
    assert claims == []


@pytest.mark.parametrize("field,value", [("deleted_at", "deleted"), ("is_verified", False)])
def test_deleted_or_unverified_account_cannot_finish_a_pending_challenge(account, field, value):
    token = pending(account)
    _, maker, loop, uid, _, claims = account

    async def change():
        async with maker() as db:
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
            setattr(user, field, datetime.utcnow() if value == "deleted" else value)
            await db.commit()

    loop.run_until_complete(change())
    refused = finish(account, token)
    assert refused.status_code == 401
    assert "set-cookie" not in refused.headers
    assert claims == []


@pytest.mark.parametrize("version", [None, True, "7", -1, 6])
def test_legacy_malformed_and_stale_challenge_versions_are_refused(account, version):
    payload = {"sub": account[3], "mfa_pending": True,
               "exp": datetime.utcnow() + timedelta(minutes=5)}
    if version is not None:
        payload["ver"] = version
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    response = finish(account, token)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert account[5] == []


def test_fresh_password_checked_challenge_still_issues_only_a_session_cookie(account):
    response = finish(account, pending(account))
    assert response.status_code == 200
    assert len(account[5]) == 1
    assert "access_token" not in response.json()
    assert cookie_name() in response.headers["set-cookie"]


def test_a_versioned_pending_challenge_is_not_a_full_session(account):
    token = pending(account)
    client = account[0]
    client.cookies.set(cookie_name(), token)
    assert client.get("/api/auth/me").status_code == 401
    assert account[5] == []


@pytest.mark.parametrize("marker", [False, 1, "true", None, {}, []])
def test_only_boolean_true_is_an_mfa_pending_marker(account, marker):
    payload = {"sub": account[3], "ver": 7, "mfa_pending": marker,
               "exp": datetime.utcnow() + timedelta(minutes=5)}
    response = finish(account, jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm))
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert account[5] == []


def test_revocation_during_second_factor_cannot_upgrade_the_issued_session(account, monkeypatch):
    token = pending(account)
    client, _, _, uid, _, _ = account

    async def revoke_during_claim(db, **kwargs):
        # Deterministic interleaving, not a claim of PostgreSQL concurrency proof.
        # Synchronizing the ORM object to8 catches accidental use of its NEW ver.
        await db.execute(update(User).where(User.id == uid).values(token_version=8))
        return True

    monkeypatch.setattr(auth_router, "claim_totp_step", revoke_during_claim)
    response = finish(account, token)
    assert response.status_code == 200
    session = client.cookies.get(cookie_name())
    assert jwt.decode(session, settings.secret_key, algorithms=[settings.algorithm])["ver"] == 7
    assert client.get("/api/auth/me").status_code == 401
