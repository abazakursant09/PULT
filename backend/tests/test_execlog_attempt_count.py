"""SECURITY-2D-1C-C1 — attempt_count / last_attempt_at additive migration + fencing-CAS predicate (SQLite).

SQLite is NOT proof of concurrency (that is test_execlog_fencing_cas_pg.py on real PostgreSQL). Here we
prove the additive migration is data-safe (up/down/re-up on a non-empty table, partial UNIQUE + all
1C-A/1C-B CHECKs preserved), that the fencing CAS SQL enforces its ownership predicate, and model↔migration
parity.
"""
import sqlite3

from alembic import command
import pytest

import models  # noqa: F401
from models.execution_log import ExecutionLog

_RBP = "rbp1a2b3c4d01"
_V1 = "v1:review:3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _cfg(monkeypatch, tmp_path):
    import db_migrations as dbm
    dbfile = tmp_path / "fcs.db"
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


def _seed(dbfile, rid, key, status="pending", gen=0):
    c = sqlite3.connect(str(dbfile))
    try:
        c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                  "idempotency_key,claim_generation) VALUES(?,?,?,?,?,?,?,?)",
                  [rid, "u1", "publish_review_response", "manual_l3", "{}", status, key, gen])
        c.commit()
    finally:
        c.close()


def test_additive_migration_roundtrip_nonempty(monkeypatch, tmp_path):
    cfg, dbfile = _cfg(monkeypatch, tmp_path)
    command.upgrade(cfg, _RBP)
    _seed(dbfile, "L1", _V1, status="in_flight")
    assert "attempt_count" not in _cols(dbfile)

    command.upgrade(cfg, "fcs1a2b3c4d01")
    cols = _cols(dbfile)
    assert cols["attempt_count"][1] == 1           # NOT NULL
    assert cols["last_attempt_at"][1] == 0          # nullable
    c = sqlite3.connect(str(dbfile))
    try:
        row = c.execute("SELECT status, attempt_count, last_attempt_at FROM execution_logs "
                        "WHERE id='L1'").fetchone()
    finally:
        c.close()
    assert row == ("in_flight", 0, None)            # backfilled

    # 1B-B partial UNIQUE preserved across the batch recreate
    assert "uq_execlog_op_claim" in _indexes(dbfile)
    with pytest.raises(sqlite3.IntegrityError):
        _seed(dbfile, "L2", _V1, status="pending")

    command.downgrade(cfg, _RBP)
    after = _cols(dbfile)
    assert "attempt_count" not in after and "last_attempt_at" not in after
    c = sqlite3.connect(str(dbfile))
    try:
        assert c.execute("SELECT count(*) FROM execution_logs").fetchone()[0] == 1   # rows kept
    finally:
        c.close()

    command.upgrade(cfg, "fcs1a2b3c4d01")
    assert "attempt_count" in _cols(dbfile)


def test_check_rejects_negative_attempt_count(monkeypatch, tmp_path):
    cfg, dbfile = _cfg(monkeypatch, tmp_path)
    command.upgrade(cfg, "fcs1a2b3c4d01")
    c = sqlite3.connect(str(dbfile))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,attempt_count) "
                      "VALUES('bad','u','a','m','{}','pending',-1)")
    finally:
        c.close()


def test_fencing_cas_predicate_sqlite(monkeypatch, tmp_path):
    """The fencing CAS SQL owns a pending+dsa-NULL row only for the matching generation."""
    cfg, dbfile = _cfg(monkeypatch, tmp_path)
    command.upgrade(cfg, "fcs1a2b3c4d01")
    _seed(dbfile, "L1", _V1, status="pending", gen=0)
    c = sqlite3.connect(str(dbfile))
    try:
        cas = ("UPDATE execution_logs SET status='in_flight', dispatch_started_at='2026-01-01 00:00:00', "
               "attempt_count=attempt_count+1, last_attempt_at='2026-01-01 00:00:00' "
               "WHERE id=? AND status='pending' AND dispatch_started_at IS NULL AND claim_generation=? "
               "RETURNING id, attempt_count")
        # stale generation → no row
        assert c.execute(cas, ["L1", 1]).fetchall() == []
        assert c.execute("SELECT status, claim_generation FROM execution_logs WHERE id='L1'").fetchone() \
            == ("pending", 0)
        # matching generation → owns, attempt_count 0→1
        won = c.execute(cas, ["L1", 0]).fetchall()
        assert won and won[0][1] == 1
        c.commit()
        # already in_flight → second CAS empty
        assert c.execute(cas, ["L1", 0]).fetchall() == []
    finally:
        c.close()


def test_model_parity_and_single_head(tmp_path):
    import sqlalchemy as sa
    from database import Base
    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'parity.db').as_posix()}")
    Base.metadata.create_all(eng)
    from sqlalchemy import inspect as _sa_inspect
    cols = {c["name"] for c in _sa_inspect(eng).get_columns("execution_logs")}
    assert {"attempt_count", "last_attempt_at"} <= cols
    eng.dispose()
    t = ExecutionLog.__table__
    assert t.columns["attempt_count"].nullable is False
    assert t.columns["last_attempt_at"].nullable is True
    ck = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "ck_execlog_attempt_count_nonneg" in ck

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rop1a2b3c4d01"]
