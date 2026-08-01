"""SECURITY-2C-3B — real-PostgreSQL atomicity proof for the one-time password reset.

SQLite is single-writer and cannot prove that two concurrent confirms of ONE reset token produce exactly
one success under real row locking. These run on real PostgreSQL 16 in the postgres-explain CI job
(skipped locally). They drive the SAME `_RESET_CONSUME` statement the endpoint runs: two racing confirms
→ one success / one neutral failure; a reused token is rejected; a rollback keeps the token valid; an
expired token is rejected; a success bumps token_version by exactly one and installs only the winner's
password; and the timestamp-expiry comparison works on PostgreSQL without a type error.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401
from models.user import User
from routers import auth as auth_router
from routers.auth import hash_password, verify_password
from services.reset_token import hash_reset_token

_RAW = "the-one-raw-reset-token-xyz"


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def _pg_engine_or_skip():
    url = os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")
    if not url or not url.startswith("postgres"):
        pytest.skip("BLOCKED_ENVIRONMENT: no PostgreSQL (PULT_TEST_PG_URL unset); runs on real "
                    "PostgreSQL 16 in the postgres-explain CI job.")
    return create_async_engine(
        url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://"))


async def _fresh():
    e = _pg_engine_or_skip()
    async with e.begin() as c:
        await c.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        await c.exec_driver_sql("CREATE SCHEMA public")
        await c.run_sync(Base.metadata.create_all)
    return e, sessionmaker(e, class_=AsyncSession, expire_on_commit=False)


async def _seed(S, *, raw=_RAW, expires_hours=1):
    uid = str(uuid.uuid4())
    async with S() as db:
        db.add(User(id=uid, email=f"{uid}@ex.c", name="S", hashed_password=hash_password("OldPass0rd"),
                    is_verified=False, token_version=0,
                    reset_token=hash_reset_token(raw),
                    reset_token_expires=datetime.utcnow() + timedelta(hours=expires_hours)))
        await db.commit()
    return uid


async def _confirm(S, raw, new_pw):
    """Run the endpoint's atomic reset statement in its own session. True = this call won."""
    async with S() as db:
        row = (await db.execute(auth_router._RESET_CONSUME, {
            "hash": hash_password(new_pw), "digest": hash_reset_token(raw),
            "verified": True, "now": datetime.utcnow(),
        })).first()
        if row is None:
            return False
        await db.commit()
        return True


async def _user(S, uid):
    async with S() as db:
        return (await db.execute(select(User).where(User.id == uid))).scalar_one()


# ── A. two concurrent confirms of one token → exactly one success ────────────
def test_pg_concurrent_confirm_one_success():
    async def go():
        e, S = await _fresh()
        uid = await _seed(S)
        results = await asyncio.gather(_confirm(S, _RAW, "WinnerPass1"), _confirm(S, _RAW, "LoserPass2"))
        u = await _user(S, uid)
        await e.dispose()
        return results, u.token_version, u.reset_token
    results, ver, tok = _run(go())
    assert sum(1 for r in results if r) == 1        # exactly one success under real row locking
    assert ver == 1 and tok is None                 # bumped once, token consumed


# ── A′. the surviving password is the winner's only; version bumped once ─────
def test_pg_success_bumps_token_version_and_password():
    async def go():
        e, S = await _fresh()
        uid = await _seed(S)
        results = await asyncio.gather(_confirm(S, _RAW, "WinnerPass1"), _confirm(S, _RAW, "LoserPass2"))
        u = await _user(S, uid)
        await e.dispose()
        winner = "WinnerPass1" if results[0] else "LoserPass2"
        loser = "LoserPass2" if results[0] else "WinnerPass1"
        return u.token_version, verify_password(winner, u.hashed_password), verify_password(loser, u.hashed_password)
    ver, winner_ok, loser_ok = _run(go())
    assert ver == 1 and winner_ok is True and loser_ok is False


# ── B. reuse after success is rejected, no further change ────────────────────
def test_pg_reuse_after_success_rejected():
    async def go():
        e, S = await _fresh()
        uid = await _seed(S)
        first = await _confirm(S, _RAW, "FirstPass1")
        second = await _confirm(S, _RAW, "SecondPass2")
        u = await _user(S, uid)
        await e.dispose()
        return first, second, u.token_version, verify_password("FirstPass1", u.hashed_password), \
            verify_password("SecondPass2", u.hashed_password)
    first, second, ver, first_ok, second_ok = _run(go())
    assert first is True and second is False
    assert ver == 1 and first_ok is True and second_ok is False


# ── C. rollback before commit keeps the token valid ──────────────────────────
def test_pg_rollback_keeps_token_valid():
    async def go():
        e, S = await _fresh()
        uid = await _seed(S)
        async with S() as db:
            row = (await db.execute(auth_router._RESET_CONSUME, {
                "hash": hash_password("Aborted1"), "digest": hash_reset_token(_RAW),
                "verified": True, "now": datetime.utcnow(),
            })).first()
            assert row is not None
            await db.rollback()                       # abort
        mid = await _user(S, uid)
        retry = await _confirm(S, _RAW, "RetryPass1")  # token was not burned
        after = await _user(S, uid)
        await e.dispose()
        return mid.reset_token, mid.token_version, retry, after.token_version, \
            verify_password("RetryPass1", after.hashed_password)
    tok, ver_mid, retry, ver_after, pw_ok = _run(go())
    assert tok == hash_reset_token(_RAW) and ver_mid == 0     # untouched after rollback
    assert retry is True and ver_after == 1 and pw_ok is True


# ── D. an expired token is rejected, nothing changes ─────────────────────────
def test_pg_expired_token_rejected():
    async def go():
        e, S = await _fresh()
        uid = await _seed(S, expires_hours=-1)         # already expired
        ok = await _confirm(S, _RAW, "NopePass1")
        u = await _user(S, uid)
        await e.dispose()
        return ok, u.token_version, u.reset_token, verify_password("OldPass0rd", u.hashed_password)
    ok, ver, tok, old_ok = _run(go())
    assert ok is False and ver == 0 and tok == hash_reset_token(_RAW) and old_ok is True


# ── F. the expiry comparison works on PostgreSQL (no text/timestamp error) ───
def test_pg_expiry_comparison_works():
    async def go():
        e, S = await _fresh()
        uid = await _seed(S, expires_hours=1)          # valid, unexpired
        ok = await _confirm(S, _RAW, "GoodPass1")
        u = await _user(S, uid)
        await e.dispose()
        return ok, u.reset_token, verify_password("GoodPass1", u.hashed_password)
    ok, tok, pw_ok = _run(go())
    assert ok is True and tok is None and pw_ok is True
