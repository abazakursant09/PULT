"""The TOTP seed is stored encrypted at rest (Fernet, via services/mfa_crypto), not plaintext.
These tests prove the stored value is not the seed, that verification still round-trips through
the whole /setup → /verify flow, and that a legacy plaintext row keeps working (backward compat).
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
from types import SimpleNamespace

from database import Base, get_db
from dependencies import get_current_user
import models  # registers tables
from models.mfa_secret import MFASecret
from routers import mfa as mfa_router
from routers.mfa import _generate_secret, _totp
from services.mfa_crypto import store_secret, load_secret

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _client(db, uid, email="s@example.com"):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(mfa_router.router, prefix="/api/auth/mfa")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid, email=email)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def _valid_code(secret):
    return _totp(secret, int(time.time()))


# ── unit: the crypto helper itself ───────────────────────────────────────────

def test_store_secret_is_not_plaintext_and_round_trips():
    seed = _generate_secret()
    stored = store_secret(seed)
    assert stored != seed
    assert seed not in stored              # the seed does not appear anywhere in the ciphertext
    assert stored.startswith("gAAAAA")     # Fernet token marker
    assert load_secret(stored) == seed


def test_load_secret_falls_back_to_legacy_plaintext():
    # A pre-change row holds the bare base32 seed. It is not a Fernet token, so load returns it.
    legacy = _generate_secret()
    assert load_secret(legacy) == legacy


# ── flow: setup persists ciphertext, verify still works ──────────────────────

def test_setup_stores_ciphertext_and_verify_succeeds():
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    c = _client(db, uid)

    setup = c.post("/api/auth/mfa/setup")
    assert setup.status_code == 200
    seed = setup.json()["secret"]

    # What landed in the DB is NOT the seed.
    row = _run(db.execute(select(MFASecret).where(MFASecret.user_id == uid))).scalar_one()
    assert row.secret != seed
    assert row.secret.startswith("gAAAAA")

    # And the seed still verifies through the real endpoint.
    ok = c.post("/api/auth/mfa/verify", json={"code": _valid_code(seed)})
    assert ok.status_code == 200, ok.text
    assert _run(db.get(MFASecret, row.id)).enabled is True


def test_legacy_plaintext_row_still_verifies():
    # Simulate a user enrolled before this change: plaintext seed already in the DB, enabled.
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    seed = _generate_secret()
    _run(_add(db, MFASecret(user_id=uid, secret=seed, enabled=True)))
    c = _client(db, uid)

    # disable takes a valid code — proves the read path decrypts-with-fallback correctly.
    r = c.request("DELETE", "/api/auth/mfa/disable", json={"code": _valid_code(seed)})
    assert r.status_code == 200, r.text


async def _add(db, obj):
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
