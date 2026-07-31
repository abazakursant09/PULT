"""
SECURITY-2C-2 — real-PostgreSQL atomicity/concurrency proof for the auth throttle.

SQLite (single-writer) cannot prove that N concurrent reserve() calls each count exactly once, that the
sliding-window reset is atomic, or that blocked_until is monotonic under races. These run on real
PostgreSQL 16 in the postgres-explain CI job (skipped locally). They prove, using the SAME service the
routers call: N concurrent failures are counted as N (no lost +1); the block trips exactly at the limit;
the three dimensions (pair/identity/ip) are independent; an identity is caught across rotating IPs and an
IP across rotating emails; unknown and real emails share one bucket (no enumeration); the window resets;
blocked_until never shortens; a successful-login release keeps the abuse evidence; state survives a new
session (restart); and the bounded cleanup removes only fully-expired rows, never an active block.
"""
import asyncio
import os
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401
from models.auth_rate_limit_bucket import AuthRateLimitBucket
from config import settings
from services import auth_throttle as T


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
        rows = (await db.execute(
            select(AuthRateLimitBucket.attempts)
            .where(AuthRateLimitBucket.action == action,
                   AuthRateLimitBucket.dimension == dimension))).scalars().all()
    return rows


# ── A. N concurrent failures are each counted (no lost update) ───────────────
def test_pg_concurrent_failures_all_counted():
    async def go():
        e, S = await _fresh()
        n = settings.auth_throttle_login_pair_limit - 1     # below the block, so all N land as attempts

        async def one():
            async with S() as db:
                await T.reserve(db, "login", identity="race@x.c", ip="10.0.0.1")

        await asyncio.gather(*[one() for _ in range(n)])
        pair = await _attempts(S, "login", "pair")
        await e.dispose()
        return pair
    pair = _run(go())
    # a SELECT→+1→UPDATE would collapse concurrent writers; the atomic upsert must show exactly N.
    assert pair == [settings.auth_throttle_login_pair_limit - 1]


# ── B. the block trips exactly at the limit, not before ──────────────────────
def test_pg_block_boundary_is_exact():
    async def go():
        e, S = await _fresh()
        lim = settings.auth_throttle_login_pair_limit
        results = []
        for _ in range(lim):
            async with S() as db:
                results.append((await T.reserve(db, "login", identity="b@x.c", ip="10.0.0.2")).blocked)
        await e.dispose()
        return results
    res = _run(go())
    assert res[: settings.auth_throttle_login_pair_limit - 1] == [False] * (settings.auth_throttle_login_pair_limit - 1)
    assert res[-1] is True                                   # the limit-th attempt is the block


# ── C. pair vs identity vs ip are independent buckets ────────────────────────
def test_pg_identity_caught_across_rotating_ips():
    async def go():
        e, S = await _fresh()
        # each request from a different IP → every pair bucket stays at 1, but the identity bucket climbs.
        blocked_at = None
        for i in range(settings.auth_throttle_login_identity_limit + 2):
            async with S() as db:
                r = await T.reserve(db, "login", identity="spray@x.c", ip=f"10.9.{i}.5")
            if r.blocked and blocked_at is None:
                blocked_at = i + 1
        pair = await _attempts(S, "login", "pair")
        await e.dispose()
        return blocked_at, pair
    blocked_at, pair = _run(go())
    assert blocked_at == settings.auth_throttle_login_identity_limit    # caught by the identity dimension
    assert all(a == 1 for a in pair)                                    # no single pair ever tripped


def test_pg_ip_caught_across_rotating_emails():
    async def go():
        e, S = await _fresh()
        blocked_at = None
        for i in range(settings.auth_throttle_login_ip_limit + 2):
            async with S() as db:
                r = await T.reserve(db, "login", identity=f"u{i}@x.c", ip="203.0.113.9")
            if r.blocked and blocked_at is None:
                blocked_at = i + 1
        await e.dispose()
        return blocked_at
    assert _run(go()) == settings.auth_throttle_login_ip_limit          # caught by the IP dimension


# ── D. unknown and real emails share one bucket (no enumeration oracle) ──────
def test_pg_unknown_and_real_email_same_bucket():
    async def go():
        e, S = await _fresh()
        async with S() as db:
            await T.reserve(db, "login", identity="Ghost@X.C", ip="10.0.0.3")     # note casing/space norm
        async with S() as db:
            r = await T.reserve(db, "login", identity="  ghost@x.c ", ip="10.0.0.3")
        pair = await _attempts(S, "login", "pair")
        await e.dispose()
        return pair, r
    pair, r = _run(go())
    assert pair == [2]           # the two spellings normalised into ONE bucket → indistinguishable


# ── E. the sliding window resets after it elapses ────────────────────────────
def test_pg_window_resets_after_elapse():
    async def go():
        e, S = await _fresh()
        async with S() as db:
            await T.reserve(db, "login", identity="w@x.c", ip="10.0.0.4")
            await T.reserve(db, "login", identity="w@x.c", ip="10.0.0.4")
        # age the window past the horizon
        old = T._now() - timedelta(seconds=settings.auth_throttle_window_seconds + 5)
        async with S() as db:
            await db.execute(text("UPDATE auth_rate_limit_buckets SET window_started_at = :o WHERE dimension='pair'"),
                             {"o": old})
            await db.commit()
        async with S() as db:
            await T.reserve(db, "login", identity="w@x.c", ip="10.0.0.4")
        pair = await _attempts(S, "login", "pair")
        await e.dispose()
        return pair
    assert _run(go()) == [1]      # reset to a fresh window, not 3


# ── F. blocked_until never shortens under a later attempt ────────────────────
def test_pg_blocked_until_is_monotonic():
    async def go():
        e, S = await _fresh()
        for _ in range(settings.auth_throttle_login_pair_limit):     # trip the block
            async with S() as db:
                await T.reserve(db, "login", identity="m@x.c", ip="10.0.0.5")
        far = T._now() + timedelta(seconds=99999)
        async with S() as db:
            await db.execute(text("UPDATE auth_rate_limit_buckets SET blocked_until = :f WHERE dimension='pair'"),
                             {"f": far})
            await db.commit()
        async with S() as db:                                        # a normal attempt computes a NEARER block
            await T.reserve(db, "login", identity="m@x.c", ip="10.0.0.5")
        async with S() as db:
            bu = (await db.execute(text(
                "SELECT blocked_until FROM auth_rate_limit_buckets WHERE dimension='pair'"))).scalar_one()
        await e.dispose()
        return bu
    bu = T._as_utc(_run(go()))
    assert bu > T._now() + timedelta(seconds=90000)     # kept the far block; not shortened to now+900


# ── G. a successful-login release keeps the abuse evidence (block survives) ──
def test_pg_release_does_not_clear_block():
    async def go():
        e, S = await _fresh()
        for _ in range(settings.auth_throttle_login_pair_limit):     # trip the block
            async with S() as db:
                await T.reserve(db, "login", identity="r@x.c", ip="10.0.0.6")
        async with S() as db:                                        # a correct password now arrives
            await T.release(db, "login", identity="r@x.c", ip="10.0.0.6")
        async with S() as db:
            r = await T.reserve(db, "login", identity="r@x.c", ip="10.0.0.6")
        await e.dispose()
        return r.blocked
    assert _run(go()) is True     # release decremented attempts but the block stands


# ── H. state survives a fresh session (restart / other worker) ───────────────
def test_pg_state_persists_across_sessions():
    async def go():
        e, S = await _fresh()
        async with S() as db:
            await T.reserve(db, "login", identity="p@x.c", ip="10.0.0.7")
        async with S() as db:                       # a brand-new session (as a restarted worker would open)
            await T.reserve(db, "login", identity="p@x.c", ip="10.0.0.7")
        pair = await _attempts(S, "login", "pair")
        await e.dispose()
        return pair
    assert _run(go()) == [2]      # not reset to 1 — the DB is the source of truth, not process memory


# ── I. bounded cleanup removes only fully-expired rows, never an active block ─
def test_pg_cleanup_keeps_active_block_deletes_expired():
    async def go():
        e, S = await _fresh()
        now = T._now()
        async with S() as db:
            # an expired, unblocked row (must go) and an active blocked row (must stay)
            await db.execute(text(
                "INSERT INTO auth_rate_limit_buckets(action,dimension,key_hash,window_started_at,attempts,"
                "blocked_until,updated_at,expires_at) VALUES "
                "('login','pair','dead'||repeat('0',60),:old,1,NULL,:old,:old),"
                "('login','ip','live'||repeat('0',60),:old,9,:future,:now,:future)"),
                {"old": now - timedelta(seconds=10), "future": now + timedelta(seconds=9999), "now": now})
            await db.commit()
        async with S() as db:
            deleted = await T.cleanup(db)
        async with S() as db:
            remaining = (await db.execute(select(AuthRateLimitBucket.dimension))).scalars().all()
        await e.dispose()
        return deleted, remaining
    deleted, remaining = _run(go())
    assert deleted == 1 and remaining == ["ip"]      # expired removed, active block untouched
