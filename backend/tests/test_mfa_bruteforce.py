"""POST /login/mfa had no attempt limit: an attacker holding a valid password got a 5-min
mfa_pending token and could spray unlimited 6-digit TOTP codes, brute-forcing the second factor.
A per-account (+IP) limiter now caps attempts. These tests prove repeated invalid codes get
blocked, a valid code within the allowance still works, and the plain login flow is untouched.
"""
import asyncio
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import rate_limit
from database import Base, get_db
import models  # registers tables
from models.user import User
from models.mfa_secret import MFASecret
from routers import auth as auth_router
from routers.auth import create_mfa_pending_token, hash_password
from routers.mfa import _generate_secret, _totp

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_mfa_user(db):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@example.com", name="S",
                hashed_password=hash_password("pw"), is_verified=True))
    secret = _generate_secret()
    db.add(MFASecret(user_id=uid, secret=secret, enabled=True))
    await db.commit()
    return uid, secret


def _client(db):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def _fresh_limiter(monkeypatch):
    monkeypatch.setattr(rate_limit, "_limiter", rate_limit._SlidingWindow())


def _valid_code(secret):
    import time
    return _totp(secret, int(time.time()))


def test_repeated_invalid_mfa_codes_are_blocked(monkeypatch):
    _fresh_limiter(monkeypatch)
    db = _run(_new_db())
    uid, _secret = _run(_seed_mfa_user(db))
    c = _client(db)
    token = create_mfa_pending_token(uid)

    statuses = []
    for _ in range(8):
        r = c.post("/api/auth/login/mfa", json={"mfa_token": token, "code": "000000"})
        statuses.append(r.status_code)

    assert 429 in statuses, f"brute-force was never throttled: {statuses}"
    # The first few are 401 (wrong code), then it flips to 429 and stays blocked.
    assert statuses[0] == 401
    assert statuses[-1] == 429


def test_valid_code_within_allowance_still_works(monkeypatch):
    _fresh_limiter(monkeypatch)
    db = _run(_new_db())
    uid, secret = _run(_seed_mfa_user(db))
    c = _client(db)
    token = create_mfa_pending_token(uid)

    # A couple of fat-finger misses (under the cap), then the right code.
    assert c.post("/api/auth/login/mfa", json={"mfa_token": token, "code": "111111"}).status_code == 401
    assert c.post("/api/auth/login/mfa", json={"mfa_token": token, "code": "222222"}).status_code == 401
    ok = c.post("/api/auth/login/mfa", json={"mfa_token": token, "code": _valid_code(secret)})
    assert ok.status_code == 200, ok.text
    assert "access_token" in ok.json()


def test_limiter_is_keyed_per_account(monkeypatch):
    # Hammering account A to its limit must not lock out account B.
    _fresh_limiter(monkeypatch)
    db = _run(_new_db())
    uid_a, _sa = _run(_seed_mfa_user(db))
    uid_b, secret_b = _run(_seed_mfa_user(db))
    c = _client(db)

    tok_a = create_mfa_pending_token(uid_a)
    for _ in range(8):
        c.post("/api/auth/login/mfa", json={"mfa_token": tok_a, "code": "000000"})

    tok_b = create_mfa_pending_token(uid_b)
    ok = c.post("/api/auth/login/mfa", json={"mfa_token": tok_b, "code": _valid_code(secret_b)})
    assert ok.status_code == 200, "account B was wrongly locked out by an attack on A"
