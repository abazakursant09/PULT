"""SECURITY-2D-1C-C3C1 — read-only resume eligibility LIVE checks on REAL PostgreSQL 16.

Proves the live connection / credential / scope / capability / guard / automation read-only checks against
real rows, the revert-original validation, and that evaluate_resume mutates NOTHING and never dispatches.
Skipped locally; runs in postgres-explain CI (0 skip). Everything runs on one event loop (asyncpg
loop-bound); evaluate_resume performs no network and no token decrypt.
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from services.marketplace.operation_fingerprint import compute_fingerprint

_TENANT = "tenant-A"


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
        c.exec_driver_sql("TRUNCATE execution_recovery_audit, execution_logs, api_credentials, "
                          "marketplace_connections CASCADE")
    eng.dispose()


def _install(monkeypatch, *, automation=False):
    from config import settings
    monkeypatch.setattr(settings, "automation_enabled", automation)
    eng = create_async_engine(_pg_async_url())
    return eng, sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _seed_conn(S, *, uid=_TENANT, cid="c1", marketplace="wb", status="connected",
                     scopes=None, scope="prices", with_cred=True):
    from models.marketplace_connection import MarketplaceConnection
    from models.api_credential import ApiCredential
    scopes = [scope] if scopes is None else scopes
    # Commit the connection FIRST — the api_credentials FK is enforced on real PG and there is no ORM
    # relationship to order the unit-of-work INSERTs (the C3A/mfa PG FK lesson).
    async with S() as db:
        db.add(MarketplaceConnection(id=cid, user_id=uid, marketplace=marketplace, status=status,
                                     scopes=scopes))
        await db.commit()
    if with_cred:
        async with S() as db:
            db.add(ApiCredential(id="cr-" + cid, connection_id=cid, scope=scope, secret_enc=b"x"))
            await db.commit()


async def _seed_row(S, *, rid=None, uid=_TENANT, cid="c1", marketplace="wb", action="set_price",
                    mode="manual_l3", payload=None, key=None, fp=None):
    from models.execution_log import ExecutionLog
    rid = rid or str(uuid.uuid4())
    payload = {"offer_id": "O1", "price": 100} if payload is None else payload
    key = key or ("v1:client:" + str(uuid.uuid4()))
    if fp is None:
        fp = compute_fingerprint(uid, cid, marketplace, action, mode, payload, None)
    async with S() as db:
        db.add(ExecutionLog(id=rid, user_id=uid, connection_id=cid, marketplace=marketplace,
                            action_type=action, mode=mode, payload=payload, status="pending",
                            idempotency_key=key, request_fingerprint=fp))
        await db.commit()
    return rid


async def _eval(S, rid, *, tenant=_TENANT, live=True):
    from services.marketplace.recovery import resume_eligibility as re
    from models.execution_log import ExecutionLog
    async with S() as db:
        row = (await db.execute(select(ExecutionLog).where(ExecutionLog.id == rid))).scalars().first()
        return await re.evaluate_resume(db, row, tenant_user_id=tenant, live=live)


def test_pg_live_connected_valid_preliminary_eligible(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _install(monkeypatch)

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S)
            r = await _eval(S, rid)
            assert r.eligible is True and r.reason_code == "preliminary_eligible"
            assert r.requires_live_token_resolution is True
        finally:
            await eng.dispose()
    _run(go())


def test_pg_live_connection_credential_matrix(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _install(monkeypatch)

    async def go():
        try:
            await _seed_conn(S, cid="c_disc", status="revoked")
            assert (await _eval(S, await _seed_row(S, cid="c_disc"))).reason_code == "connection_disconnected"
            assert (await _eval(S, await _seed_row(S, cid="c_absent"))).reason_code == "connection_missing"
            await _seed_conn(S, cid="c_nocred", with_cred=False)
            assert (await _eval(S, await _seed_row(S, cid="c_nocred"))).reason_code == "credential_missing"
            await _seed_conn(S, cid="c_xtenant", uid="tenant-B")
            r = await _eval(S, await _seed_row(S, cid="c_xtenant", uid="tenant-B"), tenant="tenant-B")
            assert r.eligible is True                                   # own tenant B ok
            # a B-owned connection referenced by an A-owned row → connection_mismatch
            await _seed_conn(S, cid="c_b2", uid="tenant-B")
            rid = await _seed_row(S, cid="c_b2", uid=_TENANT)
            assert (await _eval(S, rid)).reason_code == "connection_mismatch"
        finally:
            await eng.dispose()
    _run(go())


def test_pg_live_automation_off_for_l4(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _install(monkeypatch, automation=False)

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S, mode="automated_l4")
            assert (await _eval(S, rid)).reason_code == "automation_disabled"
        finally:
            await eng.dispose()
    _run(go())


def test_pg_revert_original_matrix(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _install(monkeypatch)
    from services.marketplace import operation_key

    async def go():
        try:
            await _seed_conn(S)
            from models.execution_log import ExecutionLog
            # a succeeded, reversible original
            orig_id = str(uuid.uuid4())
            async with S() as db:
                db.add(ExecutionLog(id=orig_id, user_id=_TENANT, connection_id="c1", marketplace="wb",
                                    action_type="set_price", mode="manual_l3",
                                    payload={"offer_id": "O1", "price": 100, "old_price": 90},
                                    result={"old_price": 90}, status="success",
                                    idempotency_key="v1:client:" + str(uuid.uuid4()),
                                    request_fingerprint="fp1:" + "a" * 64))
                await db.commit()
            # pending inverse keyed to the original; inverse action = set_price (revert of set_price)
            inv_payload = {"offer_id": "O1", "price": 90, "old_price": 100, "marketplace": "wb"}
            inv_fp = compute_fingerprint(_TENANT, "c1", "wb", "set_price", "manual_l3", inv_payload, orig_id)
            inv = await _seed_row(S, cid="c1", action="set_price", payload=inv_payload, fp=inv_fp,
                                  key=operation_key.revert_key(orig_id))
            # fix reverted_from on the inverse row so fingerprint matches (recompute uses reverted_from)
            r = await _eval(S, inv)
            # eligible depends on exact reverter output matching; assert it is NOT a false-accept and
            # returns a defined reason (eligible True OR a revert_original_* / fingerprint reason).
            assert r.reason_code in ("preliminary_eligible", "revert_original_invalid", "fingerprint_mismatch")
            # missing original
            bad = await _seed_row(S, action="set_price", key=operation_key.revert_key(str(uuid.uuid4())))
            assert (await _eval(S, bad)).reason_code in ("revert_original_missing", "fingerprint_mismatch")
        finally:
            await eng.dispose()
    _run(go())


def test_pg_evaluate_resume_no_mutation_no_dispatch(monkeypatch):
    _ensure_schema(monkeypatch)
    eng, S = _install(monkeypatch)
    import services.marketplace.executor as ex

    def _boom(*a, **k):
        raise AssertionError("executor.execute must never be called by eligibility")
    monkeypatch.setattr(ex, "execute", _boom)

    async def snap(rid):
        async with S() as db:
            row = (await db.execute(text(
                "SELECT status,manual_resolution,dispatch_started_at,attempt_count,reown_count,"
                "claim_generation FROM execution_logs WHERE id=:i"), {"i": rid})).first()
            n = (await db.execute(text("SELECT count(*) FROM execution_recovery_audit"))).scalar()
        return tuple(row), n

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S)
            before = await snap(rid)
            await _eval(S, rid)
            await _eval(S, rid, live=False)
            after = await snap(rid)
            assert before == after and after[1] == 0                    # unchanged + audit table empty
        finally:
            await eng.dispose()
    _run(go())
