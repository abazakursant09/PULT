"""
SECURITY-2C-1 — server-side session revocation via users.token_version.

The access JWT carries the user's token_version as claim `ver`; get_current_user rejects any JWT whose
ver is absent / not a plain int / != the current token_version. logout and a successful password reset
atomically bump token_version, so a copied cookie dies immediately.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
from rate_limit import limit_auth
import models  # noqa: F401
from models.user import User
from routers import auth as auth_router
from routers.auth import hash_password
from dependencies import get_current_user
from csrf import OriginCsrfMiddleware

_LOOP = asyncio.new_event_loop()
ORIGIN = "http://localhost:3000"
GOOD_PW = "Passw0rdOk"
NAME = "pult_session_dev"


def _run(c):
    return _LOOP.run_until_complete(c)


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
    app.add_middleware(OriginCsrfMiddleware)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[limit_auth] = lambda: None
    return TestClient(app)


async def _seed(db, *, email=None, token_version=0, deleted=False):
    u = User(id=str(uuid.uuid4()), email=email or f"{uuid.uuid4()}@x.c", name="S",
             hashed_password=hash_password(GOOD_PW), is_verified=True, token_version=token_version,
             deleted_at=datetime.utcnow() if deleted else None)
    db.add(u)
    await db.commit()
    return u


def _tok(sub, ver, exp_min=60):
    payload = {"sub": sub, "exp": datetime.utcnow() + timedelta(minutes=exp_min)}
    if ver is not _OMIT:
        payload["ver"] = ver
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


class _OMIT:  # sentinel: omit the ver claim entirely
    pass


# ── issuance carries the current integer version ─────────────────────────────
def test_login_issues_int_ver_matching_user():
    db = _run(_new_db()); u = _run(_seed(db, email="a@b.c", token_version=3))
    c = _client(db)
    r = c.post("/api/auth/login", json={"email": "a@b.c", "password": GOOD_PW}, headers={"origin": ORIGIN})
    assert r.status_code == 200
    raw = r.headers["set-cookie"].split(f"{NAME}=")[1].split(";")[0]
    payload = jwt.decode(raw, settings.secret_key, algorithms=[settings.algorithm])
    assert payload["ver"] == 3 and type(payload["ver"]) is int


# ── /me accepts a matching version, rejects everything else ──────────────────
def test_me_matching_version_ok():
    db = _run(_new_db()); u = _run(_seed(db, token_version=5))
    c = _client(db); c.cookies.set(NAME, _tok(u.id, 5))
    assert c.get("/api/auth/me").status_code == 200


def test_me_wrong_version_401():
    db = _run(_new_db()); u = _run(_seed(db, token_version=5))
    c = _client(db); c.cookies.set(NAME, _tok(u.id, 4))
    assert c.get("/api/auth/me").status_code == 401


def test_me_missing_ver_401():
    db = _run(_new_db()); u = _run(_seed(db, token_version=0))
    c = _client(db); c.cookies.set(NAME, _tok(u.id, _OMIT))     # pre-2C-1 token, no ver
    assert c.get("/api/auth/me").status_code == 401


def test_me_bool_string_float_negative_ver_401():
    db = _run(_new_db()); u = _run(_seed(db, token_version=1))
    for bad in (True, "1", 1.0, -1):
        c = _client(db); c.cookies.set(NAME, _tok(u.id, bad))
        # True==1 would sneak past a naive int compare; type(ver) is int must exclude bool
        assert c.get("/api/auth/me").status_code == 401, f"ver={bad!r} accepted"


def test_me_expired_and_corrupt_401():
    db = _run(_new_db()); u = _run(_seed(db, token_version=0))
    c = _client(db)
    c.cookies.set(NAME, _tok(u.id, 0, exp_min=-1)); assert c.get("/api/auth/me").status_code == 401
    c.cookies.set(NAME, "not-a-jwt"); assert c.get("/api/auth/me").status_code == 401


def test_deleted_user_403_even_with_matching_ver():
    db = _run(_new_db()); u = _run(_seed(db, token_version=2, deleted=True))
    c = _client(db); c.cookies.set(NAME, _tok(u.id, 2))
    assert c.get("/api/auth/me").status_code == 403


# ── DB error fails closed as 503, not a false 401 ────────────────────────────
def test_db_error_is_503_not_401():
    class _BoomDB:
        async def execute(self, *a, **k):
            raise SQLAlchemyError("db down")

    async def go():
        req = Request({"type": "http", "headers": [(b"cookie", f"{NAME}=".encode() +
                       _tok("11111111-1111-4111-8111-111111111111", 0).encode())], "path": "/", "method": "GET"})
        try:
            await get_current_user(req, _BoomDB())
            return None
        except Exception as e:
            return getattr(e, "status_code", None)
    assert _run(go()) == 503


# ── logout revokes: copied cookie dies ───────────────────────────────────────
def test_logout_bumps_version_and_kills_copied_cookie():
    db = _run(_new_db()); u = _run(_seed(db, email="lo@b.c", token_version=0))
    c = _client(db)
    tok = _tok(u.id, 0)
    c.cookies.set(NAME, tok)
    assert c.get("/api/auth/me").status_code == 200          # valid before logout
    assert c.post("/api/auth/logout", headers={"origin": ORIGIN}).status_code == 204
    # a COPY of the pre-logout JWT (same ver 0) must now be rejected
    c.cookies.set(NAME, tok)
    assert c.get("/api/auth/me").status_code == 401
    assert _run(db.get(User, u.id)).token_version == 1        # atomic bump landed


def test_logout_idempotent_without_and_with_revoked_cookie():
    db = _run(_new_db()); u = _run(_seed(db, token_version=0))
    c = _client(db)
    assert c.post("/api/auth/logout", headers={"origin": ORIGIN}).status_code == 204   # no cookie
    c.cookies.set(NAME, _tok(u.id, 99))                       # already-revoked (wrong ver)
    assert c.post("/api/auth/logout", headers={"origin": ORIGIN}).status_code == 204
    assert _run(db.get(User, u.id)).token_version == 0        # no bump on an invalid cookie


# ── password reset revokes all prior sessions ────────────────────────────────
def test_reset_bumps_version_old_cookie_dies_new_login_works():
    db = _run(_new_db())
    u = _run(_seed(db, email="rs@b.c", token_version=0))
    from services.reset_token import hash_reset_token   # SECURITY-2C-3B — DB holds the digest
    u.reset_token = hash_reset_token("rtok"); u.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
    _run(db.commit())
    c = _client(db)
    old = _tok(u.id, 0)
    c.cookies.set(NAME, old); assert c.get("/api/auth/me").status_code == 200
    r = c.post("/api/auth/reset-password", json={"token": "rtok", "password": "NewPass0rd"},
               headers={"origin": ORIGIN})
    assert r.status_code == 200
    c.cookies.set(NAME, old); assert c.get("/api/auth/me").status_code == 401   # old session revoked
    # a fresh login issues a JWT with the new version and works
    c.cookies.clear()
    r2 = c.post("/api/auth/login", json={"email": "rs@b.c", "password": "NewPass0rd"},
                headers={"origin": ORIGIN})
    assert r2.status_code == 200
    assert _run(db.get(User, u.id)).token_version == 1
