"""SECURITY-2C-3B — reset-token digest at rest + atomic one-time confirm (unit / SQLite).

The DB stores only sha256(raw); the raw token rides the emailed link. Confirm is one atomic UPDATE that
consumes the token, so unknown / expired / reused all return the same neutral 400. The real-PostgreSQL
concurrency proof (two confirms of one token → exactly one success) is in test_reset_digest_pg.py.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
import models  # noqa: F401
from models.user import User
from routers import auth as auth_router
from routers.auth import hash_password
from services.reset_token import hash_reset_token

_LOOP = asyncio.new_event_loop()
GOOD_PW = "NewPass0rd"


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, email, reset_raw=None, expires=None, verified=True):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=email, name="S", hashed_password=hash_password("OldPass0rd"),
                is_verified=verified,
                reset_token=hash_reset_token(reset_raw) if reset_raw else None,
                reset_token_expires=expires))
    await db.commit()
    return uid


def _client(db):
    async def _override():
        yield db
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


# ══ pure digest helper ══════════════════════════════════════════════════════

def test_digest_is_deterministic_64_lowercase_hex():
    d = hash_reset_token("abc123")
    assert d == hash_reset_token("abc123")
    assert len(d) == 64 and all(c in "0123456789abcdef" for c in d)


def test_different_raw_gives_different_digest():
    assert hash_reset_token("token-a") != hash_reset_token("token-b")


def test_digest_does_not_contain_the_raw():
    raw = "supersecret-raw-token"
    assert raw not in hash_reset_token(raw)


# ══ forgot stores the digest, emails the raw ════════════════════════════════

def _capture_email(monkeypatch):
    box = {}
    async def _rec(to, name, token):
        box["raw"] = token
        return True
    monkeypatch.setattr(auth_router, "send_password_reset_email", _rec)
    return box


def test_forgot_stores_digest_not_raw(monkeypatch):
    box = _capture_email(monkeypatch)

    async def go():
        db = await _new_db()
        uid = await _seed(db, email="f@b.c", verified=True)
        c = _client(db)
        c.post("/api/auth/forgot-password", json={"email": "f@b.c"})
        db.expire_all()
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        return box["raw"], u.reset_token
    raw, stored = _run(go())
    assert stored == hash_reset_token(raw)          # DB holds the digest
    assert stored != raw and len(stored) == 64      # never the raw token


def test_guard_raw_reset_token_is_never_written_to_db(monkeypatch):
    """A raw token (contains '-' / '_' or is <64 chars) must never sit in User.reset_token."""
    box = _capture_email(monkeypatch)

    async def go():
        db = await _new_db()
        await _seed(db, email="g@b.c", verified=True)
        _client(db).post("/api/auth/forgot-password", json={"email": "g@b.c"})
        db.expire_all()
        stored = (await db.execute(select(User.reset_token).where(User.email == "g@b.c"))).scalar_one()
        return box["raw"], stored
    raw, stored = _run(go())
    assert stored != raw
    assert len(stored) == 64 and all(c in "0123456789abcdef" for c in stored)   # a hex digest, not a raw token


# ══ atomic confirm: happy path, reuse, unknown, expired, old-plaintext ══════

def test_valid_raw_resets_and_consumes(monkeypatch):
    box = _capture_email(monkeypatch)

    async def go():
        db = await _new_db()
        uid = await _seed(db, email="v@b.c", verified=False)
        c = _client(db)
        c.post("/api/auth/forgot-password", json={"email": "v@b.c"})
        r = c.post("/api/auth/reset-password", json={"token": box["raw"], "password": GOOD_PW})
        db.expire_all()
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        return r.status_code, u.reset_token, u.is_verified, auth_router.verify_password(GOOD_PW, u.hashed_password)
    status, tok, verified, pw_ok = _run(go())
    assert status == 200 and tok is None and verified is True and pw_ok is True


def test_reuse_after_success_is_neutral_400(monkeypatch):
    box = _capture_email(monkeypatch)

    async def go():
        db = await _new_db()
        await _seed(db, email="r@b.c")
        c = _client(db)
        c.post("/api/auth/forgot-password", json={"email": "r@b.c"})
        first = c.post("/api/auth/reset-password", json={"token": box["raw"], "password": GOOD_PW})
        second = c.post("/api/auth/reset-password", json={"token": box["raw"], "password": "Other123x"})
        return first.status_code, second.status_code, second.json().get("detail")
    first, second, detail = _run(go())
    assert first == 200 and second == 400
    assert detail == "Недействительная или устаревшая ссылка"


def test_unknown_and_expired_are_identical_neutral_400():
    async def go():
        db = await _new_db()
        await _seed(db, email="u@b.c", reset_raw="known-raw",
                    expires=datetime.utcnow() + timedelta(hours=1))
        await _seed(db, email="e@b.c", reset_raw="expired-raw",
                    expires=datetime.utcnow() - timedelta(minutes=1))
        c = _client(db)
        unknown = c.post("/api/auth/reset-password", json={"token": "no-such-token", "password": GOOD_PW})
        expired = c.post("/api/auth/reset-password", json={"token": "expired-raw", "password": GOOD_PW})
        return unknown, expired
    unknown, expired = _run(go())
    assert unknown.status_code == expired.status_code == 400
    assert unknown.json() == expired.json()      # byte-identical body: no oracle


def test_old_plaintext_token_is_rejected():
    """A pre-3B row that still holds a PLAINTEXT token must not be usable — no raw-or-digest fallback."""
    async def go():
        db = await _new_db()
        uid = str(uuid.uuid4())
        # simulate a legacy row: reset_token holds the RAW token verbatim (not a digest)
        db.add(User(id=uid, email="legacy@b.c", name="S", hashed_password=hash_password("OldPass0rd"),
                    is_verified=True, reset_token="legacy-plaintext-token",
                    reset_token_expires=datetime.utcnow() + timedelta(hours=1)))
        await db.commit()
        r = _client(db).post("/api/auth/reset-password",
                             json={"token": "legacy-plaintext-token", "password": GOOD_PW})
        return r.status_code
    assert _run(go()) == 400      # confirm hashes the input → never equals the stored plaintext


# ══ DB error → 503 (fail-closed) ════════════════════════════════════════════

class _FailOnResetUpdate:
    """Delegates to a real session but raises on the reset UPDATE (never on the throttle insert)."""
    def __init__(self, real):
        self._real = real
    async def execute(self, stmt, *a, **k):
        if "UPDATE users" in str(stmt):
            raise SQLAlchemyError("boom")
        return await self._real.execute(stmt, *a, **k)
    async def commit(self):
        return await self._real.commit()
    async def rollback(self):
        return await self._real.rollback()
    def __getattr__(self, n):
        return getattr(self._real, n)


def test_db_error_on_confirm_is_503():
    async def go():
        real = await _new_db()
        await _seed(real, email="d@b.c", reset_raw="db-raw",
                    expires=datetime.utcnow() + timedelta(hours=1))
        wrapped = _FailOnResetUpdate(real)
        async def _override():
            yield wrapped
        app = FastAPI()
        app.include_router(auth_router.router, prefix="/api/auth")
        app.dependency_overrides[get_db] = _override
        r = TestClient(app).post("/api/auth/reset-password",
                                 json={"token": "db-raw", "password": GOOD_PW})
        # token untouched after the failure
        db2 = real
        db2.expire_all()
        u = (await db2.execute(select(User).where(User.email == "d@b.c"))).scalar_one()
        return r.status_code, u.reset_token
    status, tok = _run(go())
    assert status == 503 and tok == hash_reset_token("db-raw")   # fail-closed, token still valid


# ══ no raw token / password in application logs ═════════════════════════════

def test_raw_token_and_password_absent_from_logs(monkeypatch, caplog):
    box = _capture_email(monkeypatch)

    async def go():
        db = await _new_db()
        await _seed(db, email="log@b.c")
        c = _client(db)
        with caplog.at_level(logging.DEBUG):
            c.post("/api/auth/forgot-password", json={"email": "log@b.c"})
            c.post("/api/auth/reset-password", json={"token": box["raw"], "password": GOOD_PW})
        return box["raw"]
    raw = _run(go())
    app = [r.getMessage() for r in caplog.records
           if not r.name.startswith(("aiosqlite", "sqlalchemy", "asyncio"))]
    blob = " ".join(app)
    assert raw not in blob and GOOD_PW not in blob and "log@b.c" not in blob
