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


async def _seed_user(Session):
    from models.marketplace_connection import MarketplaceConnection
    from models.api_credential import ApiCredential
    from services.marketplace import credential_vault
    uid = str(uuid.uuid4())
    async with Session() as db:
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
                                     status="connected", scopes=["feedbacks"])
        db.add(conn); await db.flush()
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                             secret_enc=credential_vault.encrypt("tok"), meta={}))
        await db.commit()
    return uid


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
            for bad in (None, "", "review:x", "v1:client:NOTUUID"):
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

    async def go():
        from alembic import command
        import db_migrations as dbm
        from models.execution_log import ExecutionLog
        cfg = dbm._alembic_config()
        # drop the unique index FIRST so two same-key rows can coexist, then attempt the upgrade
        command.downgrade(cfg, "efp1a2b3c4d01")
        eng, Session = _sessionmaker()
        uid = await _seed_user(Session)
        key = "v1:client:" + str(uuid.uuid4())
        async with Session() as db:
            for _ in range(2):
                db.add(ExecutionLog(id=str(uuid.uuid4()), user_id=uid, action_type="set_price",
                                    mode="manual_l3", payload={}, status="success",
                                    idempotency_key=key))
            await db.commit()
        await eng.dispose()
        with pytest.raises(Exception) as e:
            command.upgrade(cfg, "uqc1a2b3c4d01")
        msg = str(e.value)
        assert "preflight" in msg and key not in msg and uid not in msg   # numeric only, no PII
        global _SCHEMA_READY                                              # force clean rebuild next test
        _SCHEMA_READY = False

    _run(go())
