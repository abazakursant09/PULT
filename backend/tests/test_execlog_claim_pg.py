"""SECURITY-2D-1B-B — claim-before-dispatch on REAL PostgreSQL (true cross-connection concurrency).

The guarantee under test: at-most-one LOCAL provider dispatch per (user_id, operation_key). The provider
stub counts ACTUAL dispatch calls (not just ExecutionLog rows). Skipped locally; runs in the
postgres-explain CI job (PULT_TEST_PG_URL set), 0 skip there.
"""
import asyncio
import os
import uuid

import pytest

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_async_url():
    sync = _pg_sync_url() or ""
    return sync.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


def _pg_alembic_url():
    explicit = os.environ.get("PULT_TEST_PG_ALEMBIC_URL")
    if explicit:
        return explicit
    return _pg_async_url()


pytestmark = pytest.mark.skipif(
    not (_pg_sync_url() or "").startswith("postgres"),
    reason="BLOCKED_ENVIRONMENT: no PostgreSQL (PULT_TEST_PG_URL unset); runs in postgres-explain CI.",
)

_SCHEMA_READY = False


def _ensure_schema(monkeypatch):
    global _SCHEMA_READY
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    if _SCHEMA_READY:
        return
    import sqlalchemy as sa
    from alembic import command
    import db_migrations as dbm
    eng = sa.create_engine(_pg_sync_url())
    with eng.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    command.upgrade(dbm._alembic_config(), "head")
    eng.dispose()
    _SCHEMA_READY = True


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
        db.add(conn); await db.flush()
        for scope in ("feedbacks", "prices"):
            db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope=scope,
                                 secret_enc=credential_vault.encrypt("tok"), meta={}))
        await db.commit()
    return uid


async def _conn_id(Session, uid):
    from sqlalchemy import select as _select
    from models.marketplace_connection import MarketplaceConnection
    async with Session() as db:
        return (await db.execute(_select(MarketplaceConnection.id).where(
            MarketplaceConnection.user_id == uid))).scalars().first()


def _fp_for_publish(uid, conn_id, text="Спасибо!"):
    from services.marketplace.executor import _fingerprint
    return _fingerprint(uid, conn_id, "wildberries", "publish_review_response", "manual_l3",
                        {"marketplace": "wildberries", "feedback_id": "fb1", "text": text, "rating": 5},
                        None)


async def _seed_claim(Session, uid, key, fp, status, *, in_flight_ts=False, decision_id=None,
                      action_type="publish_review_response"):
    from datetime import datetime, timezone
    from models.execution_log import ExecutionLog
    async with Session() as db:
        db.add(ExecutionLog(
            id=str(uuid.uuid4()), user_id=uid, action_type=action_type, mode="manual_l3",
            payload={}, status=status, idempotency_key=key, request_fingerprint=fp,
            decision_id=decision_id,
            dispatch_started_at=datetime.now(timezone.utc) if in_flight_ts else None))
        await db.commit()


def _install_stub(monkeypatch, counter, *, fail=None, delay=0.05):
    from services.marketplace.wb_client import wb_client
    from services.marketplace.errors import ExecutionError

    async def _fake(*, token, feedback_id, text):
        counter["n"] += 1
        await asyncio.sleep(delay)          # widen the window so claims truly overlap
        if fail:
            raise ExecutionError(fail, "boom")
        return {"api_request_id": "req"}
    monkeypatch.setattr(wb_client, "publish_feedback_answer", _fake)


async def _publish(Session, uid, key, text="Спасибо!"):
    from services.marketplace import executor
    async with Session() as db:
        return await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"marketplace": "wildberries", "feedback_id": "fb1", "text": text, "rating": 5},
            idempotency_key=key,
        )


def _rk():
    from services.marketplace import operation_key
    return operation_key.review_key(str(uuid.uuid4()))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── concurrency: at-most-one dispatch ─────────────────────────────────────────

@pytest.mark.parametrize("n", [2, 10])
def test_pg_concurrent_same_operation_dispatches_once(monkeypatch, n):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    _install_stub(monkeypatch, c)

    async def go():
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            key = _rk()
            results = await asyncio.gather(*[_publish(Session, uid, key) for _ in range(n)],
                                           return_exceptions=True)
            statuses = [getattr(r, "status", repr(r)) for r in results]
            assert statuses.count("success") >= 1
            assert c["n"] == 1, (c["n"], statuses)     # EXACTLY one real provider call
        finally:
            await eng.dispose()
    _run(go())


def test_pg_distinct_keys_same_fingerprint_each_dispatch(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    _install_stub(monkeypatch, c, delay=0)

    async def go():
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            keys = [_rk() for _ in range(4)]           # independent operations, identical content
            await asyncio.gather(*[_publish(Session, uid, k) for k in keys])
            assert c["n"] == 4
        finally:
            await eng.dispose()
    _run(go())


def test_pg_same_key_different_payload_is_mismatch(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    _install_stub(monkeypatch, c, delay=0)

    async def go():
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            key = _rk()
            r1 = await _publish(Session, uid, key, text="one")
            r2 = await _publish(Session, uid, key, text="TWO")
            assert r1.status == "success"
            assert r2.status == "needs_reconcile" and r2.error["code"] == "IDEMPOTENCY_MISMATCH"
            assert c["n"] == 1
        finally:
            await eng.dispose()
    _run(go())


def test_pg_invalid_and_missing_key_never_dispatch(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    _install_stub(monkeypatch, c, delay=0)

    async def go():
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            # Executor requires a well-formed v1 SHAPE; canonical-UUID validation is the router's job
            # (tested in test_operation_key / test_body_operation_key). These non-v1 keys are rejected.
            for bad in (None, "", "review:x", "price:p:1", "v2:client:x"):
                r = await _publish(Session, uid, bad)
                assert r.status == "rejected", bad
            assert c["n"] == 0
        finally:
            await eng.dispose()
    _run(go())


@pytest.mark.parametrize("legacy_status", ["pending", "success", "failed", "ambiguous",
                                           "rejected", "reverted"])
def test_pg_legacy_review_alias_blocks(monkeypatch, legacy_status):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    _install_stub(monkeypatch, c, delay=0)

    async def go():
        from models.execution_log import ExecutionLog
        from services.marketplace import operation_key
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            rid = str(uuid.uuid4())
            async with Session() as db:
                db.add(ExecutionLog(id=str(uuid.uuid4()), user_id=uid,
                                    action_type="publish_review_response", mode="manual_l3",
                                    payload={}, status=legacy_status,
                                    idempotency_key="review:" + rid))
                await db.commit()
            r = await _publish(Session, uid, operation_key.review_key(rid))
            assert r.status == "needs_reconcile"
            assert r.error["code"] == "LEGACY_OPERATION_NEEDS_RECONCILE"
            assert c["n"] == 0
        finally:
            await eng.dispose()
    _run(go())


def test_pg_legacy_decision_alias_blocks(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    _install_stub(monkeypatch, c, delay=0)

    async def go():
        from models.execution_log import ExecutionLog
        from services.marketplace import operation_key
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            did = str(uuid.uuid4())
            async with Session() as db:
                db.add(ExecutionLog(id=str(uuid.uuid4()), user_id=uid,
                                    action_type="publish_review_response", mode="manual_l3",
                                    payload={}, status="success", decision_id=did,
                                    idempotency_key=str(uuid.uuid4())))   # opaque, non-v1
                await db.commit()
            r = await _publish(Session, uid, operation_key.decision_key(did))
            assert r.status == "needs_reconcile"
            assert c["n"] == 0
        finally:
            await eng.dispose()
    _run(go())


def test_pg_prior_failed_blocks_retry(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    from services.marketplace.errors import ExecutionError
    _install_stub(monkeypatch, c, fail=ExecutionError.AUTH, delay=0)

    async def go():
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            key = _rk()
            r1 = await _publish(Session, uid, key)
            assert r1.status == "failed" and c["n"] == 1
            _install_stub(monkeypatch, c, delay=0)     # would now succeed — must NOT be called
            r2 = await _publish(Session, uid, key)
            assert r2.status == "needs_reconcile" and c["n"] == 1
        finally:
            await eng.dispose()
    _run(go())


def test_pg_migration_duplicate_v1_preflight_fails_closed(monkeypatch):
    """Two rows sharing (user_id, v1 key) make the unique-index migration refuse (numeric count only)."""
    _ensure_schema(monkeypatch)

    # NOTE: alembic commands run SYNC at the top level — never inside a running event loop (asyncpg's
    # env.py calls asyncio.run(), which raises if a loop is already running).
    from alembic import command
    import db_migrations as dbm
    from models.execution_log import ExecutionLog
    cfg = dbm._alembic_config()
    command.downgrade(cfg, "efp1a2b3c4d01")     # drop the unique index so two same-key rows can coexist

    key = "v1:client:" + str(uuid.uuid4())

    async def seed():
        eng, Session = _sessionmaker()
        uid = await _seed_user(Session)
        async with Session() as db:
            for _ in range(2):
                db.add(ExecutionLog(id=str(uuid.uuid4()), user_id=uid, action_type="set_price",
                                    mode="manual_l3", payload={}, status="success",
                                    idempotency_key=key))
            await db.commit()
        await eng.dispose()
        return uid

    uid = _run(seed())
    try:
        with pytest.raises(Exception) as e:
            command.upgrade(cfg, "uqc1a2b3c4d01")
        msg = str(e.value)
        assert "preflight" in msg and key not in msg and uid not in msg   # numeric only, no PII
    finally:
        # leave a clean, fully-migrated schema for any following test, regardless of run order
        import sqlalchemy as sa
        eng2 = sa.create_engine(_pg_sync_url())
        with eng2.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(cfg, "head")
        eng2.dispose()


# ── resolve outcomes on an existing claim (crash states + mismatch), all 0 dispatch ───────────

def _seeded_reconcile_case(monkeypatch, *, status, use_fp, in_flight_ts=False):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    _install_stub(monkeypatch, c, delay=0)

    async def go():
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            conn_id = await _conn_id(Session, uid)
            key = _rk()
            fp = _fp_for_publish(uid, conn_id) if use_fp == "match" else \
                (None if use_fp == "null" else "fp1:" + "0" * 64)
            await _seed_claim(Session, uid, key, fp, status=status, in_flight_ts=in_flight_ts)
            r = await _publish(Session, uid, key)
            assert r.status == "needs_reconcile", (status, use_fp, r.status)
            assert c["n"] == 0
            return r
        finally:
            await eng.dispose()
    return _run(go())


def test_pg_crash_after_pending_blocks(monkeypatch):
    # claim committed, provider never called (dispatch_started_at NULL) -> retry blocked, 0 dispatch
    r = _seeded_reconcile_case(monkeypatch, status="pending", use_fp="match")
    assert r.error["code"] == "OPERATION_IN_PROGRESS"


def test_pg_crash_after_in_flight_blocks(monkeypatch):
    # provider dispatch begun (dispatch_started_at set), terminal not written -> retry blocked
    r = _seeded_reconcile_case(monkeypatch, status="in_flight", use_fp="match", in_flight_ts=True)
    assert r.error["code"] == "OPERATION_IN_PROGRESS"


def test_pg_ambiguous_prior_blocks(monkeypatch):
    r = _seeded_reconcile_case(monkeypatch, status="ambiguous", use_fp="match")
    assert r.error["code"] == "AMBIGUOUS_PRIOR"


def test_pg_existing_failed_blocks(monkeypatch):
    r = _seeded_reconcile_case(monkeypatch, status="failed", use_fp="match")
    assert r.error["code"] == "PRIOR_FAILED"


def test_pg_v1_null_fingerprint_blocks(monkeypatch):
    r = _seeded_reconcile_case(monkeypatch, status="success", use_fp="null")
    assert r.error["code"] == "NEEDS_RECONCILE"


def test_pg_same_key_different_fingerprint_mismatch_seeded(monkeypatch):
    r = _seeded_reconcile_case(monkeypatch, status="success", use_fp="diff")
    assert r.error["code"] == "IDEMPOTENCY_MISMATCH"


# ── automation-disabled L4 + auto-pricing: 0 dispatch ─────────────────────────────────────────

def test_pg_automation_disabled_l4_zero(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    _install_stub(monkeypatch, c, delay=0)
    from config import settings
    monkeypatch.setattr(settings, "automation_enabled", False)

    async def go():
        from services.marketplace import executor, operation_key
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            async with Session() as db:
                r = await executor.execute(
                    db=db, user_id=uid, action_type="publish_review_response",
                    payload={"marketplace": "wildberries", "feedback_id": "fb1", "text": "x", "rating": 5},
                    mode="automated_l4", idempotency_key=operation_key.review_key(str(uuid.uuid4())))
            assert r.status == "rejected" and c["n"] == 0
        finally:
            await eng.dispose()
    _run(go())


def test_pg_autopricing_no_durable_key_zero(monkeypatch):
    _ensure_schema(monkeypatch)
    from services.marketplace.wb_client import wb_client
    calls = {"n": 0}

    async def _fake_set_price(*, token, offer_id, price, discount=None):
        calls["n"] += 1
        return {"requestId": "r"}
    monkeypatch.setattr(wb_client, "set_price", _fake_set_price)
    from config import settings
    monkeypatch.setattr(settings, "automation_enabled", False)

    async def go():
        from services.marketplace import executor
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            async with Session() as db:
                r = await executor.execute(          # automated_l4 set_price with NO key (auto-pricing)
                    db=db, user_id=uid, action_type="set_price",
                    payload={"marketplace": "wildberries", "offer_id": "OF1", "price": 100, "old_price": 90},
                    mode="automated_l4", idempotency_key=None)
            assert r.status == "rejected" and calls["n"] == 0
        finally:
            await eng.dispose()
    _run(go())


# ── concurrent Decision apply -> one dispatch (server-derived v1:decision:<id>) ────────────────

def test_pg_concurrent_decision_apply_one_dispatch(monkeypatch):
    _ensure_schema(monkeypatch)
    c = {"n": 0}
    _install_stub(monkeypatch, c, delay=0.05)

    async def go():
        from models.user import User
        from models.marketplace_connection import MarketplaceConnection
        from models.api_credential import ApiCredential
        from models.decision import Decision
        from services.marketplace import credential_vault
        from services import decision_apply
        eng, Session = _sessionmaker()
        try:
            uid = str(uuid.uuid4())
            did = str(uuid.uuid4())
            async with Session() as db:
                db.add(User(id=uid, email=uid + "@e.invalid", name="S",
                            hashed_password="x", is_verified=True))
                await db.flush()
                conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid,
                                             marketplace="wildberries", status="connected",
                                             scopes=["feedbacks"])
                db.add(conn)
                await db.flush()
                db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                                     secret_enc=credential_vault.encrypt("tok"), meta={}))
                db.add(Decision(id=did, user_id=uid, problem="p",
                                action_key="publish_review_response"))
                await db.commit()

            async def one():
                async with Session() as db:
                    return await decision_apply.apply_decision(
                        db=db, user_id=uid, decision_id=did,
                        overrides={"marketplace": "wildberries", "feedback_id": "fb1",
                                   "text": "Spasibo", "rating": 5})
            await asyncio.gather(one(), one(), return_exceptions=True)
            assert c["n"] == 1                       # both derive v1:decision:<id> -> one dispatch
        finally:
            await eng.dispose()
    _run(go())


# ── revert: one inverse, second revert 0, ambiguous revert not repeated ────────────────────────

def _install_set_price_stub(monkeypatch, calls, *, fail=None):
    from services.marketplace.wb_client import wb_client
    from services.marketplace.errors import ExecutionError

    async def _fake(*, token, offer_id, price, discount=None):
        calls["n"] += 1
        if fail:
            raise ExecutionError(fail, "boom")
        return {"requestId": "r"}
    monkeypatch.setattr(wb_client, "set_price", _fake)


async def _set_price(Session, uid, key):
    from services.marketplace import executor
    async with Session() as db:
        return await executor.execute(
            db=db, user_id=uid, action_type="set_price",
            payload={"marketplace": "wildberries", "offer_id": "OF1", "price": 100, "old_price": 90},
            mode="manual_l3", idempotency_key=key)


def test_pg_revert_one_inverse_then_second_revert_zero(monkeypatch):
    _ensure_schema(monkeypatch)
    calls = {"n": 0}
    _install_set_price_stub(monkeypatch, calls)

    async def go():
        from services.marketplace import executor, operation_key
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            r0 = await _set_price(Session, uid, operation_key.client_key(str(uuid.uuid4())))
            assert r0.status == "success" and calls["n"] == 1
            async with Session() as db:
                rv = await executor.revert(db=db, user_id=uid, log_id=r0.log_id)
            assert rv.status == "success" and calls["n"] == 2     # one inverse dispatch
            # second revert: original is now 'reverted' -> blocked at the guard, 0 dispatch
            raised = False
            async with Session() as db:
                try:
                    await executor.revert(db=db, user_id=uid, log_id=r0.log_id)
                except Exception:
                    raised = True
            assert raised and calls["n"] == 2
        finally:
            await eng.dispose()
    _run(go())


def test_pg_concurrent_revert_one_inverse(monkeypatch):
    _ensure_schema(monkeypatch)
    calls = {"n": 0}
    _install_set_price_stub(monkeypatch, calls)

    async def go():
        from services.marketplace import executor, operation_key
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            r0 = await _set_price(Session, uid, operation_key.client_key(str(uuid.uuid4())))
            assert calls["n"] == 1

            async def one():
                async with Session() as db:
                    return await executor.revert(db=db, user_id=uid, log_id=r0.log_id)
            await asyncio.gather(one(), one(), return_exceptions=True)
            assert calls["n"] == 2                    # original 1 + exactly one inverse
        finally:
            await eng.dispose()
    _run(go())


def test_pg_ambiguous_revert_not_repeated(monkeypatch):
    _ensure_schema(monkeypatch)
    calls = {"n": 0}
    _install_set_price_stub(monkeypatch, calls)     # original succeeds

    async def go():
        from services.marketplace import executor, operation_key
        from services.marketplace.errors import ExecutionError
        eng, Session = _sessionmaker()
        try:
            uid = await _seed_user(Session)
            r0 = await _set_price(Session, uid, operation_key.client_key(str(uuid.uuid4())))
            assert calls["n"] == 1
            _install_set_price_stub(monkeypatch, calls, fail=ExecutionError.TIMEOUT)  # inverse ambiguous
            async with Session() as db:
                rv = await executor.revert(db=db, user_id=uid, log_id=r0.log_id)
            assert rv.status == "ambiguous" and calls["n"] == 2
            _install_set_price_stub(monkeypatch, calls)     # would now succeed -> must NOT be called
            async with Session() as db:
                rv2 = await executor.revert(db=db, user_id=uid, log_id=r0.log_id)
            assert rv2.status == "needs_reconcile" and calls["n"] == 2   # ambiguous inverse not repeated
        finally:
            await eng.dispose()
    _run(go())
