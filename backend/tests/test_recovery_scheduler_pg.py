"""SECURITY-2D-1C-D — scheduler wiring on REAL PostgreSQL 16.

Proves the WIRING-specific guarantees (not the sweeps' own behaviour, which their suites cover): the
reconciliation and re-own sweeps take DISTINCT advisory locks and therefore run in parallel WITHOUT
blocking each other, they write only their own disjoint allowlist columns of the SAME stuck row, and they
never touch status / dispatch_started_at / attempt_count / manual_resolution and never issue a provider
write. Skipped locally; runs in postgres-explain CI (0 skip there).
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

_V1 = "v1:review:3f2504e0-4f89-41d3-9a0c-0305e82c3301"
_FP = "fp1:" + "a" * 64


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
        c.exec_driver_sql("TRUNCATE execution_logs CASCADE")
    eng.dispose()


def _install(monkeypatch, *, dry_run=False):
    """Point BOTH sweeps at one real-PG engine and enable both features; stub the read-only reconcile
    verdict so no provider network is touched; count provider writes (must stay 0)."""
    from config import settings
    from services.marketplace.recovery import recovery_sweep as rc
    from services.marketplace.recovery import reown_sweep as rw
    from services.marketplace.recovery import reconcile_read
    monkeypatch.setattr(settings, "recovery_reaper_enabled", True)
    monkeypatch.setattr(settings, "recovery_reaper_dry_run", dry_run)
    monkeypatch.setattr(settings, "recovery_reown_enabled", True)
    monkeypatch.setattr(settings, "recovery_reown_dry_run", dry_run)
    eng = create_async_engine(_pg_async_url())
    Session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(rc, "engine", eng)
    monkeypatch.setattr(rc, "AsyncSessionLocal", Session)
    monkeypatch.setattr(rw, "engine", eng)
    monkeypatch.setattr(rw, "AsyncSessionLocal", Session)

    async def _observe(db, row):
        return reconcile_read.STILL_UNKNOWN                # read-only verdict, no provider call
    monkeypatch.setattr(reconcile_read, "observe", _observe)
    return rc, rw, eng, Session


def _stub_provider(monkeypatch):
    from services.marketplace.wb_client import wb_client
    cnt = {"n": 0}

    async def _fake(*a, **k):
        cnt["n"] += 1
        return {"api_request_id": "x"}
    monkeypatch.setattr(wb_client, "publish_feedback_answer", _fake)
    return cnt


async def _seed(Session, *, age_s=3600, uid="u1"):
    from models.execution_log import ExecutionLog
    rid = str(uuid.uuid4())
    async with Session() as db:
        db.add(ExecutionLog(
            id=rid, user_id=uid, action_type="publish_review_response", mode="manual_l3", payload={},
            status="pending", idempotency_key=_V1, request_fingerprint=_FP, claim_generation=0,
            reown_count=0, attempt_count=0,
            created_at=datetime.utcnow() - timedelta(seconds=age_s),
            dispatch_started_at=None))
        await db.commit()
    return rid


async def _row(Session, rid):
    from models.execution_log import ExecutionLog
    async with Session() as db:
        return (await db.execute(select(ExecutionLog).where(ExecutionLog.id == rid))).scalars().first()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── distinct advisory-lock op-codes (retention / reconcile / re-own never collide) ──

def test_pg_three_sweeps_have_distinct_advisory_ops():
    from services.marketplace.recovery import recovery_sweep as rc
    from services.marketplace.recovery import reown_sweep as rw
    from services.marketplace.retention import observation_sweep as obs
    ns = {rc._LOCK_NAMESPACE, rw._LOCK_NAMESPACE, obs._LOCK_NAMESPACE}
    ops = [rc._LOCK_OPERATION, rw._LOCK_OPERATION, obs._LOCK_OPERATION]
    assert ns == {0x50554C54}                       # one shared "PULT" namespace
    assert len(set(ops)) == 3                        # three DISTINCT operation codes -> never block


# ── reconcile + re-own run concurrently: both acquire, disjoint columns, safe fields ──

def test_pg_concurrent_reconcile_and_reown_disjoint_no_block(monkeypatch):
    # schema + engine + stubs are set up SYNCHRONOUSLY (alembic's async env uses asyncio.run, which cannot
    # run inside a running loop) — only the DB work runs under _run.
    _ensure_schema(monkeypatch)
    rc, rw, eng, Session = _install(monkeypatch, dry_run=False)
    prov = _stub_provider(monkeypatch)

    async def go():
        try:
            rid = await _seed(Session)
            r_res, o_res = await asyncio.gather(
                rc.run_recovery_sweep(dry_run=False, now=None, max_duration=rc.DEFAULT_MAX_DURATION_SECONDS),
                rw.run_reown_sweep(dry_run=False, now=None, max_duration=rw.DEFAULT_MAX_DURATION_SECONDS))
            # DISTINCT advisory locks -> both ran, neither blocked the other out
            assert r_res.enabled and r_res.lock_acquired
            assert o_res.enabled and o_res.lock_acquired
            row = await _row(Session, rid)
            # reconcile wrote ONLY its allowlist
            assert row.reconciliation_status == "still_unknown"
            assert row.reconciliation_attempts == 1
            assert row.last_reconciled_at is not None
            # re-own wrote ONLY its allowlist
            assert row.claim_generation == 1
            assert row.reown_count == 1
            assert row.last_reowned_at is not None
            # NEITHER touched the fenced/terminal fields — re-own leaves the claim safe-pending
            assert row.status == "pending"
            assert row.dispatch_started_at is None
            assert row.attempt_count == 0
            assert row.manual_resolution is None
            # ZERO provider writes in either path
            assert prov["n"] == 0
        finally:
            await eng.dispose()
    _run(go())


def test_pg_dry_run_mutates_nothing(monkeypatch):
    _ensure_schema(monkeypatch)
    rc, rw, eng, Session = _install(monkeypatch, dry_run=True)
    prov = _stub_provider(monkeypatch)

    async def go():
        try:
            rid = await _seed(Session)
            await asyncio.gather(
                rc.run_recovery_sweep(dry_run=True, now=None, max_duration=rc.DEFAULT_MAX_DURATION_SECONDS),
                rw.run_reown_sweep(dry_run=True, now=None, max_duration=rw.DEFAULT_MAX_DURATION_SECONDS))
            row = await _row(Session, rid)
            # dry-run of both sweeps changes NOTHING on the row
            assert row.reconciliation_status is None
            assert row.reconciliation_attempts == 0
            assert row.last_reconciled_at is None
            assert row.claim_generation == 0
            assert row.reown_count == 0
            assert row.last_reowned_at is None
            assert row.status == "pending"
            assert row.dispatch_started_at is None
            assert row.attempt_count == 0
            assert prov["n"] == 0
        finally:
            await eng.dispose()
    _run(go())
