"""SECURITY-2D-1C-C3A — read-only operator recovery on REAL PostgreSQL 16.

Proves the server-side tenant scope, neutral cross-tenant 404, keyset pagination, the strict response
allowlist (no forbidden field/value, no operator key leak), stable non-reversible target_reference, the
supported_for_retry matrix, that a GET mutates NOTHING (audit stays empty), and the additive migration
up/down/re-up (partial UNIQUE + existing rows preserved, audit table created empty). Provider/executor
calls: ZERO (this contour imports neither). Skipped locally; runs in postgres-explain CI (0 skip there).

Every DB call for one test runs inside a SINGLE go() coroutine on ONE fresh event loop (asyncpg
connections are loop-bound — the 2C-4A lesson); the engine is disposed inside that same go().
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from services.marketplace.operation_fingerprint import compute_fingerprint

_TENANT = "tenant-A"
_OTHER = "tenant-B"


def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_async_url():
    return (_pg_sync_url() or "").replace("+psycopg2", "+asyncpg").replace(
        "postgresql://", "postgresql+asyncpg://")


def _pg_alembic_url():
    return os.environ.get("PULT_TEST_PG_ALEMBIC_URL") or _pg_async_url()


pytestmark = pytest.mark.skipif(
    not (_pg_sync_url() or "").startswith("postgres"),
    reason="BLOCKED_ENVIRONMENT: no PostgreSQL; runs in postgres-explain CI.")

_SCHEMA_READY = False


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ensure_schema(monkeypatch):
    global _SCHEMA_READY
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    if not _SCHEMA_READY:
        from alembic import command
        import db_migrations as dbm
        eng = create_engine(_pg_sync_url())
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(dbm._alembic_config(), "head")
        eng.dispose()
        _SCHEMA_READY = True
    eng = create_engine(_pg_sync_url())
    with eng.begin() as c:
        c.exec_driver_sql("TRUNCATE execution_logs CASCADE")
    eng.dispose()


def _set_perimeter(monkeypatch, *, enabled=True, key="OP-KEY", tenant=_TENANT):
    """Set ONLY the operator perimeter settings (no engine) — for auth tests that never touch the DB."""
    from config import settings
    from routers import internal_recovery as ir
    monkeypatch.setattr(settings, "recovery_operator_enabled", enabled)
    monkeypatch.setattr(settings, "recovery_operator_api_key", key)
    monkeypatch.setattr(settings, "recovery_operator_id", "operator-1")
    monkeypatch.setattr(settings, "recovery_operator_user_id", tenant)
    return ir


def _install(monkeypatch, *, key="OP-KEY", tenant=_TENANT):
    """Perimeter + a PG-bound AsyncSessionLocal wired into the router. Engine is disposed inside go()."""
    ir = _set_perimeter(monkeypatch, enabled=True, key=key, tenant=tenant)
    eng = create_async_engine(_pg_async_url())
    Session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(ir, "AsyncSessionLocal", Session)
    ctx = ir._OperatorContext(operator_id="operator-1", user_id=tenant)
    return ir, eng, Session, ctx


async def _seed(Session, *, rid=None, uid=_TENANT, status="pending", dsa=False,
                action_type="set_price", payload=None, key=None, fp=None,
                recon=None, reown=0, attempt=0, manual=None, age_s=3600):
    from models.execution_log import ExecutionLog
    rid = rid or str(uuid.uuid4())
    payload = {"offer_id": "OFF-RAW-42", "price": 1000} if payload is None else payload
    key = key or ("v1:client:" + str(uuid.uuid4()))
    if fp is None:
        fp = compute_fingerprint(uid, "c1", "wb", action_type, "manual_l3", payload, None)
    async with Session() as db:
        db.add(ExecutionLog(
            id=rid, user_id=uid, connection_id="c1", marketplace="wb", action_type=action_type,
            mode="manual_l3", payload=payload, status=status, idempotency_key=key,
            request_fingerprint=fp, reconciliation_status=recon, reown_count=reown,
            attempt_count=attempt, manual_resolution=manual,
            created_at=datetime.utcnow() - timedelta(seconds=age_s),
            dispatch_started_at=datetime.now(timezone.utc) if dsa else None))
        await db.commit()
    return rid


# 1. flag OFF → neutral 404 before any DB (dependency raises before a session is opened)
def test_pg_flag_off_neutral_404(monkeypatch):
    ir = _set_perimeter(monkeypatch, enabled=False)
    with pytest.raises(HTTPException) as ei:
        ir._require_operator(x_internal_key="OP-KEY")
    assert ei.value.status_code == 404


# 2. wrong / missing key → 403
def test_pg_wrong_or_missing_key_403(monkeypatch):
    ir = _set_perimeter(monkeypatch, enabled=True, key="RIGHT")
    for h in (None, "", "WRONG"):
        with pytest.raises(HTTPException) as ei:
            ir._require_operator(x_internal_key=h)
        assert ei.value.status_code == 403


# 3. cookie / Bearer / seller session carry no X-Internal-Key → denied
def test_pg_cookie_or_bearer_denied(monkeypatch):
    ir = _set_perimeter(monkeypatch, enabled=True, key="RIGHT")
    with pytest.raises(HTTPException) as ei:
        ir._require_operator(x_internal_key=None)   # a browser cookie/Bearer sends no operator key
    assert ei.value.status_code == 403


# 4. tenant scope — only the configured tenant's disputed rows are listed
def test_pg_tenant_scope_only_configured_user(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            mine = await _seed(S, uid=_TENANT, status="pending")
            await _seed(S, uid=_TENANT, status="success")          # NOT disputed → excluded
            await _seed(S, uid=_OTHER, status="pending")            # other tenant → excluded
            res = await ir.list_operations(Response(), ctx, cursor_created=None, cursor_id=None, limit=50)
            assert {i.log_id for i in res.items} == {mine}
        finally:
            await eng.dispose()
    _run(go())


# 5. cross-tenant detail → neutral 404 (same as nonexistent)
def test_pg_cross_tenant_detail_404(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            other = await _seed(S, uid=_OTHER, status="pending")
            with pytest.raises(HTTPException) as ei:
                await ir.get_operation(other, Response(), ctx)
            assert ei.value.status_code == 404
            with pytest.raises(HTTPException) as ei2:
                await ir.get_operation("does-not-exist", Response(), ctx)
            assert ei2.value.status_code == 404
        finally:
            await eng.dispose()
    _run(go())


# 6. keyset pagination — no dup, no gap, terminates
def test_pg_list_keyset_pagination(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            seeded = set()
            for i in range(5):
                seeded.add(await _seed(S, uid=_TENANT, status="pending", age_s=5000 - i))
            seen, cursor, pages = [], None, 0
            while True:
                cc = cursor.created_at if cursor else None
                ci = cursor.log_id if cursor else None
                res = await ir.list_operations(Response(), ctx, cursor_created=cc, cursor_id=ci, limit=2)
                seen.extend(i.log_id for i in res.items)
                pages += 1
                cursor = res.next_cursor
                if cursor is None or pages > 10:
                    break
            assert len(seen) == len(set(seen)) == 5          # no duplicates
            assert set(seen) == seeded                        # full, exact coverage
        finally:
            await eng.dispose()
    _run(go())


# 7. response contains no forbidden field / value, and not the operator key
def test_pg_response_has_no_forbidden_field_or_value(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, eng, S, ctx = _install(monkeypatch, key="SECRET-OP-KEY")

    async def go():
        try:
            await _seed(S, uid=_TENANT, status="pending",
                        payload={"offer_id": "OFF-RAW-42", "price": 1000})
            res = await ir.list_operations(Response(), ctx, cursor_created=None, cursor_id=None, limit=50)
            dumped = res.items[0].model_dump()
            forbidden_keys = {"user_id", "connection_id", "idempotency_key", "request_fingerprint",
                              "payload", "api_request_id", "result", "error_code"}
            assert not (forbidden_keys & set(dumped.keys()))
            blob = repr(dumped)
            for secret in ("OFF-RAW-42", "SECRET-OP-KEY", "fp1:", "v1:client:"):
                assert secret not in blob, f"forbidden value {secret!r} leaked into response"
            assert dumped["target_reference"].startswith("tgt:")
        finally:
            await eng.dispose()
    _run(go())


# 8. target_reference stable after operator-key rotation
def test_pg_target_reference_stable_after_key_rotation(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, eng, S, ctx = _install(monkeypatch, key="KEY-1")

    async def go():
        try:
            await _seed(S, uid=_TENANT, status="pending", payload={"offer_id": "AAA", "price": 1})
            r1 = await ir.list_operations(Response(), ctx, cursor_created=None, cursor_id=None, limit=50)
            ref_a1 = r1.items[0].target_reference
            from config import settings
            monkeypatch.setattr(settings, "recovery_operator_api_key", "KEY-2-rotated")
            r2 = await ir.list_operations(Response(), ctx, cursor_created=None, cursor_id=None, limit=50)
            ref_a2 = r2.items[0].target_reference
            assert ref_a1 == ref_a2 and ref_a1.startswith("tgt:")
        finally:
            await eng.dispose()
    _run(go())


# 9. different targets → different references
def test_pg_target_reference_distinct_targets(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            await _seed(S, uid=_TENANT, status="pending", age_s=100,
                        payload={"offer_id": "AAA", "price": 1})
            await _seed(S, uid=_TENANT, status="pending", age_s=200,
                        payload={"offer_id": "BBB", "price": 1})
            res = await ir.list_operations(Response(), ctx, cursor_created=None, cursor_id=None, limit=50)
            refs = {i.target_reference for i in res.items}
            assert len(refs) == 2
        finally:
            await eng.dispose()
    _run(go())


# 10. supported_for_retry matrix on real rows
def test_pg_supported_for_retry_matrix(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            safe = await _seed(S, uid=_TENANT, status="pending")
            inflight = await _seed(S, uid=_TENANT, status="in_flight", dsa=True)
            ambiguous = await _seed(S, uid=_TENANT, status="ambiguous", dsa=True)
            tno = await _seed(S, uid=_TENANT, status="pending", recon="target_not_observed")
            res = await ir.list_operations(Response(), ctx, cursor_created=None, cursor_id=None, limit=50)
            m = {i.log_id: (i.supported_for_retry, i.reason_code) for i in res.items}
            assert m[safe] == (True, "eligibility_safe_pending_preliminary")
            assert m[inflight][0] is False
            assert m[ambiguous][0] is False
            assert m[tno] == (False, "eligibility_under_reconciliation")
        finally:
            await eng.dispose()
    _run(go())


# 11. a GET mutates NOTHING on the ExecutionLog row and never writes the audit table
def test_pg_get_does_not_mutate(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, eng, S, ctx = _install(monkeypatch)

    async def snapshot(rid):
        async with S() as db:
            row = (await db.execute(text(
                "SELECT status, manual_resolution, claim_generation, dispatch_started_at, attempt_count, "
                "reown_count, reconciliation_status, reconciliation_attempts FROM execution_logs "
                "WHERE id=:i"), {"i": rid})).first()
            return tuple(row)

    async def go():
        try:
            rid = await _seed(S, uid=_TENANT, status="pending")
            before = await snapshot(rid)
            await ir.get_operation(rid, Response(), ctx)
            await ir.list_operations(Response(), ctx, cursor_created=None, cursor_id=None, limit=50)
            after = await snapshot(rid)
            async with S() as db:
                n = (await db.execute(text("SELECT count(*) FROM execution_recovery_audit"))).scalar()
            assert before == after
            assert n == 0
        finally:
            await eng.dispose()
    _run(go())


# 12. additive migration up/down/re-up on real PG — partial UNIQUE + rows preserved, audit created empty
def test_pg_migration_seeded_roundtrip(monkeypatch):
    global _SCHEMA_READY
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    from alembic import command
    import db_migrations as dbm
    cfg = dbm._alembic_config()
    eng = create_engine(_pg_sync_url())
    _SCHEMA_READY = False

    def _cols(c):
        return {r[0] for r in c.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns WHERE table_name='execution_logs'")}

    def _tables(c):
        return {r[0] for r in c.exec_driver_sql(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    try:
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(cfg, "rwn1a2b3c4d01")
        with eng.begin() as c:
            assert "manual_resolution" not in _cols(c)
            c.exec_driver_sql("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                              "idempotency_key) VALUES('L1','u','set_price','manual_l3','{}','pending',"
                              "'v1:client:3f2504e0-4f89-41d3-9a0c-0305e82c3301')")
        command.upgrade(cfg, "rop1a2b3c4d01")
        with eng.begin() as c:
            assert {"manual_resolution", "resolved_by", "resolved_at"} <= _cols(c)
            assert "execution_recovery_audit" in _tables(c)
            assert c.exec_driver_sql("SELECT count(*) FROM execution_recovery_audit").scalar() == 0
            assert c.exec_driver_sql("SELECT count(*) FROM execution_logs").scalar() == 1
        # partial UNIQUE preserved: a second v1-key claim for the same user collides (own txn — the
        # IntegrityError aborts only this transaction, not the assertions above).
        with pytest.raises(Exception):
            with eng.begin() as c:
                c.exec_driver_sql(
                    "INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,idempotency_key)"
                    " VALUES('L2','u','set_price','manual_l3','{}','pending',"
                    "'v1:client:3f2504e0-4f89-41d3-9a0c-0305e82c3301')")
        command.downgrade(cfg, "rwn1a2b3c4d01")
        with eng.begin() as c:
            assert not ({"manual_resolution", "resolved_by", "resolved_at"} & _cols(c))
            assert "execution_recovery_audit" not in _tables(c)
            assert c.exec_driver_sql("SELECT count(*) FROM execution_logs").scalar() == 1
        command.upgrade(cfg, "rop1a2b3c4d01")
        with eng.begin() as c:
            assert "manual_resolution" in _cols(c) and "execution_recovery_audit" in _tables(c)
    finally:
        eng.dispose()
        _SCHEMA_READY = False
