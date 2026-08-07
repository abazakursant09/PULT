"""SECURITY-2D-1C-C3B — manual operator resolution on REAL PostgreSQL 16.

Proves the fail-closed perimeter (flag / key / tenant), atomic resolution + audit, global idempotency
(cached versus mismatch on a different log / action / reason), concurrency (one audit under a duplicate,
serialized history under different decisions), rollback on error, append-only plus no mutation of
technical fields, zero provider / executor calls, forbidden-value scan, correction history, and the
migration roundtrip. Endpoint coroutines are awaited DIRECTLY on one event loop (asyncpg is loop-bound).
Skipped locally, runs in postgres-explain CI with 0 skip.
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine, text, select
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
        c.exec_driver_sql("TRUNCATE execution_recovery_audit, execution_logs CASCADE")
    eng.dispose()


def _set_perimeter(monkeypatch, *, enabled=True, key="OP-KEY", tenant=_TENANT):
    from config import settings
    from routers import internal_recovery as ir
    monkeypatch.setattr(settings, "recovery_operator_enabled", enabled)
    monkeypatch.setattr(settings, "recovery_operator_api_key", key)
    monkeypatch.setattr(settings, "recovery_operator_id", "operator-1")
    monkeypatch.setattr(settings, "recovery_operator_user_id", tenant)
    return ir


def _install(monkeypatch, *, key="OP-KEY", tenant=_TENANT):
    ir = _set_perimeter(monkeypatch, enabled=True, key=key, tenant=tenant)
    eng = create_async_engine(_pg_async_url())
    Session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    from services.marketplace.recovery import operator_resolve as orv
    monkeypatch.setattr(orv, "AsyncSessionLocal", Session)
    monkeypatch.setattr(ir, "AsyncSessionLocal", Session)
    ctx = ir._OperatorContext(operator_id="operator-1", user_id=tenant)
    return ir, orv, eng, Session, ctx


async def _seed(Session, *, rid=None, uid=_TENANT, status="pending", recon=None, dsa=False,
                key=None, fp=None, payload=None):
    from models.execution_log import ExecutionLog
    rid = rid or str(uuid.uuid4())
    payload = {"offer_id": "OFF-RAW-42", "price": 1000} if payload is None else payload
    key = key or ("v1:client:" + str(uuid.uuid4()))
    if fp is None:
        fp = compute_fingerprint(uid, "c1", "wb", "set_price", "manual_l3", payload, None)
    async with Session() as db:
        db.add(ExecutionLog(id=rid, user_id=uid, connection_id="c1", marketplace="wb",
                            action_type="set_price", mode="manual_l3", payload=payload, status=status,
                            idempotency_key=key, request_fingerprint=fp, reconciliation_status=recon,
                            dispatch_started_at=datetime.now(timezone.utc) if dsa else None))
        await db.commit()
    return rid


def _idem():
    return str(uuid.uuid4())


# 1. flag OFF -> 404 before DB (dependency raises before any session)
def test_pg_flag_off_neutral_404(monkeypatch):
    ir = _set_perimeter(monkeypatch, enabled=False)
    with pytest.raises(HTTPException) as e:
        ir._require_operator(x_internal_key="OP-KEY")
    assert e.value.status_code == 404


# 2. missing / wrong key -> 403
def test_pg_wrong_or_missing_key_403(monkeypatch):
    ir = _set_perimeter(monkeypatch, enabled=True, key="RIGHT")
    for h in (None, "", "WRONG"):
        with pytest.raises(HTTPException) as e:
            ir._require_operator(x_internal_key=h)
        assert e.value.status_code == 403


# 3. cookie/Bearer/seller (no X-Internal-Key) -> denied
def test_pg_cookie_or_bearer_denied(monkeypatch):
    ir = _set_perimeter(monkeypatch, enabled=True, key="RIGHT")
    with pytest.raises(HTTPException) as e:
        ir._require_operator(x_internal_key=None)
    assert e.value.status_code == 403


# 4. cross-tenant -> neutral 404
def test_pg_cross_tenant_404(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            other = await _seed(S, uid=_OTHER)
            with pytest.raises(HTTPException) as e:
                await ir.close_operation(other, Response(), ctx, _idem(), None)
            assert e.value.status_code == 404
        finally:
            await eng.dispose()
    _run(go())


# 5. confirm-applied -> atomic resolution + one audit row
def test_pg_confirm_applied_atomic(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            rid = await _seed(S)
            r = await ir.confirm_applied(rid, Response(), ctx, _idem(), None)
            assert r.idempotent_result.new_resolution == "confirmed_applied"
            assert r.current_operation.manual_resolution == "confirmed_applied"
            async with S() as db:
                n = (await db.execute(text("SELECT count(*) FROM execution_recovery_audit"))).scalar()
                mr = (await db.execute(text("SELECT manual_resolution FROM execution_logs WHERE id=:i"),
                                       {"i": rid})).scalar()
            assert n == 1 and mr == "confirmed_applied"
        finally:
            await eng.dispose()
    _run(go())


# 6. confirm-not-applied -> atomic resolution + one audit row
def test_pg_confirm_not_applied_atomic(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            rid = await _seed(S)
            r = await ir.confirm_not_applied(rid, Response(), ctx, _idem(), None)
            assert r.idempotent_result.new_resolution == "confirmed_not_applied"
            async with S() as db:
                n = (await db.execute(text("SELECT count(*) FROM execution_recovery_audit"))).scalar()
            assert n == 1
        finally:
            await eng.dispose()
    _run(go())


# 7. close -> atomic resolution + one audit row
def test_pg_close_atomic(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            rid = await _seed(S)
            r = await ir.close_operation(rid, Response(), ctx, _idem(), None)
            assert r.idempotent_result.new_resolution == "manual_closed"
            assert r.idempotent_result.reason_code == "operator_closed_no_action"
        finally:
            await eng.dispose()
    _run(go())


# 8. duplicate same idempotency -> one audit + CACHED
def test_pg_duplicate_idempotency_one_audit(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            rid = await _seed(S)
            cid = _idem()
            a = await ir.close_operation(rid, Response(), ctx, cid, None)
            b = await ir.close_operation(rid, Response(), ctx, cid, None)
            assert a.idempotent_result.result_code == "APPLIED"
            assert b.idempotent_result.result_code == "CACHED"
            assert a.idempotent_result.audit_id == b.idempotent_result.audit_id
            async with S() as db:
                n = (await db.execute(text("SELECT count(*) FROM execution_recovery_audit"))).scalar()
            assert n == 1
        finally:
            await eng.dispose()
    _run(go())


# 9. concurrent identical -> exactly one audit
def test_pg_concurrent_same_request_one_audit(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            rid = await _seed(S)
            cid = _idem()
            res = await asyncio.gather(
                ir.close_operation(rid, Response(), ctx, cid, None),
                ir.close_operation(rid, Response(), ctx, cid, None),
                return_exceptions=True)
            oks = [r for r in res if not isinstance(r, Exception)]
            assert len(oks) == 2
            codes = sorted(r.idempotent_result.result_code for r in oks)
            assert codes == ["APPLIED", "CACHED"]
            async with S() as db:
                n = (await db.execute(text("SELECT count(*) FROM execution_recovery_audit"))).scalar()
            assert n == 1
        finally:
            await eng.dispose()
    _run(go())


# 10/11/12. same key + different action / log / reason -> mismatch (409)
def test_pg_same_key_different_action_or_log_or_reason_mismatch(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)
    from routers.internal_recovery import ResolveBody

    async def go():
        try:
            r1 = await _seed(S)
            r2 = await _seed(S)
            cid = _idem()
            await ir.close_operation(r1, Response(), ctx, cid, None)
            calls = [
                ir.confirm_applied(r1, Response(), ctx, cid, None),
                ir.close_operation(r2, Response(), ctx, cid, None),
                ir.close_operation(r1, Response(), ctx, cid,
                                   ResolveBody(reason_code="stale_pending_review")),
            ]
            for call in calls:
                with pytest.raises(HTTPException) as e:
                    await call
                assert e.value.status_code == 409 and e.value.detail == "IDEMPOTENCY_MISMATCH"
        finally:
            await eng.dispose()
    _run(go())


# 13. concurrent different decisions serialize, both audits, consistent history
def test_pg_concurrent_different_decisions_serialize(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            rid = await _seed(S)
            res = await asyncio.gather(
                ir.confirm_applied(rid, Response(), ctx, _idem(), None),
                ir.confirm_not_applied(rid, Response(), ctx, _idem(), None),
                return_exceptions=True)
            assert all(not isinstance(r, Exception) for r in res)
            async with S() as db:
                n = (await db.execute(text("SELECT count(*) FROM execution_recovery_audit"))).scalar()
                mr = (await db.execute(text("SELECT manual_resolution FROM execution_logs WHERE id=:i"),
                                       {"i": rid})).scalar()
            assert n == 2
            assert mr in ("confirmed_applied", "confirmed_not_applied")
        finally:
            await eng.dispose()
    _run(go())


# 16. previous_status/resolution captured from the locked row
def test_pg_previous_captured_from_locked_row(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            rid = await _seed(S)
            a = await ir.confirm_applied(rid, Response(), ctx, _idem(), None)
            assert a.idempotent_result.previous_status == "pending"
            assert a.idempotent_result.previous_resolution is None
            b = await ir.close_operation(rid, Response(), ctx, _idem(), None)
            assert b.idempotent_result.previous_resolution == "confirmed_applied"
        finally:
            await eng.dispose()
    _run(go())


# 17/18. no technical field changes, only manual_resolution/resolved_by/at
def test_pg_technical_fields_unchanged(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def snap(rid):
        async with S() as db:
            return tuple((await db.execute(text(
                "SELECT status,dispatch_started_at,claim_generation,attempt_count,reown_count,"
                "reconciliation_status,reconciliation_attempts,idempotency_key,request_fingerprint,"
                "payload::text,result,reverted_from FROM execution_logs WHERE id=:i"), {"i": rid})).first())

    async def go():
        try:
            rid = await _seed(S)
            before = await snap(rid)
            await ir.confirm_applied(rid, Response(), ctx, _idem(), None)
            after = await snap(rid)
            assert before == after
        finally:
            await eng.dispose()
    _run(go())


# 19. zero provider/executor calls: even if executor.execute would raise, resolve never touches it
def test_pg_zero_provider_executor_calls(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)
    import services.marketplace.executor as ex

    def _boom(*a, **k):
        raise AssertionError("executor.execute must never be called by C3B")
    monkeypatch.setattr(ex, "execute", _boom)

    async def go():
        try:
            rid = await _seed(S)
            r = await ir.close_operation(rid, Response(), ctx, _idem(), None)
            assert r.idempotent_result.new_resolution == "manual_closed"
        finally:
            await eng.dispose()
    _run(go())


# 20. response/audit carry no forbidden values
def test_pg_response_no_forbidden_values(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch, key="SECRET-OP-KEY")

    async def go():
        try:
            rid = await _seed(S, payload={"offer_id": "OFF-RAW-42", "price": 1000})
            r = await ir.confirm_applied(rid, Response(), ctx, _idem(), None)
            blob = repr(r.idempotent_result.model_dump()) + repr(r.current_operation.model_dump())
            for secret in ("OFF-RAW-42", "SECRET-OP-KEY", "fp1:", "v1:client:"):
                assert secret not in blob, f"leaked {secret!r}"
        finally:
            await eng.dispose()
    _run(go())


# 21/26. correction creates a NEW audit, earlier audit unchanged, cached stays original
def test_pg_correction_new_audit_old_unchanged(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)
    from models import ExecutionRecoveryAudit

    async def go():
        try:
            rid = await _seed(S)
            cid1 = _idem()
            await ir.confirm_applied(rid, Response(), ctx, cid1, None)
            await ir.confirm_not_applied(rid, Response(), ctx, _idem(), None)
            cached = await ir.confirm_applied(rid, Response(), ctx, cid1, None)
            assert cached.idempotent_result.result_code == "CACHED"
            assert cached.idempotent_result.new_resolution == "confirmed_applied"
            async with S() as db:
                rows = (await db.execute(select(ExecutionRecoveryAudit).where(
                    ExecutionRecoveryAudit.execution_log_id == rid))).scalars().all()
            assert len(rows) == 2
            orig = [x for x in rows if x.correlation_id == cid1][0]
            assert orig.new_resolution == "confirmed_applied"
        finally:
            await eng.dispose()
    _run(go())


# 22. manual_closed transition matrix incl re-open
def test_pg_manual_closed_transition_matrix(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            rid = await _seed(S)
            c = await ir.close_operation(rid, Response(), ctx, _idem(), None)
            assert c.current_operation.manual_resolution == "manual_closed"
            reopen = await ir.confirm_applied(rid, Response(), ctx, _idem(), None)
            assert reopen.current_operation.manual_resolution == "confirmed_applied"
        finally:
            await eng.dispose()
    _run(go())


# 23. malformed/fingerprint-mismatch: confirm denied (409), close allowed
def test_pg_unverified_confirm_denied_close_allowed(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            bad = await _seed(S, fp="fp1:" + "b" * 64)
            with pytest.raises(HTTPException) as e:
                await ir.confirm_applied(bad, Response(), ctx, _idem(), None)
            assert e.value.status_code == 409
            assert e.value.detail == "UNVERIFIED_OPERATION_ONLY_CLOSE_ALLOWED"
            r = await ir.close_operation(bad, Response(), ctx, _idem(), None)
            assert r.current_operation.manual_resolution == "manual_closed"
            legacy = await _seed(S, key="review:not-a-uuid")
            with pytest.raises(HTTPException) as e2:
                await ir.confirm_not_applied(legacy, Response(), ctx, _idem(), None)
            assert e2.value.status_code == 409
        finally:
            await eng.dispose()
    _run(go())


# 24. non-disputed same-tenant -> 409 NOT_OPEN; recon-attention row IS disputed
def test_pg_non_disputed_409_not_open(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            for st in ("success", "reverted", "rejected"):
                rid = await _seed(S, status=st)
                with pytest.raises(HTTPException) as e:
                    await ir.close_operation(rid, Response(), ctx, _idem(), None)
                assert e.value.status_code == 409
                assert e.value.detail == "OPERATION_NOT_OPEN_FOR_MANUAL_RESOLUTION"
            rid2 = await _seed(S, status="failed", recon="still_unknown")
            ok = await ir.close_operation(rid2, Response(), ctx, _idem(), None)
            assert ok.current_operation.manual_resolution == "manual_closed"
        finally:
            await eng.dispose()
    _run(go())


# 25. lookalike CSRF paths are not exempt (predicate)
def test_pg_csrf_only_three_posts_exempt():
    from csrf import _is_recovery_resolution_post as ex
    uid = str(uuid.uuid4())
    assert ex("POST", f"/api/internal/recovery/operations/{uid}/close") is True
    assert ex("POST", f"/api/internal/recovery/operations/{uid}/authorize-retry") is False
    assert ex("PATCH", f"/api/internal/recovery/operations/{uid}/close") is False


# 27. rollback path has no MissingGreenlet/expired-object error (mismatch resolves cleanly)
def test_pg_mismatch_path_no_greenlet_error(monkeypatch):
    _ensure_schema(monkeypatch)
    ir, orv, eng, S, ctx = _install(monkeypatch)

    async def go():
        try:
            r1 = await _seed(S)
            r2 = await _seed(S)
            cid = _idem()
            await ir.close_operation(r1, Response(), ctx, cid, None)
            with pytest.raises(HTTPException) as e:
                await ir.close_operation(r2, Response(), ctx, cid, None)
            assert e.value.status_code == 409
        finally:
            await eng.dispose()
    _run(go())


# 28. migration seeded up/down/re-up: global UNIQUE added, composite/FK/CHECK kept, rows preserved
def test_pg_migration_seeded_roundtrip(monkeypatch):
    global _SCHEMA_READY
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    from alembic import command
    import db_migrations as dbm
    cfg = dbm._alembic_config()
    eng = create_engine(_pg_sync_url())
    _SCHEMA_READY = False

    def _cons(c):
        return {r[0] for r in c.exec_driver_sql(
            "SELECT conname FROM pg_constraint WHERE conrelid='execution_recovery_audit'::regclass")}
    try:
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(cfg, "rop1a2b3c4d01")
        with eng.begin() as c:
            assert "uq_recovery_audit_correlation" not in _cons(c)
            c.exec_driver_sql("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status) "
                              "VALUES('L1','u','set_price','manual_l3','{}','pending')")
            c.exec_driver_sql(
                "INSERT INTO execution_recovery_audit(id,execution_log_id,action,actor_id,reason_code,"
                "correlation_id,created_at) VALUES('a1','L1','close','op','operator_closed_no_action',"
                "'CID','2026-01-01T00:00:00+00')")
        command.upgrade(cfg, "rob1a2b3c4d01")
        with eng.begin() as c:
            cons = _cons(c)
            assert "uq_recovery_audit_correlation" in cons
            assert "uq_recovery_audit_op_corr_action" in cons
            assert c.exec_driver_sql("SELECT count(*) FROM execution_recovery_audit").scalar() == 1
        with pytest.raises(Exception):
            with eng.begin() as c:
                c.exec_driver_sql("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status)"
                                  " VALUES('L2','u','set_price','manual_l3','{}','pending')")
                c.exec_driver_sql(
                    "INSERT INTO execution_recovery_audit(id,execution_log_id,action,actor_id,reason_code,"
                    "correlation_id,created_at) VALUES('a2','L2','close','op','operator_closed_no_action',"
                    "'CID','2026-01-01T00:00:00+00')")
        command.downgrade(cfg, "rop1a2b3c4d01")
        with eng.begin() as c:
            cons = _cons(c)
            assert "uq_recovery_audit_correlation" not in cons
            assert "uq_recovery_audit_op_corr_action" in cons
            assert c.exec_driver_sql("SELECT count(*) FROM execution_recovery_audit").scalar() == 1
        command.upgrade(cfg, "rob1a2b3c4d01")
    finally:
        eng.dispose()
        _SCHEMA_READY = False
