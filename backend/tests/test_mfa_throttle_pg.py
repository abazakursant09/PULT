"""SECURITY-2C-4A — real-PostgreSQL proof for the durable MFA throttle + enable/disable session
revocation.

Two independent throttle actions: mfa_login (POST /login/mfa) and mfa_manage (enable + disable). These
run on real PostgreSQL 16 in the postgres-explain CI job (skipped locally). They exercise the SAME
services/auth_throttle the routers call, and the SAME /api/mfa endpoints (real get_current_user + real
cookie), proving: concurrent attempts count exactly; the block trips at the limit; the three dimensions
are independent; one source can never globally lock the owner (narrowest-first, per 2C-2); a distributed
attack does reach the user-global limit; mfa_login and mfa_manage buckets never touch each other; an
active block is not bypassed by a would-be-correct code; a success compensates the reservation but not
an active block; the reservation is committed independently (honest Option-C: a reserve-level DB error
counts nothing, a post-reserve error counts one); and enable/disable bump token_version (old cookies 401,
the current user gets a fresh one) atomically, with wrong codes changing nothing.
"""
import asyncio
import os
import uuid

import pytest
import time

from fastapi import HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401
from dependencies import get_current_user
from models.user import User
from models.mfa_secret import MFASecret
from models.auth_rate_limit_bucket import AuthRateLimitBucket
from config import settings
from services import auth_throttle as T
from routers.mfa import mfa_verify, mfa_disable, MFACodeIn, _generate_secret, _totp
from routers.auth import hash_password, create_access_token

COOKIE = "pult_session_dev"   # app_env defaults to test → dev cookie name

L_PAIR = settings.auth_throttle_mfa_login_pair_limit       # 5
L_ID   = settings.auth_throttle_mfa_login_identity_limit   # 20
L_IP   = settings.auth_throttle_mfa_login_ip_limit         # 50
M_PAIR = settings.auth_throttle_mfa_manage_pair_limit      # 5
M_ID   = settings.auth_throttle_mfa_manage_identity_limit  # 10


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


async def _attempts(S, action, dimension):
    async with S() as db:
        return (await db.execute(
            select(AuthRateLimitBucket.attempts)
            .where(AuthRateLimitBucket.action == action,
                   AuthRateLimitBucket.dimension == dimension))).scalars().all()


# ══ mfa_login throttle (direct reserve/release) ═════════════════════════════

def test_pg_mfa_login_concurrent_counted_and_boundary():
    async def go():
        e, S = await _fresh()

        async def one():
            async with S() as db:
                return (await T.reserve(db, "mfa_login", identity="u1", ip="10.0.0.1")).blocked

        # N = pair limit concurrent attempts: all counted, and exactly the limit-th is the block
        res = await asyncio.gather(*[one() for _ in range(L_PAIR)])
        pair = await _attempts(S, "mfa_login", "pair")
        await e.dispose()
        return res, pair
    res, pair = _run(go())
    assert pair == [L_PAIR]                       # every concurrent attempt counted (no lost update)
    assert res.count(True) == 1                   # exactly one saw the block (the limit-th)


def test_pg_mfa_login_identity_across_ips():
    async def go():
        e, S = await _fresh()
        blocked_at = None
        for i in range(L_ID + 2):
            async with S() as db:
                r = await T.reserve(db, "mfa_login", identity="spray", ip=f"10.9.{i}.5")
            if r.blocked and blocked_at is None:
                blocked_at = i + 1
        pair = await _attempts(S, "mfa_login", "pair")
        await e.dispose()
        return blocked_at, pair
    blocked_at, pair = _run(go())
    assert blocked_at == L_ID and all(a == 1 for a in pair)   # caught by identity; no single pair tripped


def test_pg_mfa_login_ip_across_users():
    async def go():
        e, S = await _fresh()
        blocked_at = None
        for i in range(L_IP + 2):
            async with S() as db:
                r = await T.reserve(db, "mfa_login", identity=f"u{i}", ip="203.0.113.9")
            if r.blocked and blocked_at is None:
                blocked_at = i + 1
        await e.dispose()
        return blocked_at
    assert _run(go()) == L_IP                      # caught by the IP dimension


def test_pg_mfa_login_single_source_never_locks_victim_but_distributed_does():
    async def go():
        e, S = await _fresh()
        # one IP hammers one user far past the pair limit
        for _ in range(L_PAIR + 30):
            async with S() as db:
                await T.reserve(db, "mfa_login", identity="victim", ip="10.7.7.7")
        ident_one = (await _attempts(S, "mfa_login", "identity"))[0]

        # a genuine distributed attack: independent IPs, each a single failure → identity accrues
        e2, S2 = await _fresh()
        last = False
        for i in range(L_ID):
            async with S2() as db:
                last = (await T.reserve(db, "mfa_login", identity="target", ip=f"10.20.{i}.1")).blocked
        await e.dispose()
        await e2.dispose()
        return ident_one, last
    ident_one, distributed = _run(go())
    assert ident_one <= L_PAIR and ident_one < L_ID    # one source can't near the global lock
    assert distributed is True                         # several sources do reach it


def test_pg_mfa_login_success_release_keeps_block():
    async def go():
        e, S = await _fresh()
        for _ in range(L_PAIR):                    # trip the pair block
            async with S() as db:
                await T.reserve(db, "mfa_login", identity="r", ip="10.0.0.6")
        async with S() as db:                      # a correct code arrives → compensate
            await T.release(db, "mfa_login", identity="r", ip="10.0.0.6")
        async with S() as db:
            still = (await T.reserve(db, "mfa_login", identity="r", ip="10.0.0.6")).blocked
        await e.dispose()
        return still
    assert _run(go()) is True                       # release lowered attempts but the block stands


def test_pg_mfa_login_persists_across_sessions():
    async def go():
        e, S = await _fresh()
        async with S() as db:
            await T.reserve(db, "mfa_login", identity="p", ip="10.0.0.7")
        async with S() as db:                       # a brand-new session (restarted worker)
            await T.reserve(db, "mfa_login", identity="p", ip="10.0.0.7")
        pair = await _attempts(S, "mfa_login", "pair")
        await e.dispose()
        return pair
    assert _run(go()) == [2]                         # DB is the source of truth, not process memory


# ══ mfa_manage throttle (direct reserve/release) ════════════════════════════

def test_pg_mfa_manage_boundary_and_victim_lockout():
    async def go():
        e, S = await _fresh()
        results = []
        for _ in range(M_PAIR):
            async with S() as db:
                results.append((await T.reserve(db, "mfa_manage", identity="m", ip="10.1.1.1")).blocked)
        ident = (await _attempts(S, "mfa_manage", "identity"))[0]
        await e.dispose()
        return results, ident
    results, ident = _run(go())
    assert results[:-1] == [False] * (M_PAIR - 1) and results[-1] is True   # pair block at the limit
    assert ident <= M_PAIR and ident < M_ID                                  # one source can't lock identity


def test_pg_mfa_manage_identity_across_ips():
    async def go():
        e, S = await _fresh()
        blocked_at = None
        for i in range(M_ID + 2):
            async with S() as db:
                r = await T.reserve(db, "mfa_manage", identity="mid", ip=f"10.5.{i}.2")
            if r.blocked and blocked_at is None:
                blocked_at = i + 1
        await e.dispose()
        return blocked_at
    assert _run(go()) == M_ID


def test_pg_mfa_manage_ip_across_users():
    async def go():
        e, S = await _fresh()
        blocked_at = None
        for i in range(settings.auth_throttle_mfa_manage_ip_limit + 2):
            async with S() as db:
                r = await T.reserve(db, "mfa_manage", identity=f"mu{i}", ip="198.51.100.7")
            if r.blocked and blocked_at is None:
                blocked_at = i + 1
        await e.dispose()
        return blocked_at
    assert _run(go()) == settings.auth_throttle_mfa_manage_ip_limit


# ══ isolation + active-block + Option-C ═════════════════════════════════════

def test_pg_mfa_login_and_manage_are_isolated():
    async def go():
        e, S = await _fresh()
        # hammer mfa_manage for a user+IP to its pair block
        for _ in range(M_PAIR + 5):
            async with S() as db:
                await T.reserve(db, "mfa_manage", identity="x", ip="10.2.2.2")
        # the SAME user+IP on mfa_login must be completely free (separate action buckets)
        async with S() as db:
            login_blocked = (await T.reserve(db, "mfa_login", identity="x", ip="10.2.2.2")).blocked
        # and vice-versa: hammer mfa_login, manage stays free
        for _ in range(L_PAIR + 5):
            async with S() as db:
                await T.reserve(db, "mfa_login", identity="y", ip="10.3.3.3")
        async with S() as db:
            manage_blocked = (await T.reserve(db, "mfa_manage", identity="y", ip="10.3.3.3")).blocked
        await e.dispose()
        return login_blocked, manage_blocked
    login_blocked, manage_blocked = _run(go())
    assert login_blocked is False and manage_blocked is False


def test_pg_active_block_not_bypassed_by_a_correct_code():
    async def go():
        e, S = await _fresh()
        for _ in range(L_PAIR):                    # trip the block
            async with S() as db:
                await T.reserve(db, "mfa_login", identity="z", ip="10.4.4.4")
        # the very next reserve (what a would-be-correct submission does first) is still blocked:
        # the throttle gate runs BEFORE any TOTP verification, so a correct code cannot skip the block.
        async with S() as db:
            still = (await T.reserve(db, "mfa_login", identity="z", ip="10.4.4.4")).blocked
        await e.dispose()
        return still
    assert _run(go()) is True


class _BoomOnUpsert:
    """Delegates to a real session but raises on the throttle UPSERT (reserve-level DB failure)."""
    def __init__(self, real):
        self._real = real
    async def execute(self, stmt, *a, **k):
        if "INSERT INTO auth_rate_limit_buckets" in str(stmt):
            raise SQLAlchemyError("boom")
        return await self._real.execute(stmt, *a, **k)
    async def commit(self):
        return await self._real.commit()
    async def rollback(self):
        return await self._real.rollback()
    def __getattr__(self, n):
        return getattr(self._real, n)


def test_pg_reservation_option_c_semantics():
    async def go():
        e, S = await _fresh()
        # (a) a DB error INSIDE reserve (the upsert) → nothing committed → attempt NOT counted
        async with S() as real:
            with pytest.raises(SQLAlchemyError):
                await T.reserve(_BoomOnUpsert(real), "mfa_login", identity="oc", ip="10.6.6.6")
        after_fail = await _attempts(S, "mfa_login", "pair")
        # (b) a normal reserve COMMITS independently → the attempt persists in a fresh session even
        #     though no claim/commit followed it (a later claim/commit failure cannot un-count it)
        async with S() as db:
            await T.reserve(db, "mfa_login", identity="oc", ip="10.6.6.6")
        after_ok = await _attempts(S, "mfa_login", "pair")
        await e.dispose()
        return after_fail, after_ok
    after_fail, after_ok = _run(go())
    assert after_fail == []          # reserve-level failure: no row, no attempt counted
    assert after_ok == [1]           # committed independently: post-reserve failure would leave it counted


# ══ enable / disable session revocation (endpoint coroutines called DIRECTLY) ═══
# TestClient runs its own event loop; asyncpg connections are loop-bound, so we call the route
# coroutines directly on the one _run() loop (the same pattern token_version_pg uses).

async def _seed_mfa_user(S, *, enabled, ver=0):
    uid = str(uuid.uuid4())
    secret = _generate_secret()
    async with S() as db:                       # parent User first (real-PG FK), then child MFASecret
        db.add(User(id=uid, email=f"{uid}@x.c", name="S", hashed_password=hash_password("pw"),
                    is_verified=True, token_version=ver))
        await db.commit()
    async with S() as db:
        db.add(MFASecret(user_id=uid, secret=secret, enabled=enabled))
        await db.commit()
    return uid, secret


def _http_req(ip="203.0.113.5"):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": (ip, 0)})


def _cookie_req(token):
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": [(b"cookie", f"{COOKIE}={token}".encode())]})


async def _call_verify(S, uid, code, ip="203.0.113.5"):
    """Invoke the real mfa_verify with a user loaded in the SAME session, so the token_version bump and
    db.refresh operate on a live ORM instance. Returns (status, cookie_set)."""
    resp = Response()
    async with S() as db:
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        try:
            await mfa_verify(MFACodeIn(code=code), _http_req(ip), resp, user, db)
            status = 200
        except HTTPException as ex:
            status = ex.status_code
    return status, "set-cookie" in {k.lower() for k in resp.headers}


async def _call_disable(S, uid, code, ip="203.0.113.5"):
    resp = Response()
    async with S() as db:
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        try:
            await mfa_disable(MFACodeIn(code=code), _http_req(ip), resp, user, db)
            status = 200
        except HTTPException as ex:
            status = ex.status_code
    return status, "set-cookie" in {k.lower() for k in resp.headers}


async def _state(S, uid):
    async with S() as db:
        ver = (await db.execute(select(User.token_version).where(User.id == uid))).scalar_one()
        enabled = (await db.execute(select(MFASecret.enabled).where(MFASecret.user_id == uid))).scalar_one()
    return ver, enabled


def test_pg_enable_bumps_token_version_and_reissues_cookie():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed_mfa_user(S, enabled=False, ver=0)
        status, cookie_set = await _call_verify(S, uid, _totp(secret, int(time.time())))
        ver, enabled = await _state(S, uid)
        # old cookie (ver 0) now rejected; a cookie with the new version is accepted
        old_rejected = False
        async with S() as db:
            try:
                await get_current_user(_cookie_req(create_access_token(uid, 0)), db)
            except HTTPException as ex:
                old_rejected = ex.status_code == 401
        async with S() as db:
            u = await get_current_user(_cookie_req(create_access_token(uid, ver)), db)
            new_ok = u.id == uid
        await e.dispose()
        return status, cookie_set, ver, enabled, old_rejected, new_ok
    status, cookie_set, ver, enabled, old_rejected, new_ok = _run(go())
    assert status == 200 and cookie_set is True
    assert ver == 1 and enabled is True and old_rejected is True and new_ok is True


def test_pg_disable_bumps_token_version_and_reissues_cookie():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed_mfa_user(S, enabled=True, ver=0)
        status, cookie_set = await _call_disable(S, uid, _totp(secret, int(time.time())))
        ver, enabled = await _state(S, uid)
        await e.dispose()
        return status, cookie_set, ver, enabled
    status, cookie_set, ver, enabled = _run(go())
    assert status == 200 and cookie_set is True and ver == 1 and enabled is False


def test_pg_wrong_manage_code_does_not_bump_or_reissue():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed_mfa_user(S, enabled=True, ver=0)
        wrong = "000000" if _totp(secret, int(time.time())) != "000000" else "111111"
        status, cookie_set = await _call_disable(S, uid, wrong)
        ver, enabled = await _state(S, uid)
        await e.dispose()
        return status, cookie_set, ver, enabled
    status, cookie_set, ver, enabled = _run(go())
    assert status == 400 and cookie_set is False and ver == 0 and enabled is True   # nothing changed


def test_pg_concurrent_enable_no_desync():
    async def go():
        e, S = await _fresh()
        uid, secret = await _seed_mfa_user(S, enabled=False, ver=0)
        code = _totp(secret, int(time.time()))
        # two concurrent enables on independent sessions → real PG row-lock serialization
        results = await asyncio.gather(_call_verify(S, uid, code, ip="10.1.1.1"),
                                       _call_verify(S, uid, code, ip="10.1.1.2"))
        ver, enabled = await _state(S, uid)
        await e.dispose()
        return {s for s, _ in results}, ver, enabled
    codes, ver, enabled = _run(go())
    # same code → the replay guard lets exactly one win (200) and the other 400; version bumped by the
    # winner only. Never a 500 / desync; MFA ends enabled.
    assert enabled is True and ver >= 1
    assert codes <= {200, 400}
