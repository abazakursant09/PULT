"""SECURITY-2D-1C-C2 — re-own migration + sweep behaviour on SQLite (flag gate, dry-run, eligibility).

True cross-connection concurrency + advisory-lock is proven on real PostgreSQL (test_reown_sweep_pg.py).
Here: the additive migration is data-safe (up/down/re-up, partial UNIQUE + C1/recovery CHECKs preserved);
the sweep does NOTHING when OFF, counts-but-mutates-nothing in dry-run, and a real run transfers ownership
of ONLY a stuck safe pending claim (generation+1 / reown_count+1 / last_reowned_at) while leaving status /
dispatch_started_at / attempt_count / reconciliation untouched. No provider, no executor.
"""
import asyncio
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.execution_log import ExecutionLog
from services.marketplace import operation_key
from services.marketplace.recovery import reown_sweep as rw

_FCS = "fcs1a2b3c4d01"
_V1 = "v1:review:3f2504e0-4f89-41d3-9a0c-0305e82c3301"
_FP = "fp1:" + "a" * 64


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ───────────────────────── migration ─────────────────────────
def _cfg(monkeypatch, tmp_path):
    import db_migrations as dbm
    dbfile = tmp_path / "rwn.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    return dbm._alembic_config(), dbfile


def _cols(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1]: (r[2], r[3]) for r in c.execute("PRAGMA table_info(execution_logs)")}
    finally:
        c.close()


def _indexes(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1] for r in c.execute("PRAGMA index_list(execution_logs)")}
    finally:
        c.close()


def test_additive_migration_roundtrip_nonempty(monkeypatch, tmp_path):
    cfg, dbfile = _cfg(monkeypatch, tmp_path)
    command.upgrade(cfg, _FCS)
    c = sqlite3.connect(str(dbfile))
    try:
        c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,idempotency_key,"
                  "attempt_count,claim_generation) VALUES('L1','u','set_price','manual_l3','{}','in_flight',"
                  "?,3,2)", [_V1])
        c.commit()
    finally:
        c.close()
    assert "reown_count" not in _cols(dbfile)

    command.upgrade(cfg, "rwn1a2b3c4d01")
    cols = _cols(dbfile)
    assert cols["reown_count"][1] == 1                # NOT NULL
    assert cols["last_reowned_at"][1] == 0             # nullable
    c = sqlite3.connect(str(dbfile))
    try:
        row = c.execute("SELECT reown_count, last_reowned_at, attempt_count, claim_generation "
                        "FROM execution_logs WHERE id='L1'").fetchone()
    finally:
        c.close()
    assert row == (0, None, 3, 2)                      # backfilled; C1 fields intact
    assert "uq_execlog_op_claim" in _indexes(dbfile)
    with pytest.raises(sqlite3.IntegrityError):        # partial UNIQUE preserved across batch
        cc = sqlite3.connect(str(dbfile))
        try:
            cc.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                       "idempotency_key) VALUES('L2','u','set_price','manual_l3','{}','pending',?)", [_V1])
            cc.commit()
        finally:
            cc.close()

    command.downgrade(cfg, _FCS)
    assert "reown_count" not in _cols(dbfile) and "last_reowned_at" not in _cols(dbfile)
    c = sqlite3.connect(str(dbfile))
    try:
        assert c.execute("SELECT count(*) FROM execution_logs").fetchone()[0] == 1
    finally:
        c.close()
    command.upgrade(cfg, "rwn1a2b3c4d01")
    assert "reown_count" in _cols(dbfile)


def test_check_rejects_negative_reown_count(monkeypatch, tmp_path):
    cfg, dbfile = _cfg(monkeypatch, tmp_path)
    command.upgrade(cfg, "rwn1a2b3c4d01")
    c = sqlite3.connect(str(dbfile))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,reown_count) "
                      "VALUES('b','u','a','m','{}','pending',-1)")
    finally:
        c.close()


def test_model_parity_and_single_head(tmp_path):
    import sqlalchemy as sa
    from sqlalchemy import inspect as _ins
    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    Base.metadata.create_all(eng)
    cols = {c["name"] for c in _ins(eng).get_columns("execution_logs")}
    assert {"reown_count", "last_reowned_at"} <= cols
    eng.dispose()
    t = ExecutionLog.__table__
    assert t.columns["reown_count"].nullable is False and t.columns["last_reowned_at"].nullable is True
    ck = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "ck_execlog_reown_count_nonneg" in ck
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rwn1a2b3c4d01"]


# ───────────────────────── sweep behaviour ─────────────────────────
async def _install(monkeypatch, *, enabled=True, dry_run=False):
    from config import settings
    monkeypatch.setattr(settings, "recovery_reown_enabled", enabled)
    monkeypatch.setattr(settings, "recovery_reown_dry_run", dry_run)
    eng = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False},
                              poolclass=StaticPool)
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    Session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(rw, "engine", eng)
    monkeypatch.setattr(rw, "AsyncSessionLocal", Session)
    return eng, Session


async def _seed(Session, *, status="pending", dsa=False, key=_V1, fp=_FP, gen=0, reown=0,
                attempt=0, age_s=3600, uid="u1"):
    async with Session() as db:
        db.add(ExecutionLog(
            id=str(uuid.uuid4()), user_id=uid, action_type="publish_review_response", mode="manual_l3",
            payload={}, status=status, idempotency_key=key, request_fingerprint=fp,
            claim_generation=gen, reown_count=reown, attempt_count=attempt,
            created_at=datetime.utcnow() - timedelta(seconds=age_s),
            dispatch_started_at=datetime.now(timezone.utc) if dsa else None))
        await db.commit()


async def _only(Session):
    async with Session() as db:
        return (await db.execute(select(ExecutionLog))).scalars().first()


def test_flag_off_zero_everything(monkeypatch):
    async def go():
        eng, S = await _install(monkeypatch, enabled=False)
        await _seed(S)
        r = await rw.run_reown_sweep()
        assert r.enabled is False and r.candidates == 0 and r.reowned == 0 and r.lock_acquired is False
        row = await _only(S)
        assert row.claim_generation == 0 and row.reown_count == 0 and row.last_reowned_at is None
        await eng.dispose()
    _run(go())


def test_dry_run_counts_no_mutation(monkeypatch):
    async def go():
        eng, S = await _install(monkeypatch, enabled=True, dry_run=True)
        await _seed(S)
        r = await rw.run_reown_sweep()
        assert r.enabled and r.dry_run and r.candidates == 1 and r.eligible == 1 and r.reowned == 0
        row = await _only(S)
        assert row.claim_generation == 0 and row.reown_count == 0 and row.last_reowned_at is None
        await eng.dispose()
    _run(go())


def test_stale_pending_reowned(monkeypatch):
    async def go():
        eng, S = await _install(monkeypatch, enabled=True, dry_run=False)
        await _seed(S, gen=0, reown=0, attempt=2)
        r = await rw.run_reown_sweep()
        assert r.reowned == 1 and r.skipped_race == 0 and r.skipped_invalid == 0
        row = await _only(S)
        assert row.claim_generation == 1 and row.reown_count == 1 and row.last_reowned_at is not None
        assert row.status == "pending" and row.dispatch_started_at is None    # stays a pending claim
        assert row.attempt_count == 2                                          # dispatch counter untouched
        assert row.reconciliation_status is None and row.reconciliation_attempts == 0
        await eng.dispose()
    _run(go())


def test_fresh_pending_not_reowned(monkeypatch):
    async def go():
        eng, S = await _install(monkeypatch, enabled=True, dry_run=False)
        await _seed(S, age_s=10)          # younger than the 900s stale cutoff
        r = await rw.run_reown_sweep()
        assert r.candidates == 0 and r.reowned == 0
        assert (await _only(S)).claim_generation == 0
        await eng.dispose()
    _run(go())


@pytest.mark.parametrize("status,dsa", [("pending", True), ("in_flight", True), ("ambiguous", True),
                                        ("failed", True), ("success", True), ("reverted", True),
                                        ("rejected", False)])
def test_non_safe_pending_never_reowned(monkeypatch, status, dsa):
    async def go():
        eng, S = await _install(monkeypatch, enabled=True, dry_run=False)
        await _seed(S, status=status, dsa=dsa)
        r = await rw.run_reown_sweep()
        assert r.reowned == 0
        assert (await _only(S)).claim_generation == 0
        await eng.dispose()
    _run(go())


def test_max_reowns_boundary(monkeypatch):
    async def go():
        eng, S = await _install(monkeypatch, enabled=True, dry_run=False)
        await _seed(S, reown=5)           # == recovery_max_reowns (default 5) → not eligible
        r = await rw.run_reown_sweep()
        assert r.candidates == 0 and r.reowned == 0
        assert (await _only(S)).reown_count == 5
        await eng.dispose()
    _run(go())


@pytest.mark.parametrize("key,fp", [("price:p:1", _FP), (None, _FP), ("v1:review:not-a-uuid", _FP),
                                    (_V1, None), (_V1, "fp1:" + "A" * 64), (_V1, "fp1:short")])
def test_invalid_key_or_fingerprint_skipped(monkeypatch, key, fp):
    async def go():
        eng, S = await _install(monkeypatch, enabled=True, dry_run=False)
        await _seed(S, key=key, fp=fp)
        r = await rw.run_reown_sweep()
        assert r.reowned == 0                         # canonical validation blocks it
        assert (await _only(S)).claim_generation == 0
        await eng.dispose()
    _run(go())
