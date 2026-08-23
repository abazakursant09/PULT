"""SECURITY-2C-3C — the login_attempts writer and model are gone (guard + regression).

The write-only login_attempts table (plaintext email + IP, zero readers) was removed. These prove the
model no longer exists, production auth code neither imports it nor writes it, the auth flows still work
without it (a leftover INSERT would hit a missing table), and no auth flow leaks email/IP to the logs.
The real-PostgreSQL DROP proof is in test_login_attempts_drop_pg.py.
"""
import asyncio
import importlib
import logging
import pathlib
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base, get_db
from routers import auth as auth_router
from routers.auth import hash_password

_LOOP = asyncio.new_event_loop()
ORIGIN = "http://localhost:3000"


def _run(c):
    return _LOOP.run_until_complete(c)


# ══ guard: the model / table are gone ═══════════════════════════════════════

def test_login_attempts_table_not_registered():
    assert "login_attempts" not in Base.metadata.tables


def test_login_attempt_model_module_is_removed():
    try:
        importlib.import_module("models.login_attempt")
        assert False, "models.login_attempt should no longer exist"
    except ModuleNotFoundError:
        pass


def test_models_package_has_no_login_attempt_export():
    assert not hasattr(models, "LoginAttempt")


def test_auth_source_has_no_writer_or_import():
    src = pathlib.Path(auth_router.__file__).read_text(encoding="utf-8")
    assert "LoginAttempt" not in src
    assert "login_attempts" not in src
    assert "async def _log(" not in src
    assert "await _log(" not in src


# ══ regression: auth flows work WITHOUT the table ═══════════════════════════

async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)   # builds every registered table — NOT login_attempts
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _client(db):
    async def _override():
        yield db
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


async def _seed_user(db, *, email, pw="Passw0rd", verified=True):
    from models.user import User
    db.add(User(id=str(uuid.uuid4()), email=email, name="S", hashed_password=hash_password(pw),
                is_verified=verified, token_version=0))
    await db.commit()


def test_login_flows_work_without_login_attempts_table():
    """A leftover write would raise 'no such table: login_attempts' — its absence proves the writer is gone."""
    db = _run(_new_db())
    _run(_seed_user(db, email="ok@b.c"))
    c = _client(db)
    # unknown email → neutral 401 (used to INSERT a failed attempt)
    r_unknown = c.post("/api/auth/login", json={"email": "nobody@b.c", "password": "x"},
                       headers={"origin": ORIGIN})
    # wrong password on a real account → 401 (used to INSERT a failed attempt)
    r_wrong = c.post("/api/auth/login", json={"email": "ok@b.c", "password": "wrong"},
                     headers={"origin": ORIGIN})
    # correct login → 200 (used to INSERT a success attempt)
    r_ok = c.post("/api/auth/login", json={"email": "ok@b.c", "password": "Passw0rd"},
                  headers={"origin": ORIGIN})
    assert r_unknown.status_code == 401 and r_wrong.status_code == 401
    assert r_ok.status_code == 200                    # no missing-table error anywhere


def test_register_works_without_login_attempts_table(monkeypatch):
    async def _sent(*a, **k):
        return True
    monkeypatch.setattr(auth_router, "send_verification_email", _sent)
    db = _run(_new_db())
    c = _client(db)
    r = c.post("/api/auth/register",
               json={"email": "new@b.c", "name": "N", "password": "Passw0rd", "consent": True},
               headers={"origin": ORIGIN})
    assert r.status_code == 201


def test_auth_flows_do_not_log_email_or_ip(monkeypatch, caplog):
    async def _sent(*a, **k):
        return True
    monkeypatch.setattr(auth_router, "send_verification_email", _sent)
    db = _run(_new_db())
    _run(_seed_user(db, email="log@b.c"))
    c = _client(db)
    with caplog.at_level(logging.DEBUG):
        c.post("/api/auth/login", json={"email": "log@b.c", "password": "wrong"},
               headers={"origin": ORIGIN, "x-forwarded-for": "203.0.113.55"})
        c.post("/api/auth/register", json={"email": "reg@b.c", "name": "N", "password": "Passw0rd", "consent": True},
               headers={"origin": ORIGIN})
    app = [r.getMessage() for r in caplog.records
           if not r.name.startswith(("aiosqlite", "sqlalchemy", "asyncio"))]
    blob = " ".join(app)
    assert "log@b.c" not in blob and "reg@b.c" not in blob and "203.0.113.55" not in blob
