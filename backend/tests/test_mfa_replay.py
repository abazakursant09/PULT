"""SECURITY-2C-3A — MFA TOTP replay guard (pure function + SQLite integration).

verify_totp_step returns the exact matched step (newest on collision) and NEVER writes; claim_totp_step
reserves that step atomically so a code authenticates at most once. These cover the contract on SQLite
and via the HTTP endpoints; the real-PostgreSQL concurrency proof (two racing verifies of one code →
exactly one winner) is in test_mfa_replay_pg.py.
"""
import asyncio
import logging
import time
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import rate_limit
from database import Base, get_db
from dependencies import get_current_user
import models  # noqa: F401  registers tables
from models.user import User
from models.mfa_secret import MFASecret
from routers import auth as auth_router
from routers import mfa as mfa_mod
from routers.mfa import verify_totp_step, claim_totp_step, _totp, _generate_secret
from routers.auth import create_mfa_pending_token, hash_password

_LOOP = asyncio.new_event_loop()
_NOW0 = 1_000_000_020            # divisible by 30 → clean step boundaries


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_mfa(db, *, enabled=True, last_step=None):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@ex.c", name="S", hashed_password=hash_password("pw"),
                is_verified=True))
    secret = _generate_secret()
    db.add(MFASecret(user_id=uid, secret=secret, enabled=enabled, last_totp_step=last_step))
    await db.commit()
    return uid, secret


async def _step_of(db, uid):
    row = (await db.execute(select(MFASecret.last_totp_step)
                            .where(MFASecret.user_id == uid))).scalar_one()
    return row


# ══ pure verify_totp_step ═══════════════════════════════════════════════════

def test_valid_current_step():
    s = _generate_secret()
    assert verify_totp_step(s, _totp(s, _NOW0), now=_NOW0) == _NOW0 // 30


def test_prior_step_in_window():
    s = _generate_secret()
    assert verify_totp_step(s, _totp(s, _NOW0 - 30), now=_NOW0) == (_NOW0 - 30) // 30


def test_future_step_in_window():
    s = _generate_secret()
    assert verify_totp_step(s, _totp(s, _NOW0 + 30), now=_NOW0) == (_NOW0 + 30) // 30


def test_wrong_code_matches_nothing():
    s = _generate_secret()
    bad = "000000" if _totp(s, _NOW0) != "000000" else "111111"
    assert verify_totp_step(s, bad, now=_NOW0) is None


@pytest.mark.parametrize("bad", ["", "abc", "12", "1234567", "  ", "notacode"])
def test_malformed_code_rejected(bad):
    s = _generate_secret()
    assert verify_totp_step(s, bad, now=_NOW0) is None


def test_out_of_window_step_not_matched():
    s = _generate_secret()
    # a code from two steps ago (−60 s) is outside the ±1 window
    assert verify_totp_step(s, _totp(s, _NOW0 - 60), now=_NOW0) is None


def test_collision_returns_maximum_step(monkeypatch):
    # Force the SAME code to be valid for two steps in the window and prove the NEWER step wins.
    monkeypatch.setattr(mfa_mod, "_totp", lambda secret, ts: "123456" if ts >= _NOW0 else "999999")
    # window = {_NOW0-30, _NOW0, _NOW0+30}; matches at _NOW0 and _NOW0+30 → max is (_NOW0+30)//30
    assert verify_totp_step("x", "123456", now=_NOW0) == (_NOW0 + 30) // 30


def test_pure_function_writes_nothing_and_is_deterministic():
    s = _generate_secret()
    c = _totp(s, _NOW0)
    assert verify_totp_step(s, c, now=_NOW0) == verify_totp_step(s, c, now=_NOW0)


# ══ CHECK constraint ════════════════════════════════════════════════════════

def test_check_rejects_negative_step():
    async def go():
        db = await _new_db()
        db.add(User(id="u1", email="c@ex.c", name="S", hashed_password="h", is_verified=True))
        db.add(MFASecret(user_id="u1", secret="s", enabled=True, last_totp_step=-1))
        with pytest.raises(IntegrityError):
            await db.commit()
    _run(go())


def test_null_initial_state_allowed():
    async def go():
        db = await _new_db()
        uid, _ = await _seed_mfa(db)
        assert await _step_of(db, uid) is None
    _run(go())


# ══ claim_totp_step (atomic reserve, SQLite) ════════════════════════════════

def test_claim_accepts_then_replay_and_prior_rejected_next_accepted():
    async def go():
        db = await _new_db()
        uid, secret = await _seed_mfa(db)
        cur = _totp(secret, _NOW0)

        ok = await claim_totp_step(db, user_id=uid, secret=secret, code=cur, now=_NOW0)
        await db.commit()
        assert ok is True and await _step_of(db, uid) == _NOW0 // 30

        # same code again → step already spent → replay rejected, step unchanged
        replay = await claim_totp_step(db, user_id=uid, secret=secret, code=cur, now=_NOW0)
        await db.commit()
        assert replay is False and await _step_of(db, uid) == _NOW0 // 30

        # a code from the prior step → older than last → rejected
        prior = await claim_totp_step(db, user_id=uid, secret=secret,
                                      code=_totp(secret, _NOW0 - 30), now=_NOW0)
        await db.commit()
        assert prior is False and await _step_of(db, uid) == _NOW0 // 30

        # the next step's code → newer → accepted, advances
        nxt = await claim_totp_step(db, user_id=uid, secret=secret,
                                    code=_totp(secret, _NOW0 + 30), now=_NOW0)
        await db.commit()
        assert nxt is True and await _step_of(db, uid) == (_NOW0 + 30) // 30
    _run(go())


def test_claim_wrong_code_is_false_without_touching_step():
    async def go():
        db = await _new_db()
        uid, secret = await _seed_mfa(db)
        bad = "000000" if _totp(secret, _NOW0) != "000000" else "111111"
        res = await claim_totp_step(db, user_id=uid, secret=secret, code=bad, now=_NOW0)
        await db.commit()
        assert res is False and await _step_of(db, uid) is None
    _run(go())


def test_claim_db_error_raises_503_and_rolls_back():
    class _Boom:
        def __init__(self): self.rolled_back = False
        async def execute(self, *a, **k): raise SQLAlchemyError("boom")
        async def rollback(self): self.rolled_back = True

    async def go():
        s = _generate_secret()
        boom = _Boom()
        with pytest.raises(HTTPException) as ei:
            await claim_totp_step(boom, user_id="u", secret=s, code=_totp(s, _NOW0), now=_NOW0)
        assert ei.value.status_code == 503 and boom.rolled_back is True
    _run(go())


# ══ HTTP integration ════════════════════════════════════════════════════════

def _fresh_limiter(monkeypatch):
    monkeypatch.setattr(rate_limit, "_limiter", rate_limit._SlidingWindow())


def _auth_client(db):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def _mfa_client(db, user):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(mfa_mod.router, prefix="/api/mfa")
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_login_valid_then_same_code_replay_rejected_no_cookie(monkeypatch):
    _fresh_limiter(monkeypatch)

    async def go():
        db = await _new_db()
        uid, secret = await _seed_mfa(db)
        return db, uid, secret
    db, uid, secret = _run(go())
    c = _auth_client(db)
    token = create_mfa_pending_token(uid)
    code = _totp(secret, int(time.time()))

    ok = c.post("/api/auth/login/mfa", json={"mfa_token": token, "code": code})
    assert ok.status_code == 200, ok.text
    assert "set-cookie" in {k.lower() for k in ok.headers}

    replay = c.post("/api/auth/login/mfa", json={"mfa_token": token, "code": code})
    assert replay.status_code == 401                                   # step already spent
    assert "set-cookie" not in {k.lower() for k in replay.headers}     # no session on replay


def test_login_db_error_returns_503_no_cookie(monkeypatch):
    _fresh_limiter(monkeypatch)

    async def go():
        db = await _new_db()
        uid, secret = await _seed_mfa(db)
        return db, uid, secret
    db, uid, secret = _run(go())

    async def _boom(*a, **k):
        raise HTTPException(status_code=503, detail="x")
    monkeypatch.setattr(auth_router, "claim_totp_step", _boom)

    c = _auth_client(db)
    token = create_mfa_pending_token(uid)
    r = c.post("/api/auth/login/mfa", json={"mfa_token": token, "code": "123456"})
    assert r.status_code == 503
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_enable_then_login_same_code_is_replay(monkeypatch):
    # The code spent to ENABLE cannot be reused for an immediate login (cross-path replay guard).
    _fresh_limiter(monkeypatch)

    async def go():
        db = await _new_db()
        uid, secret = await _seed_mfa(db, enabled=False)
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        return db, uid, secret, user
    db, uid, secret, user = _run(go())

    code = _totp(secret, int(time.time()))
    mc = _mfa_client(db, user)
    en = mc.post("/api/mfa/verify", json={"code": code})
    assert en.status_code == 200, en.text

    ac = _auth_client(db)
    token = create_mfa_pending_token(uid)
    r = ac.post("/api/auth/login/mfa", json={"mfa_token": token, "code": code})
    assert r.status_code == 401                                        # same step already consumed


def test_disable_with_already_spent_code_rejected(monkeypatch):
    # A code whose step is <= the last spent step cannot turn MFA off; MFA stays enabled.
    async def go():
        db = await _new_db()
        # seed as if the current step was already used
        uid, secret = await _seed_mfa(db, enabled=True, last_step=int(time.time()) // 30 + 5)
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        return db, uid, secret, user
    db, uid, secret, user = _run(go())

    mc = _mfa_client(db, user)
    r = mc.request("DELETE", "/api/mfa/disable", json={"code": _totp(secret, int(time.time()))})
    assert r.status_code == 400
    still = _run(_step_of(db, uid))
    assert still == int(time.time()) // 30 + 5                          # unchanged

    async def _check():
        rec = (await db.execute(select(MFASecret).where(MFASecret.user_id == uid))).scalar_one()
        return rec.enabled
    assert _run(_check()) is True                                       # still enabled


def test_secret_code_step_absent_from_logs(monkeypatch, caplog):
    _fresh_limiter(monkeypatch)

    async def go():
        db = await _new_db()
        uid, secret = await _seed_mfa(db)
        return db, uid, secret
    db, uid, secret = _run(go())
    c = _auth_client(db)
    token = create_mfa_pending_token(uid)
    code = _totp(secret, int(time.time()))
    with caplog.at_level(logging.DEBUG):
        c.post("/api/auth/login/mfa", json={"mfa_token": token, "code": code})
    # APPLICATION logs only — exclude the DB driver's own DEBUG SQL-param trace (aiosqlite/sqlalchemy),
    # which necessarily carries bind parameters and is never enabled in production.
    app = [r.getMessage() for r in caplog.records
           if not r.name.startswith(("aiosqlite", "sqlalchemy", "asyncio"))]
    blob = " ".join(app)
    assert code not in blob and secret not in blob
    assert str(int(time.time()) // 30) not in blob      # our logging never emits the step counter
