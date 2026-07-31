"""SECURITY-2C-3A — real-PostgreSQL atomicity/concurrency proof for the MFA TOTP replay guard.

SQLite is single-writer and cannot prove that two concurrent verifies of the SAME code produce exactly
one winner under real row locking. These run on real PostgreSQL 16 in the postgres-explain CI job
(skipped locally). They exercise the SAME claim_totp_step the routers call: two racing claims of one
code → one success; a spent step is never re-accepted (replay / prior step); the next step advances; a
rollback before commit does not burn the step; an uncommitted claim is invisible to other sessions (no
session granted before commit); a DB error is fail-closed (HTTP 503); enable races to one winner; a
replayed code cannot disable MFA.
"""
import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401  registers tables
from models.user import User
from models.mfa_secret import MFASecret
from routers.mfa import claim_totp_step, _totp, _generate_secret

_NOW0 = 1_000_000_020        # divisible by 30 → clean step boundary


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


async def _seed(S, *, enabled=True, last_step=None):
    uid = str(uuid.uuid4())
    secret = _generate_secret()
    async with S() as db:
        db.add(User(id=uid, email=f"{uid}@ex.c", name="S", hashed_password="h", is_verified=True))
        db.add(MFASecret(user_id=uid, secret=secret, enabled=enabled, last_totp_step=last_step))
        await db.commit()
    return uid, secret


async def _step(S, uid):
    async with S() as db:
        return (await db.execute(select(MFASecret.last_totp_step)
                                 .where(MFASecret.user_id == uid))).scalar_one()


# ── A. two concurrent verifies of ONE code → exactly one success ─────────────
def test_pg_two_concurrent_verify_one_success():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed(S)
        code = _totp(secret, _NOW0)

        async def one():
            async with S() as db:
                ok = await claim_totp_step(db, user_id=uid, secret=secret, code=code, now=_NOW0)
                await db.commit()
                return ok

        results = await asyncio.gather(one(), one())
        step = await _step(S, uid)
        await e.dispose()
        return results, step
    results, step = _run(go())
    assert sum(1 for r in results if r) == 1      # exactly one winner under real row locking
    assert step == _NOW0 // 30


# ── B. a spent step is never re-accepted (replay) ────────────────────────────
def test_pg_same_step_replay_rejected():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed(S)
        code = _totp(secret, _NOW0)
        async with S() as db:
            first = await claim_totp_step(db, user_id=uid, secret=secret, code=code, now=_NOW0)
            await db.commit()
        async with S() as db:
            again = await claim_totp_step(db, user_id=uid, secret=secret, code=code, now=_NOW0)
            await db.commit()
        await e.dispose()
        return first, again
    first, again = _run(go())
    assert first is True and again is False


# ── C. a prior step after a newer one is rejected ────────────────────────────
def test_pg_prior_step_after_newer_rejected():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed(S)
        async with S() as db:
            await claim_totp_step(db, user_id=uid, secret=secret,
                                  code=_totp(secret, _NOW0), now=_NOW0)
            await db.commit()
        async with S() as db:
            prior = await claim_totp_step(db, user_id=uid, secret=secret,
                                          code=_totp(secret, _NOW0 - 30), now=_NOW0)
            await db.commit()
        await e.dispose()
        return prior
    assert _run(go()) is False


# ── D. the next step advances ────────────────────────────────────────────────
def test_pg_next_step_accepted():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed(S)
        async with S() as db:
            await claim_totp_step(db, user_id=uid, secret=secret,
                                  code=_totp(secret, _NOW0), now=_NOW0)
            await db.commit()
        async with S() as db:
            nxt = await claim_totp_step(db, user_id=uid, secret=secret,
                                        code=_totp(secret, _NOW0 + 30), now=_NOW0)
            await db.commit()
        step = await _step(S, uid)
        await e.dispose()
        return nxt, step
    nxt, step = _run(go())
    assert nxt is True and step == (_NOW0 + 30) // 30


# ── E. rollback before commit does NOT burn the step ─────────────────────────
def test_pg_rollback_before_commit_does_not_burn_step():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed(S)
        code = _totp(secret, _NOW0)
        async with S() as db:
            ok = await claim_totp_step(db, user_id=uid, secret=secret, code=code, now=_NOW0)
            await db.rollback()                       # abort before commit
        after_rollback = await _step(S, uid)
        async with S() as db:                         # same code still usable → step was not burned
            retry = await claim_totp_step(db, user_id=uid, secret=secret, code=code, now=_NOW0)
            await db.commit()
        await e.dispose()
        return ok, after_rollback, retry
    ok, after_rollback, retry = _run(go())
    assert ok is True and after_rollback is None and retry is True


# ── F. an uncommitted claim is invisible to other sessions (no early session) ─
def test_pg_uncommitted_claim_not_visible_to_others():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed(S)
        async with S() as a:
            await claim_totp_step(a, user_id=uid, secret=secret,
                                  code=_totp(secret, _NOW0), now=_NOW0)   # NOT committed
            async with S() as b:
                seen = (await b.execute(select(MFASecret.last_totp_step)
                                        .where(MFASecret.user_id == uid))).scalar_one()
            await a.commit()
        await e.dispose()
        return seen
    assert _run(go()) is None      # another session sees no spent step until the claim commits


# ── G. DB error is fail-closed → HTTP 503 (never a false "wrong code") ────────
def test_pg_db_error_is_503():
    class _Boom:
        def __init__(self): self.rolled_back = False
        async def execute(self, *a, **k): raise SQLAlchemyError("boom")
        async def rollback(self): self.rolled_back = True

    async def go():
        s = _generate_secret()
        boom = _Boom()
        with pytest.raises(HTTPException) as ei:
            await claim_totp_step(boom, user_id="u", secret=s, code=_totp(s, _NOW0), now=_NOW0)
        return ei.value.status_code, boom.rolled_back
    status, rolled = _run(go())
    assert status == 503 and rolled is True


# ── H. concurrent ENABLE races to exactly one winner (claim+flip in one txn) ──
def test_pg_enable_concurrent_one_success():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed(S, enabled=False)
        code = _totp(secret, _NOW0)

        async def enable_once():
            async with S() as db:
                rec = (await db.execute(select(MFASecret)
                                        .where(MFASecret.user_id == uid))).scalar_one()
                if rec.enabled:
                    return False
                if not await claim_totp_step(db, user_id=uid, secret=secret, code=code, now=_NOW0):
                    return False
                rec.enabled = True
                await db.commit()
                return True

        results = await asyncio.gather(enable_once(), enable_once())
        async with S() as db:
            enabled = (await db.execute(select(MFASecret.enabled)
                                        .where(MFASecret.user_id == uid))).scalar_one()
        await e.dispose()
        return results, enabled
    results, enabled = _run(go())
    assert sum(1 for r in results if r) == 1 and enabled is True


# ── I. a replayed (already-spent) code cannot DISABLE MFA ─────────────────────
def test_pg_disable_replay_rejected():
    async def go():
        e, S = await _fresh()
        # last spent step is ahead of the current code's step → the code is a replay
        uid, secret = await _seed(S, enabled=True, last_step=_NOW0 // 30 + 5)
        async with S() as db:
            rec = (await db.execute(select(MFASecret)
                                    .where(MFASecret.user_id == uid))).scalar_one()
            accepted = await claim_totp_step(db, user_id=uid, secret=secret,
                                             code=_totp(secret, _NOW0), now=_NOW0)
            if accepted:
                rec.enabled = False
            await db.commit()
        async with S() as db:
            enabled = (await db.execute(select(MFASecret.enabled)
                                        .where(MFASecret.user_id == uid))).scalar_one()
        await e.dispose()
        return accepted, enabled
    accepted, enabled = _run(go())
    assert accepted is False and enabled is True      # replay refused; MFA stays on
