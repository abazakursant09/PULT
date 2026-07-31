"""
SECURITY-2C-1 — real-PostgreSQL concurrency proof for token_version revocation.

SQLite cannot prove atomicity of the `token_version = token_version + 1` increment under concurrent
writers, so these run on real PostgreSQL 16 in the postgres-explain CI job (skipped locally). They prove:
two concurrent logouts never lose an update (final version reflects BOTH), a bumped version rejects the
old JWT via the real get_current_user, a reset bump revokes the old session while a new one works, and a
DB failure fails closed as 503.
"""
import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401
from models.user import User
from routers.auth import hash_password, create_access_token
from dependencies import get_current_user

PW = "Passw0rdOk"
COOKIE = "pult_session_dev"   # app_env defaults to test → dev cookie name


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def _pg_engine_or_skip():
    url = os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")
    if not url or not url.startswith("postgres"):
        pytest.skip("BLOCKED_ENVIRONMENT: no PostgreSQL (PULT_TEST_PG_URL unset); runs on real "
                    "PostgreSQL 16 in the postgres-explain CI job.")
    return create_async_engine(
        url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://"))


async def _reset_and_seed(e, ver=0, deleted=False):
    async with e.begin() as c:
        await c.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        await c.exec_driver_sql("CREATE SCHEMA public")
        await c.run_sync(Base.metadata.create_all)
    S = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)
    uid = str(uuid.uuid4())
    async with S() as db:
        from datetime import datetime
        db.add(User(id=uid, email=f"{uid}@x.c", name="S", hashed_password=hash_password(PW),
                    is_verified=True, token_version=ver,
                    deleted_at=datetime.utcnow() if deleted else None))
        await db.commit()
    return S, uid


def _req(token):
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": [(b"cookie", f"{COOKIE}={token}".encode())]})


async def _bump(S, uid):
    # the exact atomic SQL the logout/reset paths use
    async with S() as db:
        await db.execute(update(User).where(User.id == uid).values(token_version=User.token_version + 1))
        await db.commit()


# ── A. two concurrent logouts: no lost update, no 500 ────────────────────────
def test_pg_concurrent_logout_no_lost_update():
    async def go():
        e = _pg_engine_or_skip()
        S, uid = await _reset_and_seed(e, ver=0)
        await asyncio.gather(_bump(S, uid), _bump(S, uid))    # both must apply
        async with S() as db:
            v = (await db.execute(select(User.token_version).where(User.id == uid))).scalar_one()
        await e.dispose()
        return v
    # a Python read-modify-write would collapse to 1; the atomic SQL increment yields 2.
    assert _run(go()) == 2


# ── B. request vs logout: after the bump commits, the old JWT is rejected ─────
def test_pg_old_jwt_rejected_after_bump_new_accepted():
    async def go():
        e = _pg_engine_or_skip()
        S, uid = await _reset_and_seed(e, ver=0)
        old = create_access_token(uid, 0)
        async with S() as db:                       # old JWT valid before logout
            u = await get_current_user(_req(old), db)
            assert u.id == uid
        await _bump(S, uid)                          # logout
        async with S() as db:
            try:
                await get_current_user(_req(old), db)
                return "ACCEPTED_OLD"               # must not happen
            except HTTPException as ex:
                assert ex.status_code == 401
        new = create_access_token(uid, 1)           # a fresh login issues ver=1
        async with S() as db:
            u2 = await get_current_user(_req(new), db)
            assert u2.id == uid
        await e.dispose()
        return "OK"
    assert _run(go()) == "OK"


# ── C. reset revokes old session while a new one works (same atomic primitive) ─
def test_pg_reset_bump_revokes_old_session():
    async def go():
        e = _pg_engine_or_skip()
        S, uid = await _reset_and_seed(e, ver=7)
        old = create_access_token(uid, 7)
        await _bump(S, uid)                          # reset bump → 8
        async with S() as db:
            try:
                await get_current_user(_req(old), db); return "OLD_OK"
            except HTTPException as ex:
                assert ex.status_code == 401
            u = await get_current_user(_req(create_access_token(uid, 8)), db)
            assert u.id == uid
        await e.dispose()
        return "OK"
    assert _run(go()) == "OK"


# ── D. DB failure fails closed as 503 (never a false 401, never fail-open) ────
def test_pg_db_failure_is_503():
    async def go():
        e = _pg_engine_or_skip()
        S, uid = await _reset_and_seed(e, ver=0)
        tok = create_access_token(uid, 0)
        await e.dispose()                           # engine gone → any query errors
        async with S() as db:
            try:
                await get_current_user(_req(tok), db)
                return "NO_ERROR"
            except HTTPException as ex:
                return ex.status_code
    assert _run(go()) == 503
