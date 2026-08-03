"""SECURITY-2D-1C-C2 — controlled re-own on REAL PostgreSQL 16 (true cross-connection concurrency).

Guarantee: the re-own sweep ONLY transfers ownership of a stuck safe pending claim (generation+1) via an
atomic CAS; two re-owners → one winner; an old worker holding the previous generation is then fenced by
the 1C-C1 CAS; a genuine lock conflict rolls the batch back and the next continues; the read-only
reconciliation sweep and re-own use distinct advisory locks and run in parallel. ZERO provider and ZERO
executor calls in every path. Skipped locally; runs in postgres-explain CI (0 skip there).
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from sqlalchemy import select, text
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


def _install(monkeypatch, *, enabled=True, dry_run=False):
    from config import settings
    from services.marketplace.recovery import reown_sweep as rw
    monkeypatch.setattr(settings, "recovery_reown_enabled", enabled)
    monkeypatch.setattr(settings, "recovery_reown_dry_run", dry_run)
    eng = create_async_engine(_pg_async_url())
    Session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(rw, "engine", eng)
    monkeypatch.setattr(rw, "AsyncSessionLocal", Session)
    return rw, eng, Session


async def _seed(Session, *, rid=None, status="pending", dsa=False, key=_V1, fp=_FP, gen=0, reown=0,
                attempt=0, age_s=3600, uid="u1"):
    from models.execution_log import ExecutionLog
    rid = rid or str(uuid.uuid4())
    async with Session() as db:
        db.add(ExecutionLog(
            id=rid, user_id=uid, action_type="publish_review_response", mode="manual_l3", payload={},
            status=status, idempotency_key=key, request_fingerprint=fp, claim_generation=gen,
            reown_count=reown, attempt_count=attempt,
            created_at=datetime.utcnow() - timedelta(seconds=age_s),
            dispatch_started_at=datetime.now(timezone.utc) if dsa else None))
        await db.commit()
    return rid


async def _row(Session, rid):
    from models.execution_log import ExecutionLog
    async with Session() as db:
        return (await db.execute(select(ExecutionLog).where(ExecutionLog.id == rid))).scalars().first()


def _stub_provider(monkeypatch):
    """Count any provider write — must remain 0 in every re-own path."""
    from services.marketplace.wb_client import wb_client
    cnt = {"n": 0}

    async def _fake(*a, **k):
        cnt["n"] += 1
        return {"api_request_id": "x"}
    monkeypatch.setattr(wb_client, "publish_feedback_answer", _fake)
    return cnt


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# 1. flag OFF → nothing
def test_pg_reown_flag_off_zero(monkeypatch):
    _ensure_schema(monkeypatch); prov = _stub_provider(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=False)

    async def go():
        try:
            rid = await _seed(S)
            r = await rw.run_reown_sweep()
            assert r.enabled is False and r.reowned == 0 and r.lock_acquired is False
            row = await _row(S, rid); assert row.claim_generation == 0 and prov["n"] == 0
        finally:
            await eng.dispose()
    _run(go())


# 2. dry-run → count, no mutation
def test_pg_reown_dry_run_no_mutation(monkeypatch):
    _ensure_schema(monkeypatch); prov = _stub_provider(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=True)

    async def go():
        try:
            rid = await _seed(S)
            r = await rw.run_reown_sweep()
            assert r.eligible == 1 and r.reowned == 0
            assert (await _row(S, rid)).claim_generation == 0 and prov["n"] == 0
        finally:
            await eng.dispose()
    _run(go())


# 3. stale pending → generation+1 / reown+1 / timestamp; attempt & reconciliation untouched
def test_pg_reown_stale_pending_transferred(monkeypatch):
    _ensure_schema(monkeypatch); prov = _stub_provider(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)

    async def go():
        try:
            rid = await _seed(S, attempt=2)
            r = await rw.run_reown_sweep()
            assert r.reowned == 1 and prov["n"] == 0
            row = await _row(S, rid)
            assert row.claim_generation == 1 and row.reown_count == 1 and row.last_reowned_at is not None
            assert row.status == "pending" and row.dispatch_started_at is None and row.attempt_count == 2
            assert row.reconciliation_status is None
        finally:
            await eng.dispose()
    _run(go())


# 4/5/6. fresh / dsa-set / non-pending → 0
@pytest.mark.parametrize("kw", [dict(age_s=5), dict(dsa=True), dict(status="in_flight", dsa=True),
                                dict(status="ambiguous", dsa=True), dict(status="success", dsa=True),
                                dict(status="reverted", dsa=True), dict(status="rejected")])
def test_pg_reown_ineligible_zero(monkeypatch, kw):
    _ensure_schema(monkeypatch); _stub_provider(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)

    async def go():
        try:
            rid = await _seed(S, **kw)
            r = await rw.run_reown_sweep()
            assert r.reowned == 0
            assert (await _row(S, rid)).claim_generation == 0
        finally:
            await eng.dispose()
    _run(go())


# 7. two concurrent re-owners on the SAME row → exactly one winner (generation=:seen CAS)
def test_pg_reown_two_concurrent_one_winner(monkeypatch):
    _ensure_schema(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)

    async def go():
        try:
            rid = await _seed(S, gen=0)
            now = datetime.now(timezone.utc)
            cut_naive = datetime.utcnow() + timedelta(seconds=60)   # everything is stale vs this
            cut_tz = now + timedelta(seconds=60)

            async def cas():
                async with S() as db:
                    res = await db.execute(rw._REOWN_CAS, {"id": rid, "uid": "u1", "seen": 0, "max": 5,
                                                           "now": now, "cut_naive": cut_naive, "cut_tz": cut_tz})
                    won = res.fetchone() is not None
                    await db.commit()
                    return won
            wins = await asyncio.gather(cas(), cas())
            assert sum(1 for w in wins if w) == 1              # exactly one CAS won
            assert (await _row(S, rid)).claim_generation == 1  # bumped exactly once
        finally:
            await eng.dispose()
    _run(go())


# 8/9. after transfer, the OLD generation is fenced by the C1 CAS; the NEW generation can still win it
def test_pg_reown_old_worker_fenced_new_passes_c1(monkeypatch):
    _ensure_schema(monkeypatch); prov = _stub_provider(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)
    from services.marketplace.executor import _FENCE_CAS

    async def go():
        try:
            rid = await _seed(S, gen=0)
            await rw.run_reown_sweep()                         # generation 0 → 1
            assert (await _row(S, rid)).claim_generation == 1
            now = datetime.now(timezone.utc)
            async with S() as db:                              # old worker (owned gen 0) → C1 CAS empty
                old = (await db.execute(_FENCE_CAS, {"id": rid, "now": now, "gen": 0})).first()
                await db.commit()
            assert old is None and prov["n"] == 0
            assert (await _row(S, rid)).status == "pending"    # still un-dispatched, no in_flight
            async with S() as db:                              # new owner (gen 1) can take it (harness only)
                new = (await db.execute(_FENCE_CAS, {"id": rid, "now": now, "gen": 1})).first()
                await db.commit()
            assert new is not None
            assert (await _row(S, rid)).status == "in_flight"
        finally:
            await eng.dispose()
    _run(go())


# 11. a real row lock → the batch hits lock_timeout, rolls back, next continues; row unchanged
def test_pg_reown_lock_timeout_rolls_back(monkeypatch):
    _ensure_schema(monkeypatch); prov = _stub_provider(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)
    monkeypatch.setattr(rw, "_LOCK_TIMEOUT", "150ms")

    async def go():
        engA = create_async_engine(_pg_async_url()); SA = sessionmaker(engA, class_=AsyncSession, expire_on_commit=False)
        holder = None
        try:
            rid = await _seed(S)
            holder = SA()
            await holder.execute(text("SELECT id FROM execution_logs WHERE id=:id FOR UPDATE"), {"id": rid})
            r = await rw.run_reown_sweep()
            assert r.reowned == 0 and r.failed_batches >= 1 and prov["n"] == 0
            assert (await _row(S, rid)).claim_generation == 0   # untouched
        finally:
            if holder is not None:
                await holder.rollback(); await holder.close()
            await engA.dispose(); await eng.dispose()
    _run(go())


# 12. max_reowns boundary → 0
def test_pg_reown_max_boundary(monkeypatch):
    _ensure_schema(monkeypatch); _stub_provider(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)

    async def go():
        try:
            rid = await _seed(S, reown=5)
            r = await rw.run_reown_sweep()
            assert r.reowned == 0 and (await _row(S, rid)).reown_count == 5
        finally:
            await eng.dispose()
    _run(go())


# 15/16/17. invalid key / fingerprint / cross-user → 0
@pytest.mark.parametrize("key,fp,uid_cas", [("v1:review:not-uuid", _FP, "u1"), (_V1, "fp1:BAD", "u1"),
                                            (_V1, _FP, "OTHER")])
def test_pg_reown_invalid_or_cross_user_zero(monkeypatch, key, fp, uid_cas):
    _ensure_schema(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)

    async def go():
        try:
            rid = await _seed(S, key=key, fp=fp, uid="u1")
            if uid_cas == "OTHER":
                # sweep runs per real user 'u1'; cross-user defence is in the CAS user_id predicate
                now = datetime.now(timezone.utc)
                async with S() as db:
                    res = await db.execute(rw._REOWN_CAS, {"id": rid, "uid": "OTHER", "seen": 0, "max": 5,
                        "now": now, "cut_naive": datetime.utcnow()+timedelta(60), "cut_tz": now+timedelta(60)})
                    assert res.fetchone() is None; await db.commit()
            else:
                r = await rw.run_reown_sweep(); assert r.reowned == 0
            assert (await _row(S, rid)).claim_generation == 0
        finally:
            await eng.dispose()
    _run(go())


# 18/19. pending revert inverse re-owned; original untouched; two inverse reowners → one
def test_pg_reown_revert_inverse(monkeypatch):
    _ensure_schema(monkeypatch); prov = _stub_provider(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)
    from services.marketplace import operation_key

    async def go():
        try:
            orig = await _seed(S, key="v1:client:3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                               status="success", dsa=True, age_s=3600)
            inv_key = operation_key.revert_key(orig)          # v1:revert:<original>
            inv = await _seed(S, key=inv_key, status="pending", gen=0, age_s=3600)
            r = await rw.run_reown_sweep()
            assert r.reowned == 1 and prov["n"] == 0
            assert (await _row(S, inv)).claim_generation == 1                 # inverse transferred
            o = await _row(S, orig)
            assert o.status == "success" and o.claim_generation == 0          # original untouched
        finally:
            await eng.dispose()
    _run(go())


# 21. reconciliation (RECN) and re-own (REOW) advisory locks are distinct → both acquirable at once
def test_pg_reown_advisory_lock_distinct_from_reconcile(monkeypatch):
    _ensure_schema(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)
    from services.marketplace.recovery import recovery_sweep as rs

    async def go():
        try:
            assert (rw._LOCK_NAMESPACE, rw._LOCK_OPERATION) != (rs._LOCK_NAMESPACE, rs._LOCK_OPERATION)
            async with eng.connect() as c1, eng.connect() as c2:
                a = (await c1.execute(text("SELECT pg_try_advisory_lock(:n,:o)"),
                     {"n": rw._LOCK_NAMESPACE, "o": rw._LOCK_OPERATION})).scalar()
                b = (await c2.execute(text("SELECT pg_try_advisory_lock(:n,:o)"),
                     {"n": rs._LOCK_NAMESPACE, "o": rs._LOCK_OPERATION})).scalar()
                assert a is True and b is True                # different locks, both granted
                await c1.execute(text("SELECT pg_advisory_unlock(:n,:o)"),
                                 {"n": rw._LOCK_NAMESPACE, "o": rw._LOCK_OPERATION})
                await c2.execute(text("SELECT pg_advisory_unlock(:n,:o)"),
                                 {"n": rs._LOCK_NAMESPACE, "o": rs._LOCK_OPERATION})
        finally:
            await eng.dispose()
    _run(go())


# 22. advisory-lock collision: a held REOW lock → the sweep does not run, 0 mutation
def test_pg_reown_advisory_collision_skips(monkeypatch):
    _ensure_schema(monkeypatch)
    rw, eng, S = _install(monkeypatch, enabled=True, dry_run=False)

    async def go():
        lock_eng = create_async_engine(_pg_async_url())
        try:
            rid = await _seed(S)
            async with lock_eng.connect() as held:
                got = (await held.execute(text("SELECT pg_try_advisory_lock(:n,:o)"),
                       {"n": rw._LOCK_NAMESPACE, "o": rw._LOCK_OPERATION})).scalar()
                assert got is True
                r = await rw.run_reown_sweep()
                assert r.lock_acquired is False and r.reowned == 0
                await held.execute(text("SELECT pg_advisory_unlock(:n,:o)"),
                                   {"n": rw._LOCK_NAMESPACE, "o": rw._LOCK_OPERATION})
            assert (await _row(S, rid)).claim_generation == 0
        finally:
            await lock_eng.dispose(); await eng.dispose()
    _run(go())


# 23. migration seeded up/down/re-up preserves C1/recovery columns + UNIQUE/CHECK
def test_pg_reown_migration_seeded_roundtrip(monkeypatch):
    import sqlalchemy as sa
    from alembic import command
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    import db_migrations as dbm
    cfg = dbm._alembic_config()
    eng = sa.create_engine(_pg_sync_url())
    global _SCHEMA_READY
    _SCHEMA_READY = False

    def _cols(c):
        return {r[0] for r in c.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns WHERE table_name='execution_logs'")}
    try:
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(cfg, "fcs1a2b3c4d01")
        with eng.begin() as c:
            assert "reown_count" not in _cols(c)
            c.exec_driver_sql("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                              "idempotency_key,attempt_count) VALUES('L1','u','set_price','manual_l3','{}',"
                              "'in_flight','" + _V1 + "',4)")
        command.upgrade(cfg, "rwn1a2b3c4d01")
        with eng.begin() as c:
            assert {"reown_count", "last_reowned_at"} <= _cols(c)
            row = c.exec_driver_sql("SELECT reown_count, last_reowned_at, attempt_count FROM execution_logs "
                                    "WHERE id='L1'").first()
            assert tuple(row) == (0, None, 4)          # backfilled; C1 attempt_count intact
        command.downgrade(cfg, "fcs1a2b3c4d01")
        with eng.begin() as c:
            assert not ({"reown_count", "last_reowned_at"} & _cols(c))
            assert "attempt_count" in _cols(c) and c.exec_driver_sql(
                "SELECT count(*) FROM execution_logs").scalar() == 1
        command.upgrade(cfg, "rwn1a2b3c4d01")
        with eng.begin() as c:
            assert "reown_count" in _cols(c)
    finally:
        eng.dispose()
