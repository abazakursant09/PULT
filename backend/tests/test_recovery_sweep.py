"""SECURITY-2D-1C-B — recovery sweep behaviour on SQLite (flag gate, dry-run, verdicts, fail-closed).

True cross-connection concurrency + advisory-lock behaviour is proven on real PostgreSQL
(test_recovery_sweep_pg.py). Here we prove the read-only contract: OFF does nothing, dry-run writes
nothing, unsupported actions never call a provider, a fingerprint mismatch is fail-closed, and a real
run writes ONLY the reconciliation columns (never status / claim_generation) with ZERO provider writes.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
from services.marketplace import credential_vault
from services.marketplace.executor import _fingerprint
from services.marketplace.recovery import recovery_sweep as rs


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Provider:
    """Counts provider READ and WRITE calls separately. WRITE must stay 0 in every recovery test."""
    def __init__(self, price=None):
        self.reads = 0
        self.writes = 0
        self._price = price

    async def list_prices(self, *, token, offset=0, limit=1000):
        self.reads += 1
        return [{"nmID": "OF1", "price": self._price}] if self._price is not None else []

    async def set_price(self, **kw):        # a WRITE — must never be called by recovery
        self.writes += 1
        return {"requestId": "x"}


async def _install(monkeypatch, *, enabled=True, dry_run=False, price=None):
    from config import settings
    monkeypatch.setattr(settings, "recovery_reaper_enabled", enabled)
    monkeypatch.setattr(settings, "recovery_reaper_dry_run", dry_run)
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    Session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(rs, "engine", eng)
    monkeypatch.setattr(rs, "AsyncSessionLocal", Session)
    prov = _Provider(price=price)
    from services.marketplace.recovery import reconcile_read
    import services.marketplace.wb_client as wbmod
    monkeypatch.setattr(reconcile_read, "_resolve", _fake_resolve)      # deterministic wb resolution
    monkeypatch.setattr(wbmod, "wb_client", prov)
    return eng, Session, prov


async def _fake_resolve(db, row):
    # ("wb", token, account_ref, connection-with-ozon_client_id)
    from types import SimpleNamespace
    return "wb", "tok", None, SimpleNamespace(ozon_client_id=None)


def _fp(uid, conn_id, action, payload):
    return _fingerprint(uid, conn_id, "wildberries", action, "manual_l3", payload, None)


async def _seed(Session, *, action="set_price", status="ambiguous", payload=None, bad_fp=False,
                age_s=100000):
    uid = str(uuid.uuid4())
    conn_id = str(uuid.uuid4())
    payload = payload if payload is not None else {"marketplace": "wildberries", "offer_id": "OF1",
                                                   "price": 100, "old_price": 90}
    async with Session() as db:
        db.add(MarketplaceConnection(id=conn_id, user_id=uid, marketplace="wildberries",
                                     status="connected", scopes=["prices", "feedbacks"]))
        await db.flush()
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn_id, scope="prices",
                             secret_enc=credential_vault.encrypt("tok"), meta={}))
        fp = "fp1:" + "0" * 64 if bad_fp else _fp(uid, conn_id, action, payload)
        old = datetime.utcnow() - timedelta(seconds=age_s)
        db.add(ExecutionLog(id=str(uuid.uuid4()), user_id=uid, connection_id=conn_id,
                            marketplace="wildberries", action_type=action, mode="manual_l3",
                            payload=payload, status=status, request_fingerprint=fp, created_at=old))
        await db.commit()
    return uid


async def _row(Session, uid):
    async with Session() as db:
        return (await db.execute(select(ExecutionLog).where(ExecutionLog.user_id == uid))).scalars().first()


def test_flag_off_does_nothing(monkeypatch):
    async def go():
        eng, Session, prov = await _install(monkeypatch, enabled=False)
        await _seed(Session)
        r = await rs.run_recovery_sweep()
        assert r.enabled is False and r.candidates == 0 and prov.reads == 0 and prov.writes == 0
        row = await _row(Session, (await _users(Session))[0])
        assert row.reconciliation_status is None and row.reconciliation_attempts == 0
        await eng.dispose()
    _run(go())


async def _users(Session):
    async with Session() as db:
        return [u for (u,) in (await db.execute(select(ExecutionLog.user_id).distinct())).all()]


def test_dry_run_writes_nothing(monkeypatch):
    async def go():
        eng, Session, prov = await _install(monkeypatch, enabled=True, dry_run=True, price=100)
        uid = await _seed(Session)
        r = await rs.run_recovery_sweep()               # dry_run defaults from settings
        assert r.dry_run is True and r.candidates == 1 and r.reconciled == 0
        assert prov.writes == 0
        row = await _row(Session, uid)
        assert row.reconciliation_status is None and row.reconciliation_attempts == 0
        assert row.status == "ambiguous"                # executor status untouched
        await eng.dispose()
    _run(go())


def test_real_run_intent_observed_writes_only_recon(monkeypatch):
    async def go():
        eng, Session, prov = await _install(monkeypatch, enabled=True, dry_run=False, price=100)
        uid = await _seed(Session)                       # target price 100, provider returns 100
        r = await rs.run_recovery_sweep(dry_run=False)
        assert r.candidates == 1 and r.intent_observed == 1 and r.reconciled == 1
        assert prov.reads == 1 and prov.writes == 0      # provider WRITE never called
        row = await _row(Session, uid)
        assert row.reconciliation_status == "intent_observed"
        assert row.reconciliation_attempts == 1 and row.last_reconciled_at is not None
        assert row.status == "ambiguous"                 # status unchanged
        assert row.claim_generation == 0                 # fencing token unchanged
        assert row.dispatch_started_at is None
        await eng.dispose()
    _run(go())


def test_not_observed_when_price_differs(monkeypatch):
    async def go():
        eng, Session, prov = await _install(monkeypatch, enabled=True, dry_run=False, price=555)
        uid = await _seed(Session)                       # target 100, provider returns 555
        r = await rs.run_recovery_sweep(dry_run=False)
        assert r.target_not_observed == 1 and prov.writes == 0
        row = await _row(Session, uid)
        assert row.reconciliation_status == "target_not_observed" and row.next_reconcile_at is not None
        await eng.dispose()
    _run(go())


def test_unsupported_action_never_calls_provider(monkeypatch):
    async def go():
        eng, Session, prov = await _install(monkeypatch, enabled=True, dry_run=False, price=100)
        uid = await _seed(Session, action="ad_set_bid",
                          payload={"marketplace": "wildberries", "campaign_id": 7, "cpm": 50})
        r = await rs.run_recovery_sweep(dry_run=False)
        assert r.still_unknown == 1 and prov.reads == 0 and prov.writes == 0
        row = await _row(Session, uid)
        assert row.reconciliation_status == "still_unknown"
        await eng.dispose()
    _run(go())


def test_fingerprint_mismatch_is_fail_closed(monkeypatch):
    async def go():
        eng, Session, prov = await _install(monkeypatch, enabled=True, dry_run=False, price=100)
        uid = await _seed(Session, bad_fp=True)          # stored fingerprint does not match the payload
        r = await rs.run_recovery_sweep(dry_run=False)
        assert r.fingerprint_mismatches == 1 and r.still_unknown == 1
        assert prov.reads == 0 and prov.writes == 0      # no provider read on a mismatch
        row = await _row(Session, uid)
        assert row.reconciliation_status == "still_unknown"
        await eng.dispose()
    _run(go())


def test_terminal_rows_are_not_candidates(monkeypatch):
    async def go():
        eng, Session, prov = await _install(monkeypatch, enabled=True, dry_run=False, price=100)
        for st in ("success", "failed", "rejected", "reverted"):
            await _seed(Session, status=st)
        r = await rs.run_recovery_sweep(dry_run=False)
        assert r.candidates == 0 and prov.reads == 0
        await eng.dispose()
    _run(go())


def test_fresh_pending_not_yet_a_candidate(monkeypatch):
    async def go():
        eng, Session, prov = await _install(monkeypatch, enabled=True, dry_run=False, price=100)
        await _seed(Session, status="pending", age_s=1)   # too young → below the stale threshold
        r = await rs.run_recovery_sweep(dry_run=False)
        assert r.candidates == 0
        await eng.dispose()
    _run(go())
