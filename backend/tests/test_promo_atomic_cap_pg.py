"""SECURITY-2D-2-A — atomic promo activation cap on REAL PostgreSQL 16 (true cross-connection races).

Proves the aggregate cap cannot be overrun: concurrent activations by DIFFERENT users on a promo with N
free slots yield EXACTLY N successes (never N+1), the counter equals the success count, and each rejected
activation leaves NO row and NO increment. A concurrent SAME-user double-submit yields exactly one
success and a controlled 400 (never a 500). Fault injection proves activation + counter are one atomic
transaction (a reserve/commit failure persists nothing) and that an unexpected IntegrityError is not
masked as "already used". Skipped locally; runs in postgres-explain CI (0 skip there).
"""
import asyncio
import os
import types
import uuid

import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

import routers.promo as promo
from models.promo_code import PromoCode, PromoCodeActivation


def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_async_url():
    return (_pg_sync_url() or "").replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


def _pg_alembic_url():
    return os.environ.get("PULT_TEST_PG_ALEMBIC_URL") or _pg_async_url()


pytestmark = pytest.mark.skipif(
    not (_pg_sync_url() or "").startswith("postgres"),
    reason="BLOCKED_ENVIRONMENT: no PostgreSQL; runs in postgres-explain CI.")

_SCHEMA_READY = False


def _ensure_schema(monkeypatch):
    global _SCHEMA_READY
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    import sqlalchemy as sa
    if not _SCHEMA_READY:
        from alembic import command
        import db_migrations as dbm
        eng = sa.create_engine(_pg_sync_url())
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(dbm._alembic_config(), "head")
        eng.dispose()
        _SCHEMA_READY = True
    eng = sa.create_engine(_pg_sync_url())
    with eng.begin() as c:
        c.exec_driver_sql("TRUNCATE promo_codes, promo_code_activations CASCADE")
    eng.dispose()


def _session():
    eng = create_async_engine(_pg_async_url())
    return eng, sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _seed(Session, *, code, max_activations, current=0):
    async with Session() as s:
        s.add(PromoCode(id=str(uuid.uuid4()), code=code, type="percent", value=10.0,
                        applicable_plans="all", max_activations=max_activations,
                        current_activations=current, is_active=True))
        await s.commit()


async def _apply(Session, uid, code, *, wrap=None):
    async with Session() as s:
        db = wrap(s) if wrap else s
        return await promo.apply_promo(promo.ApplyRequest(code=code, plan="master"),
                                       db=db, current_user=types.SimpleNamespace(id=uid))


async def _counter(Session, code):
    async with Session() as s:
        return (await s.execute(select(PromoCode.current_activations)
                                .where(PromoCode.code == code))).scalar_one()


async def _acts(Session, code):
    async with Session() as s:
        pid = (await s.execute(select(PromoCode.id).where(PromoCode.code == code))).scalar_one()
        return (await s.execute(select(func.count()).select_from(PromoCodeActivation)
                                .where(PromoCodeActivation.promo_id == pid))).scalar_one()


def _classify(results):
    """(#success, #http400, #http503, #other) from a gather(return_exceptions=True) list."""
    ok = sum(1 for r in results if isinstance(r, dict) and r.get("ok"))
    h400 = sum(1 for r in results if isinstance(r, promo.HTTPException) and r.status_code == 400)
    h503 = sum(1 for r in results if isinstance(r, promo.HTTPException) and r.status_code == 503)
    other = len(results) - ok - h400 - h503
    return ok, h400, h503, other


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── fault-injection session proxy ──────────────────────────────────────────────────

class _Fault:
    """Delegates to a real AsyncSession but raises at one chosen point to prove atomicity."""

    def __init__(self, inner, mode):
        self._inner = inner
        self._mode = mode

    def add(self, *a, **k):
        return self._inner.add(*a, **k)

    async def flush(self, *a, **k):
        if self._mode == "flush_integrity":
            raise IntegrityError("stmt", {}, Exception("injected non-unique integrity error"))
        return await self._inner.flush(*a, **k)

    async def execute(self, stmt, *a, **k):
        if self._mode == "reserve_fail" and stmt is promo._CAP_RESERVE:
            raise OperationalError("stmt", {}, Exception("injected reserve failure"))
        return await self._inner.execute(stmt, *a, **k)

    async def commit(self):
        return await self._inner.commit()

    async def rollback(self):
        return await self._inner.rollback()

    def __getattr__(self, n):
        return getattr(self._inner, n)


# ── 1. normal ────────────────────────────────────────────────────────────────────

def test_pg_normal_success(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="OK", max_activations=5)
            r = await _apply(S, "u1", "OK")
            assert r["ok"] is True
            assert await _acts(S, "OK") == 1
            assert await _counter(S, "OK") == 1
        finally:
            await eng.dispose()
    _run(go())


# ── 2. concurrent different users, one free slot ───────────────────────────────────

def test_pg_concurrent_two_users_one_slot(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="ONE", max_activations=1)
            res = await asyncio.gather(_apply(S, "a", "ONE"), _apply(S, "b", "ONE"),
                                       return_exceptions=True)
            ok, h400, h503, other = _classify(res)
            assert (ok, h400, h503, other) == (1, 1, 0, 0)   # exactly one winner, one cap-reject
            assert await _acts(S, "ONE") == 1                 # loser left NO activation row
            assert await _counter(S, "ONE") == 1             # counter never exceeded the cap
        finally:
            await eng.dispose()
    _run(go())


# ── 3. concurrent[10] different users, cap 3 ───────────────────────────────────────

def test_pg_concurrent_ten_users_cap_three(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="CAP3", max_activations=3)
            res = await asyncio.gather(*[_apply(S, f"u{i}", "CAP3") for i in range(10)],
                                       return_exceptions=True)
            ok, h400, h503, other = _classify(res)
            assert ok == 3                                    # successes == free slots
            assert h400 == 7                                  # rest controlled 400
            assert h503 == 0 and other == 0                   # no 500 / no unexpected
            assert await _acts(S, "CAP3") == 3                # exact activation delta
            assert await _counter(S, "CAP3") == 3             # exact counter delta, == cap
        finally:
            await eng.dispose()
    _run(go())


# ── 4. concurrent same user ────────────────────────────────────────────────────────

def test_pg_concurrent_same_user_two(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="SAME", max_activations=100)
            res = await asyncio.gather(_apply(S, "solo", "SAME"), _apply(S, "solo", "SAME"),
                                       return_exceptions=True)
            ok, h400, h503, other = _classify(res)
            assert (ok, h400, h503, other) == (1, 1, 0, 0)   # one success, one duplicate 400 (never 500)
            assert await _acts(S, "SAME") == 1
            assert await _counter(S, "SAME") == 1
        finally:
            await eng.dispose()
    _run(go())


def test_pg_concurrent_same_user_ten(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="SAME10", max_activations=100)
            res = await asyncio.gather(*[_apply(S, "solo", "SAME10") for _ in range(10)],
                                       return_exceptions=True)
            ok, h400, h503, other = _classify(res)
            assert ok == 1 and h503 == 0 and other == 0       # exactly one success, no 500
            assert h400 == 9
            assert await _acts(S, "SAME10") == 1
            assert await _counter(S, "SAME10") == 1
        finally:
            await eng.dispose()
    _run(go())


# ── 5. unlimited ───────────────────────────────────────────────────────────────────

def test_pg_unlimited_accepts_all(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="INF", max_activations=None)
            res = await asyncio.gather(*[_apply(S, f"u{i}", "INF") for i in range(8)],
                                       return_exceptions=True)
            ok, h400, h503, other = _classify(res)
            assert ok == 8 and h400 == 0 and h503 == 0 and other == 0
            assert await _acts(S, "INF") == 8
            assert await _counter(S, "INF") == 8
        finally:
            await eng.dispose()
    _run(go())


# ── 6. already full ────────────────────────────────────────────────────────────────

def test_pg_already_full_rejects(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="FULL", max_activations=1, current=1)
            res = await asyncio.gather(_apply(S, "late", "FULL"), return_exceptions=True)
            ok, h400, h503, other = _classify(res)
            assert (ok, h400) == (0, 1)
            assert await _acts(S, "FULL") == 0
            assert await _counter(S, "FULL") == 1
        finally:
            await eng.dispose()
    _run(go())


# ── 7/9. reserve/commit failure → nothing persists, retry then works ───────────────

def test_pg_reserve_failure_persists_nothing_then_retry(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="FAIL", max_activations=5)
            with pytest.raises(promo.HTTPException) as ei:
                await _apply(S, "u1", "FAIL", wrap=lambda s: _Fault(s, "reserve_fail"))
            assert ei.value.status_code == 503
            assert await _acts(S, "FAIL") == 0        # activation flush rolled back with the reserve
            assert await _counter(S, "FAIL") == 0     # counter untouched
            # a clean retry (no fault) now succeeds
            r = await _apply(S, "u1", "FAIL")
            assert r["ok"] is True
            assert await _acts(S, "FAIL") == 1
            assert await _counter(S, "FAIL") == 1
        finally:
            await eng.dispose()
    _run(go())


# ── 11. unexpected IntegrityError is NOT masked as duplicate ────────────────────────

def test_pg_unexpected_integrity_not_masked(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="INTEG", max_activations=5)
            with pytest.raises(promo.HTTPException) as ei:
                await _apply(S, "u1", "INTEG", wrap=lambda s: _Fault(s, "flush_integrity"))
            assert ei.value.status_code == 503       # NOT 400 "already used" — no real uq_promo_user hit
            assert await _acts(S, "INTEG") == 0
            assert await _counter(S, "INTEG") == 0
        finally:
            await eng.dispose()
    _run(go())


# ── 10. lost-ACK: retry after a real commit → duplicate 400, no second effect ──────

def test_pg_retry_after_success_is_duplicate_no_second_effect(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="RETRY", max_activations=5)
            await _apply(S, "u1", "RETRY")                    # first, really commits
            with pytest.raises(promo.HTTPException) as ei:
                await _apply(S, "u1", "RETRY")                # client retried a lost ACK
            assert ei.value.status_code == 400                # honest duplicate response
            assert await _acts(S, "RETRY") == 1               # no second activation
            assert await _counter(S, "RETRY") == 1            # counter not incremented twice
        finally:
            await eng.dispose()
    _run(go())


# ── 12. cross-user isolation ────────────────────────────────────────────────────────

def test_pg_cross_user_isolation(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _session()

    async def go():
        try:
            await _seed(S, code="ISO", max_activations=5)
            await _apply(S, "a", "ISO")
            # user b never applied → has no activation for this promo
            async with S() as s:
                pid = (await s.execute(select(PromoCode.id).where(PromoCode.code == "ISO"))).scalar_one()
                n_b = (await s.execute(
                    select(func.count()).select_from(PromoCodeActivation)
                    .where(PromoCodeActivation.promo_id == pid,
                           PromoCodeActivation.user_id == "b"))).scalar_one()
            assert n_b == 0
            assert await _counter(S, "ISO") == 1
        finally:
            await eng.dispose()
    _run(go())
