"""SECURITY-2D-1C-B — recovery sweep on REAL PostgreSQL (advisory lock, isolation, read-only contract).

Skipped locally; runs in the postgres-explain CI job (0 skip). A provider stub counts READ and WRITE
calls separately — WRITE must be 0 in every case.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_async_url():
    sync = _pg_sync_url() or ""
    return sync.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


def _pg_alembic_url():
    return os.environ.get("PULT_TEST_PG_ALEMBIC_URL") or _pg_async_url()


pytestmark = pytest.mark.skipif(
    not (_pg_sync_url() or "").startswith("postgres"),
    reason="BLOCKED_ENVIRONMENT: no PostgreSQL; runs in the postgres-explain CI job.")

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


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Provider:
    def __init__(self, price=None):
        self.reads = 0
        self.writes = 0
        self._price = price

    async def list_prices(self, *, token, offset=0, limit=1000):
        self.reads += 1
        return [{"nmID": "OF1", "price": self._price}] if self._price is not None else []

    async def set_price(self, **kw):
        self.writes += 1
        return {"requestId": "x"}


async def _fake_resolve(db, row):
    from types import SimpleNamespace
    return "wb", "tok", None, SimpleNamespace(ozon_client_id=None)


def _install(monkeypatch, *, enabled=True, dry_run=False, price=100):
    import sqlalchemy as sa
    from config import settings
    from services.marketplace.recovery import recovery_sweep as rs
    from services.marketplace.recovery import reconcile_read
    import services.marketplace.wb_client as wbmod
    # The PG schema is shared across tests (built once); start each test from a clean slate so candidate
    # counts are deterministic — otherwise rows from earlier tests accumulate.
    sync_eng = sa.create_engine(_pg_sync_url())
    with sync_eng.begin() as c:
        c.exec_driver_sql("TRUNCATE execution_logs, api_credentials, marketplace_connections CASCADE")
    sync_eng.dispose()
    monkeypatch.setattr(settings, "recovery_reaper_enabled", enabled)
    monkeypatch.setattr(settings, "recovery_reaper_dry_run", dry_run)
    eng = create_async_engine(_pg_async_url())
    Session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(rs, "engine", eng)
    monkeypatch.setattr(rs, "AsyncSessionLocal", Session)
    prov = _Provider(price=price)
    monkeypatch.setattr(reconcile_read, "_resolve", _fake_resolve)
    monkeypatch.setattr(wbmod, "wb_client", prov)
    return rs, eng, Session, prov


def _fp(uid, conn_id, action, payload):
    from services.marketplace.executor import _fingerprint
    return _fingerprint(uid, conn_id, "wildberries", action, "manual_l3", payload, None)


async def _seed(Session, *, action="set_price", status="ambiguous", payload=None, bad_fp=False,
                age_s=100000, uid=None):
    from models.marketplace_connection import MarketplaceConnection
    from models.api_credential import ApiCredential
    from models.execution_log import ExecutionLog
    from services.marketplace import credential_vault
    uid = uid or str(uuid.uuid4())
    conn_id = str(uuid.uuid4())
    payload = payload if payload is not None else {"marketplace": "wildberries", "offer_id": "OF1",
                                                   "price": 100, "old_price": 90}
    async with Session() as db:
        db.add(MarketplaceConnection(id=conn_id, user_id=uid, marketplace="wildberries",
                                     status="connected", scopes=["prices"]))
        await db.flush()
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn_id, scope="prices",
                             secret_enc=credential_vault.encrypt("tok"), meta={}))
        fp = "fp1:" + "0" * 64 if bad_fp else _fp(uid, conn_id, action, payload)
        db.add(ExecutionLog(id=str(uuid.uuid4()), user_id=uid, connection_id=conn_id,
                            marketplace="wildberries", action_type=action, mode="manual_l3",
                            payload=payload, status=status, request_fingerprint=fp,
                            created_at=datetime.utcnow() - timedelta(seconds=age_s)))
        await db.commit()
    return uid


async def _row(Session, uid):
    from models.execution_log import ExecutionLog
    async with Session() as db:
        return (await db.execute(select(ExecutionLog).where(
            ExecutionLog.user_id == uid))).scalars().first()


def test_pg_two_concurrent_sweeps_one_lock_owner(monkeypatch):
    _ensure_schema(monkeypatch)

    async def go():
        rs, eng, Session, prov = _install(monkeypatch, enabled=True, dry_run=False, price=100)
        try:
            await _seed(Session)
            r1, r2 = await asyncio.gather(rs.run_recovery_sweep(dry_run=False),
                                          rs.run_recovery_sweep(dry_run=False))
            owners = [r for r in (r1, r2) if r.lock_acquired]
            assert len(owners) == 1                       # exactly one advisory-lock owner
            assert prov.writes == 0
        finally:
            await eng.dispose()
    _run(go())


def test_pg_lock_released_after_run(monkeypatch):
    _ensure_schema(monkeypatch)

    async def go():
        rs, eng, Session, prov = _install(monkeypatch, enabled=True, dry_run=False, price=100)
        try:
            await _seed(Session)
            r1 = await rs.run_recovery_sweep(dry_run=False)
            r2 = await rs.run_recovery_sweep(dry_run=False)   # must be able to re-acquire
            assert r1.lock_acquired and r2.lock_acquired and prov.writes == 0
        finally:
            await eng.dispose()
    _run(go())


def test_pg_user_error_does_not_stop_others(monkeypatch):
    _ensure_schema(monkeypatch)

    async def go():
        rs, eng, Session, prov = _install(monkeypatch, enabled=True, dry_run=False, price=100)
        from services.marketplace.recovery import reconcile_read
        bad_uid = str(uuid.uuid4())

        async def flaky_observe(db, row):
            if row.user_id == bad_uid:
                raise RuntimeError("boom")
            return reconcile_read.INTENT_OBSERVED
        monkeypatch.setattr(reconcile_read, "observe", flaky_observe)
        try:
            await _seed(Session, uid=bad_uid)
            good = await _seed(Session)
            r = await rs.run_recovery_sweep(dry_run=False)
            assert r.failed_users == 1
            assert (await _row(Session, good)).reconciliation_status == "intent_observed"
            assert prov.writes == 0
        finally:
            await eng.dispose()
    _run(go())


def test_pg_intent_observed_writes_only_recon(monkeypatch):
    _ensure_schema(monkeypatch)

    async def go():
        rs, eng, Session, prov = _install(monkeypatch, enabled=True, dry_run=False, price=100)
        try:
            uid = await _seed(Session)
            r = await rs.run_recovery_sweep(dry_run=False)
            assert r.intent_observed == 1 and r.reconciled == 1 and prov.reads == 1 and prov.writes == 0
            row = await _row(Session, uid)
            assert row.reconciliation_status == "intent_observed"
            assert row.status == "ambiguous" and row.claim_generation == 0
        finally:
            await eng.dispose()
    _run(go())


def test_pg_not_observed_and_dry_run_and_mismatch(monkeypatch):
    _ensure_schema(monkeypatch)

    async def go():
        # not_observed
        rs, eng, Session, prov = _install(monkeypatch, enabled=True, dry_run=False, price=999)
        try:
            uid = await _seed(Session)
            r = await rs.run_recovery_sweep(dry_run=False)
            assert r.not_observed == 1 and prov.writes == 0
            assert (await _row(Session, uid)).reconciliation_status == "not_observed"
        finally:
            await eng.dispose()
        # dry-run writes nothing
        rs, eng, Session, prov = _install(monkeypatch, enabled=True, dry_run=True, price=100)
        try:
            uid = await _seed(Session)
            r = await rs.run_recovery_sweep()
            assert r.candidates == 1 and r.reconciled == 0 and prov.writes == 0
            assert (await _row(Session, uid)).reconciliation_status is None
        finally:
            await eng.dispose()
        # fingerprint mismatch → fail-closed, no provider read
        rs, eng, Session, prov = _install(monkeypatch, enabled=True, dry_run=False, price=100)
        try:
            uid = await _seed(Session, bad_fp=True)
            r = await rs.run_recovery_sweep(dry_run=False)
            assert r.fingerprint_mismatches == 1 and prov.reads == 0 and prov.writes == 0
            assert (await _row(Session, uid)).reconciliation_status == "still_unknown"
        finally:
            await eng.dispose()
    _run(go())


def test_pg_read_timeout_is_still_unknown(monkeypatch):
    _ensure_schema(monkeypatch)

    async def go():
        rs, eng, Session, prov = _install(monkeypatch, enabled=True, dry_run=False, price=100)
        import services.marketplace.wb_client as wbmod

        async def timeout(*a, **k):
            prov.reads += 1
            raise TimeoutError("5xx")
        prov.list_prices = timeout                     # provider read raises
        monkeypatch.setattr(wbmod, "wb_client", prov)
        try:
            uid = await _seed(Session)
            r = await rs.run_recovery_sweep(dry_run=False)
            assert r.still_unknown == 1 and prov.writes == 0   # error → still_unknown, never confirmed
            row = await _row(Session, uid)
            assert row.reconciliation_status == "still_unknown" and row.status == "ambiguous"
        finally:
            await eng.dispose()
    _run(go())


def test_pg_flag_off_zero_everything(monkeypatch):
    _ensure_schema(monkeypatch)

    async def go():
        rs, eng, Session, prov = _install(monkeypatch, enabled=False, dry_run=False, price=100)
        try:
            uid = await _seed(Session)
            r = await rs.run_recovery_sweep()
            assert r.enabled is False and r.candidates == 0 and prov.reads == 0 and prov.writes == 0
            assert (await _row(Session, uid)).reconciliation_status is None
        finally:
            await eng.dispose()
    _run(go())
