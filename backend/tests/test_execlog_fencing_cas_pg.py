"""SECURITY-2D-1C-C1 — fencing ownership CAS on REAL PostgreSQL 16 (true cross-connection concurrency).

Guarantee: a worker may take in_flight + dispatch to the provider ONLY while it still owns the
un-dispatched claim (status='pending', dispatch_started_at IS NULL, its own claim_generation). A worker
that lost ownership (a concurrent session bumped claim_generation) matches nothing in the CAS and makes
ZERO provider calls. The provider stub counts ACTUAL dispatch calls, not ExecutionLog rows.

Skipped locally; runs in the postgres-explain CI job (PULT_TEST_PG_URL set), 0 skip there.
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_async_url():
    return (_pg_sync_url() or "").replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


def _pg_alembic_url():
    return os.environ.get("PULT_TEST_PG_ALEMBIC_URL") or _pg_async_url()


pytestmark = pytest.mark.skipif(
    not (_pg_sync_url() or "").startswith("postgres"),
    reason="BLOCKED_ENVIRONMENT: no PostgreSQL (PULT_TEST_PG_URL unset); runs in postgres-explain CI.",
)

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
    # each test starts from an empty shared schema
    eng = sa.create_engine(_pg_sync_url())
    with eng.begin() as c:
        c.exec_driver_sql("TRUNCATE execution_logs, api_credentials, marketplace_connections CASCADE")
    eng.dispose()


def _sessionmaker():
    eng = create_async_engine(_pg_async_url())
    return eng, sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _seed_user(Session, marketplace="wildberries"):
    from models.marketplace_connection import MarketplaceConnection
    from models.api_credential import ApiCredential
    from services.marketplace import credential_vault
    uid = str(uuid.uuid4())
    async with Session() as db:
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                     status="connected", scopes=["feedbacks", "prices"])
        db.add(conn)
        await db.flush()
        for scope in ("feedbacks", "prices"):
            db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope=scope,
                                 secret_enc=credential_vault.encrypt("tok"), meta={}))
        await db.commit()
        cid = conn.id
    return uid, cid


def _install_stub(monkeypatch, counter, *, fail=None, delay=0.0, crash=False):
    from services.marketplace.wb_client import wb_client
    from services.marketplace.errors import ExecutionError

    async def _fake(*, token, feedback_id, text):
        counter["n"] += 1
        if delay:
            await asyncio.sleep(delay)
        if crash:
            raise SystemExit("simulated crash after CAS, at provider entry")  # BaseException, not caught
        if fail:
            raise ExecutionError(fail, "boom")
        return {"api_request_id": "req"}
    monkeypatch.setattr(wb_client, "publish_feedback_answer", _fake)


def _rk():
    from services.marketplace import operation_key
    return operation_key.review_key(str(uuid.uuid4()))


async def _publish(Session, uid, key, text="Спасибо!"):
    from services.marketplace import executor
    async with Session() as db:
        return await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"marketplace": "wildberries", "feedback_id": "fb1", "text": text, "rating": 5},
            idempotency_key=key)


async def _row(Session, uid):
    from sqlalchemy import select
    from models.execution_log import ExecutionLog
    async with Session() as db:
        return (await db.execute(select(ExecutionLog).where(ExecutionLog.user_id == uid))).scalars().first()


async def _publish_tolerant(Session, uid, key, text="Спасибо!"):
    """Run the executor with an explicit session whose close tolerates the async-driver teardown noise a
    CAS-phase DB error can leave behind (the executor already returned its safe result before teardown)."""
    from services.marketplace import executor
    db = Session()
    try:
        return await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"marketplace": "wildberries", "feedback_id": "fb1", "text": text, "rating": 5},
            idempotency_key=key)
    finally:
        try:
            await db.close()
        except Exception:  # noqa: BLE001 — teardown after a CAS-phase driver error is not under test
            pass


async def _seed_pending_claim(Session, uid, cid, key, *, gen=0, dsa=False,
                              action_type="publish_review_response", fp="fp1:x"):
    from models.execution_log import ExecutionLog
    async with Session() as db:
        rec = ExecutionLog(id=str(uuid.uuid4()), user_id=uid, connection_id=cid,
                           action_type=action_type, mode="manual_l3", payload={},
                           status="pending", idempotency_key=key, request_fingerprint=fp,
                           claim_generation=gen,
                           dispatch_started_at=datetime.now(timezone.utc) if dsa else None)
        db.add(rec)
        await db.commit()
        return rec.id


async def _cas(Session, rec_id, gen):
    from services.marketplace.executor import _FENCE_CAS
    async with Session() as db:
        r = (await db.execute(_FENCE_CAS,
             {"id": rec_id, "now": datetime.now(timezone.utc), "gen": gen})).first()
        await db.commit()
        return r


async def _bump_generation(Session, rec_id):
    from sqlalchemy import text
    async with Session() as db:
        await db.execute(text("UPDATE execution_logs SET claim_generation=claim_generation+1 "
                              "WHERE id=:id AND status='pending' AND dispatch_started_at IS NULL"),
                         {"id": rec_id})
        await db.commit()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── 1. single worker: one dispatch, attempt_count=1, dsa set ──────────────────
def test_pg_fencing_single_dispatch_sets_fields(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}; _install_stub(monkeypatch, c)

    async def go():
        eng, S = _sessionmaker()
        try:
            uid, _ = await _seed_user(S)
            r = await _publish(S, uid, _rk())
            assert r.status == "success" and c["n"] == 1
            row = await _row(S, uid)
            assert row.status == "success" and row.dispatch_started_at is not None
            assert row.attempt_count == 1 and row.last_attempt_at is not None
        finally:
            await eng.dispose()
    _run(go())


# ── 2. two concurrent workers, one key → exactly one dispatch ─────────────────
@pytest.mark.parametrize("n", [2, 8])
def test_pg_fencing_concurrent_one_dispatch(monkeypatch, n):
    _ensure_schema(monkeypatch)
    c = {"n": 0}; _install_stub(monkeypatch, c, delay=0.05)

    async def go():
        eng, S = _sessionmaker()
        try:
            uid, _ = await _seed_user(S)
            key = _rk()
            res = await asyncio.gather(*[_publish(S, uid, key) for _ in range(n)],
                                       return_exceptions=True)
            assert [getattr(r, "status", "") for r in res].count("success") >= 1
            assert c["n"] == 1                                   # exactly one real provider call
        finally:
            await eng.dispose()
    _run(go())


# ── 3/4. CAS predicate: stale generation → empty (0 dispatch); matching → owns ─
def test_pg_fencing_cas_predicate_generation(monkeypatch):
    _ensure_schema(monkeypatch)

    async def go():
        eng, S = _sessionmaker()
        try:
            uid, cid = await _seed_user(S)
            rid = await _seed_pending_claim(S, uid, cid, _rk(), gen=0)
            await _bump_generation(S, rid)                       # a re-own bumps to gen=1, still pending+dsa NULL
            assert await _cas(S, rid, 0) is None                 # stale owner (gen 0) → RETURNING empty
            row = await _row(S, uid)
            assert row.status == "pending" and row.dispatch_started_at is None
            assert row.claim_generation == 1                     # not overwritten by the stale worker
            won = await _cas(S, rid, 1)                          # current owner (gen 1) wins
            assert won is not None
            row = await _row(S, uid)
            assert row.status == "in_flight" and row.dispatch_started_at is not None and row.attempt_count == 1
            assert await _cas(S, rid, 1) is None                 # second CAS → already in_flight → empty
        finally:
            await eng.dispose()
    _run(go())


# ── 5. CAS on a dsa-already-set pending row → empty (never re-dispatch) ────────
def test_pg_fencing_cas_rejects_dsa_set(monkeypatch):
    _ensure_schema(monkeypatch)

    async def go():
        eng, S = _sessionmaker()
        try:
            uid, cid = await _seed_user(S)
            rid = await _seed_pending_claim(S, uid, cid, _rk(), gen=0, dsa=True)
            assert await _cas(S, rid, 0) is None                 # dsa set → predicate excludes it
        finally:
            await eng.dispose()
    _run(go())


# ── 6. stale generation injected DURING the live execute window → 0 dispatch ──
def test_pg_fencing_reown_during_execute_zero_dispatch(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}; _install_stub(monkeypatch, c)
    # hook the token resolution (runs AFTER owned_generation capture, BEFORE the CAS) to bump the
    # generation from a separate session — simulating a re-own winning the race mid-flight.
    from services.marketplace import executor as ex
    orig = ex._resolve_token

    async def go():
        eng, S = _sessionmaker()

        async def _hook(db, connection_id, scope):
            from sqlalchemy import text
            await db.execute(text("UPDATE execution_logs SET claim_generation=claim_generation+1 "
                                  "WHERE connection_id=:c AND status='pending' AND dispatch_started_at IS NULL"),
                             {"c": connection_id})
            await db.commit()
            return await orig(db, connection_id, scope)
        monkeypatch.setattr(ex, "_resolve_token", _hook)
        try:
            uid, _ = await _seed_user(S)
            r = await _publish(S, uid, _rk())
            assert c["n"] == 0                                   # lost ownership → zero provider calls
            assert r.status == "needs_reconcile"
            row = await _row(S, uid)
            assert row.status == "pending" and row.dispatch_started_at is None   # still a safe pending claim
        finally:
            await eng.dispose()
    _run(go())


# ── 7. crash after CAS commit, before provider → in_flight, retry = 0 dispatch ─
def test_pg_fencing_crash_after_cas_no_auto_retry(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}; _install_stub(monkeypatch, c, crash=True)

    async def go():
        eng, S = _sessionmaker()
        try:
            uid, _ = await _seed_user(S)
            key = _rk()
            with pytest.raises(SystemExit):
                await _publish(S, uid, key)                      # CAS committed, then crash at provider entry
            row = await _row(S, uid)
            assert row.status == "in_flight" and row.dispatch_started_at is not None and row.attempt_count == 1
            # a later retry of the SAME key never dispatches — in_flight → OPERATION_IN_PROGRESS
            c["n"] = 0
            _install_stub(monkeypatch, c)                        # overwrite the crash stub with a counting one
            r2 = await _publish(S, uid, key)
            assert r2.status == "needs_reconcile" and c["n"] == 0
        finally:
            await eng.dispose()
    _run(go())


# ── 8. provider timeout/5xx → ambiguous; CAS fields kept; retry 0 dispatch ─────
def test_pg_fencing_ambiguous_keeps_fields_no_retry(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    # A provider error AFTER the request left → recorded as ambiguous/failed; either way the CAS fields
    # are preserved and a retry of the same key never dispatches a second time.
    _install_stub(monkeypatch, c, fail="TIMEOUT")

    async def go():
        eng, S = _sessionmaker()
        try:
            uid, _ = await _seed_user(S)
            key = _rk()
            r = await _publish(S, uid, key)
            assert r.status in ("ambiguous", "failed")           # provider error recorded
            row = await _row(S, uid)
            assert row.dispatch_started_at is not None and row.attempt_count == 1   # CAS fields preserved
            c["n"] = 0
            r2 = await _publish(S, uid, key)                     # retry same key → 0 dispatch
            assert c["n"] == 0 and r2.status in ("needs_reconcile", "success", "ambiguous")
        finally:
            await eng.dispose()
    _run(go())


# ── 9. migration seeded up/down/re-up preserves rows + UNIQUE ─────────────────
def test_pg_fencing_migration_seeded_roundtrip(monkeypatch):
    if not (_pg_sync_url() or "").startswith("postgres"):
        pytest.skip("no PG")
    import sqlalchemy as sa
    from alembic import command
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    import db_migrations as dbm
    cfg = dbm._alembic_config()
    eng = sa.create_engine(_pg_sync_url())
    global _SCHEMA_READY
    _SCHEMA_READY = False
    try:
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(cfg, "rbp1a2b3c4d01")
        with eng.begin() as c:
            assert "attempt_count" not in _cols(c)
            c.exec_driver_sql("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                              "idempotency_key) VALUES('L1','u','publish_review_response','manual_l3','{}',"
                              "'in_flight','v1:review:3f2504e0-4f89-41d3-9a0c-0305e82c3301')")
        command.upgrade(cfg, "fcs1a2b3c4d01")
        with eng.begin() as c:
            assert {"attempt_count", "last_attempt_at"} <= _cols(c)
            row = c.exec_driver_sql("SELECT status, attempt_count, last_attempt_at FROM execution_logs "
                                    "WHERE id='L1'").first()
            assert tuple(row) == ("in_flight", 0, None)          # existing row backfilled
            # 1B-B partial UNIQUE preserved: same (user, v1 key) rejected
            import pytest as _pt
            with _pt.raises(Exception):
                c.exec_driver_sql("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                                  "idempotency_key) VALUES('L2','u','publish_review_response','manual_l3','{}',"
                                  "'pending','v1:review:3f2504e0-4f89-41d3-9a0c-0305e82c3301')")
        with eng.begin() as c:
            pass
        command.downgrade(cfg, "rbp1a2b3c4d01")
        with eng.begin() as c:
            assert not ({"attempt_count", "last_attempt_at"} & _cols(c))
            assert c.exec_driver_sql("SELECT count(*) FROM execution_logs").scalar() == 1   # rows kept
        command.upgrade(cfg, "fcs1a2b3c4d01")
        with eng.begin() as c:
            assert "attempt_count" in _cols(c)
    finally:
        eng.dispose()


def _cols(c):
    return {r[0] for r in c.exec_driver_sql(
        "SELECT column_name FROM information_schema.columns WHERE table_name='execution_logs'")}




# ── K. clean business failure → failed; retry of same key → 0 dispatch ────────
def test_pg_fencing_business_failure_then_retry_zero(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}; _install_stub(monkeypatch, c, fail="VALIDATION")   # non-ambiguous → 'failed'

    async def go():
        eng, S = _sessionmaker()
        try:
            uid, _ = await _seed_user(S)
            key = _rk()
            r1 = await _publish(S, uid, key)
            assert r1.status == "failed" and c["n"] == 1
            row = await _row(S, uid)
            assert row.status == "failed" and row.dispatch_started_at is not None and row.attempt_count == 1
            c["n"] = 0
            r2 = await _publish(S, uid, key)               # same key → never re-dispatch
            assert c["n"] == 0 and r2.status == "needs_reconcile"
        finally:
            await eng.dispose()
    _run(go())


# ── L. terminal commit FAILS after the provider call → dispatch=1; retry → 0 ──
def test_pg_fencing_terminal_commit_failure_after_provider(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    st = {"dispatched": False}
    from services.marketplace.wb_client import wb_client

    async def _fake(*, token, feedback_id, text):
        c["n"] += 1
        st["dispatched"] = True
        return {"api_request_id": "req"}
    monkeypatch.setattr(wb_client, "publish_feedback_answer", _fake)
    real_commit = AsyncSession.commit

    async def _com(self, *a, **k):
        if st["dispatched"]:                               # phase = the FIRST commit after the provider call
            st["dispatched"] = False
            raise SQLAlchemyError("injected terminal COMMIT error")
        return await real_commit(self, *a, **k)
    monkeypatch.setattr(AsyncSession, "commit", _com, raising=True)

    async def go():
        eng, S = _sessionmaker()
        try:
            uid, _ = await _seed_user(S)
            key = _rk()
            with pytest.raises(SQLAlchemyError):
                await _publish(S, uid, key)                # dispatch=1, terminal commit raises (uncaught by design)
            assert c["n"] == 1
            row = await _row(S, uid)
            # CAS-set state survived; terminal 'success' never landed → row is in_flight, retry-safe
            assert row.status == "in_flight" and row.dispatch_started_at is not None and row.attempt_count == 1
            c["n"] = 0
            r2 = await _publish(S, uid, key)               # in_flight → OPERATION_IN_PROGRESS, 0 dispatch
            assert c["n"] == 0 and r2.status == "needs_reconcile"
        finally:
            await eng.dispose()
    _run(go())


# ── G. REAL PostgreSQL lock_timeout at the CAS UPDATE (real row lock, not malformed SQL) → 0 dispatch ──
def test_pg_fencing_cas_lock_timeout_zero_dispatch(monkeypatch):
    from sqlalchemy import text
    from services.marketplace import executor as ex
    _ensure_schema(monkeypatch)
    c = {"n": 0}; _install_stub(monkeypatch, c)
    orig_token = ex._resolve_token

    async def go():
        engB, SB = _sessionmaker()
        engA, SA = _sessionmaker()          # independent connection for the lock holder
        holder = {"db": None}

        async def _barrier(db, connection_id, scope):
            # TEST BARRIER ONLY (reuses the existing pre-CAS token resolver; no production hook added).
            # Runs AFTER the claim commit and BEFORE the fencing CAS. Here: (1) a SEPARATE session A takes
            # a real FOR UPDATE row lock on B's pending claim; (2) B's session gets a tiny lock_timeout.
            rid = (await db.execute(text(
                "SELECT id FROM execution_logs WHERE connection_id=:c AND status='pending' "
                "AND dispatch_started_at IS NULL"), {"c": connection_id})).scalar()
            dbA = SA()
            await dbA.execute(text("SELECT id FROM execution_logs WHERE id=:id FOR UPDATE"), {"id": rid})
            holder["db"] = dbA              # A holds the lock in an open transaction (idle, not awaiting)
            await db.execute(text("SET lock_timeout = '150ms'"))   # session-level, persists to the CAS
            return await orig_token(db, connection_id, scope)
        monkeypatch.setattr(ex, "_resolve_token", _barrier)

        try:
            uid, _ = await _seed_user(SB)
            r = await _publish_tolerant(SB, uid, _rk())   # the real _FENCE_CAS UPDATE blocks on A's lock → lock_timeout
            assert c["n"] == 0                                   # provider never reached
            assert r.status == "needs_reconcile" and r.error["code"] == "CLAIM_CAS_ERROR"
            row = await _row(SB, uid)
            assert row.status == "pending" and row.dispatch_started_at is None
            assert row.attempt_count == 0 and row.last_attempt_at is None      # CAS never landed
            assert row.claim_generation == 0                     # unchanged
        finally:
            if holder["db"] is not None:
                await holder["db"].rollback(); await holder["db"].close()
            await engA.dispose(); await engB.dispose()
    _run(go())


# ── H. deterministic failure of the CAS COMMIT itself (2nd commit) via a test-only Session subclass ──
def test_pg_fencing_cas_commit_failure_zero_dispatch(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    st = {"dispatched": False}
    from services.marketplace.wb_client import wb_client

    async def _fake(*, token, feedback_id, text):        # provider must never be reached
        c["n"] += 1; st["dispatched"] = True
        return {"api_request_id": "req"}
    monkeypatch.setattr(wb_client, "publish_feedback_answer", _fake)

    class _CASCommitFailSession(AsyncSession):
        # commit #1 = the claim INSERT commit (runs for real); commit #2 = the fencing-CAS commit
        # (structurally after the real CAS UPDATE, before spec.dispatch) → deterministically fail it,
        # raising BEFORE any real commit IO so the connection stays healthy for the executor's rollback.
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._n_commit = 0

        async def commit(self):
            self._n_commit += 1
            if self._n_commit == 1:
                return await super().commit()            # real claim commit
            assert self._n_commit == 2                   # phase: the 2nd commit IS the CAS commit
            assert not st["dispatched"]                  # phase: provider not yet called
            raise SQLAlchemyError("injected CAS COMMIT failure (2nd commit)")

    async def go():
        from sqlalchemy.orm import sessionmaker
        eng = create_async_engine(_pg_async_url())
        S = sessionmaker(eng, class_=_CASCommitFailSession, expire_on_commit=False)
        try:
            uid, _ = await _seed_user(S)
            r = await _publish_tolerant(S, uid, _rk())
            assert c["n"] == 0                                   # provider never reached
            assert r.status == "needs_reconcile" and r.error["code"] == "CLAIM_CAS_ERROR"
            # a separate, normal verification session sees the fully-rolled-back CAS
            _, SV = _sessionmaker()
            row = await _row(SV, uid)
            assert row.status == "pending" and row.dispatch_started_at is None
            assert row.attempt_count == 0 and row.last_attempt_at is None   # CAS UPDATE ran then rolled back
            assert row.claim_generation == 0
        finally:
            await eng.dispose()
    _run(go())
