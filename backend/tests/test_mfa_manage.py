"""SECURITY-2C-4A — MFA enable/disable durable throttle + token_version bump + cookie reissue (SQLite).

Enable and disable now: reserve a `mfa_manage` attempt (429 if blocked), claim the TOTP step, flip
`enabled`, bump token_version, commit once, then reissue the current user a fresh cookie. A wrong /
replayed code changes nothing. `/mfa/setup` still refuses to rotate the secret while MFA is enabled.
The real-PostgreSQL concurrency + session-revocation proof is in test_mfa_throttle_pg.py.
"""
import asyncio
import time
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
import models  # noqa: F401
from models.user import User
from models.mfa_secret import MFASecret
from config import settings
from routers import mfa as mfa_router
from routers.auth import hash_password, create_access_token
from routers.mfa import _generate_secret, _totp

_LOOP = asyncio.new_event_loop()
COOKIE = "pult_session_dev"


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, enabled, ver=0):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@x.c", name="S", hashed_password=hash_password("pw"),
                is_verified=True, token_version=ver))
    await db.commit()
    secret = _generate_secret()
    db.add(MFASecret(user_id=uid, secret=secret, enabled=enabled))
    await db.commit()
    return uid, secret


def _client(db, uid):
    async def _override():
        yield db
    app = FastAPI()
    app.include_router(mfa_router.router, prefix="/api/mfa")
    app.dependency_overrides[get_db] = _override
    c = TestClient(app)
    c.cookies.set(COOKIE, create_access_token(uid, 0))
    return c


def _code(secret):
    return _totp(secret, int(time.time()))


# ── enable: bump + cookie ────────────────────────────────────────────────────

def test_enable_bumps_version_and_sets_cookie():
    db = _run(_new_db())
    uid, secret = _run(_seed(db, enabled=False, ver=0))
    r = _client(db, uid).post("/api/mfa/verify", json={"code": _code(secret)})
    assert r.status_code == 200
    assert "set-cookie" in {k.lower() for k in r.headers}
    async def check():
        db.expire_all()
        rec = (await db.execute(select(MFASecret).where(MFASecret.user_id == uid))).scalar_one()
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        return rec.enabled, u.token_version
    enabled, ver = _run(check())
    assert enabled is True and ver == 1


def test_disable_bumps_version_and_sets_cookie():
    db = _run(_new_db())
    uid, secret = _run(_seed(db, enabled=True, ver=0))
    r = _client(db, uid).request("DELETE", "/api/mfa/disable", json={"code": _code(secret)})
    assert r.status_code == 200 and "set-cookie" in {k.lower() for k in r.headers}
    async def check():
        db.expire_all()
        rec = (await db.execute(select(MFASecret).where(MFASecret.user_id == uid))).scalar_one()
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        return rec.enabled, u.token_version
    enabled, ver = _run(check())
    assert enabled is False and ver == 1


def test_wrong_disable_code_changes_nothing():
    db = _run(_new_db())
    uid, secret = _run(_seed(db, enabled=True, ver=0))
    wrong = "000000" if _code(secret) != "000000" else "111111"
    r = _client(db, uid).request("DELETE", "/api/mfa/disable", json={"code": wrong})
    assert r.status_code == 400 and "set-cookie" not in {k.lower() for k in r.headers}
    async def check():
        db.expire_all()
        rec = (await db.execute(select(MFASecret).where(MFASecret.user_id == uid))).scalar_one()
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        return rec.enabled, u.token_version
    enabled, ver = _run(check())
    assert enabled is True and ver == 0           # no bump, no disable


def test_enable_then_old_cookie_is_rejected():
    db = _run(_new_db())
    uid, secret = _run(_seed(db, enabled=False, ver=0))
    _client(db, uid).post("/api/mfa/verify", json={"code": _code(secret)})
    from fastapi import Request
    def _req(tok):
        return Request({"type": "http", "method": "GET", "path": "/",
                        "headers": [(b"cookie", f"{COOKIE}={tok}".encode())]})
    async def check():
        db.expire_all()
        from fastapi import HTTPException
        rejected = False
        try:
            await get_current_user(_req(create_access_token(uid, 0)), db)
        except HTTPException as ex:
            rejected = ex.status_code == 401
        u = await get_current_user(_req(create_access_token(uid, 1)), db)   # new ver accepted
        return rejected, u.id == uid
    old_rejected, new_ok = _run(check())
    assert old_rejected is True and new_ok is True


# ── mfa_manage durable throttle ──────────────────────────────────────────────

def test_disable_throttle_blocks_after_pair_limit():
    db = _run(_new_db())
    uid, secret = _run(_seed(db, enabled=True, ver=0))
    c = _client(db, uid)
    wrong = "000000" if _code(secret) != "000000" else "111111"
    statuses = [c.request("DELETE", "/api/mfa/disable", json={"code": wrong}).status_code
                for _ in range(settings.auth_throttle_mfa_manage_pair_limit + 2)]
    assert 429 in statuses                          # durable mfa_manage throttle trips
    assert statuses[0] == 400 and statuses[-1] == 429


# ── setup guard: no secret rotation while enabled ────────────────────────────

def test_setup_refuses_to_rotate_secret_when_enabled():
    db = _run(_new_db())
    uid, secret = _run(_seed(db, enabled=True, ver=0))
    r = _client(db, uid).post("/api/mfa/setup")
    assert r.status_code == 400                     # cannot re-run setup while MFA is enabled
    async def check():
        db.expire_all()
        return (await db.execute(select(MFASecret.secret).where(MFASecret.user_id == uid))).scalar_one()
    assert _run(check()) == secret                  # the existing secret is untouched
