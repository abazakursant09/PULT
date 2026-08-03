"""SECURITY-2D-1C-B — additive reconciliation-scheduling columns + migration (SQLite + parity)."""
import sqlite3

from alembic import command
from sqlalchemy import inspect as _sa_inspect

import models  # noqa: F401
from models.execution_log import ExecutionLog

_V1 = "v1:client:3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _cols(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1]: (r[2], r[3]) for r in c.execute("PRAGMA table_info(execution_logs)")}
    finally:
        c.close()


def _idx(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1] for r in c.execute("PRAGMA index_list(execution_logs)")}
    finally:
        c.close()


def test_additive_roundtrip_and_unique_preserved(monkeypatch, tmp_path):
    import db_migrations as dbm
    dbfile = tmp_path / "rcb.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    cfg = dbm._alembic_config()

    command.upgrade(cfg, "rcv1a2b3c4d01")
    c = sqlite3.connect(str(dbfile))
    c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,idempotency_key) "
              "VALUES('L1','u1','set_price','manual_l3','{}','pending',?)", [_V1])
    c.commit()
    c.close()

    command.upgrade(cfg, "rcb1a2b3c4d01")
    cols = _cols(dbfile)
    assert cols["reconciliation_attempts"][1] == 1                      # NOT NULL
    assert cols["last_reconciled_at"][1] == 0 and cols["next_reconcile_at"][1] == 0   # nullable
    c = sqlite3.connect(str(dbfile))
    row = c.execute("SELECT status, reconciliation_attempts, last_reconciled_at, next_reconcile_at "
                    "FROM execution_logs WHERE id='L1'").fetchone()
    assert row == ("pending", 0, None, None)
    # 1B-B partial UNIQUE survived the batch recreate
    assert "uq_execlog_op_claim" in _idx(dbfile)
    try:
        c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,idempotency_key) "
                  "VALUES('L2','u1','set_price','manual_l3','{}','pending',?)", [_V1])
        c.commit()
        raise AssertionError("partial UNIQUE lost")
    except sqlite3.IntegrityError:
        pass
    # CHECK rejects a negative attempt count
    try:
        c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                  "reconciliation_attempts) VALUES('B','u','a','m','{}','pending',-1)")
        c.commit()
        raise AssertionError("CHECK not enforced")
    except sqlite3.IntegrityError:
        pass
    c.close()

    command.downgrade(cfg, "rcv1a2b3c4d01")
    after = _cols(dbfile)
    assert not ({"reconciliation_attempts", "last_reconciled_at", "next_reconcile_at"} & set(after))
    assert "reconciliation_status" in after                            # 1C-A column preserved
    assert "uq_execlog_op_claim" in _idx(dbfile)
    c = sqlite3.connect(str(dbfile))
    assert c.execute("SELECT count(*) FROM execution_logs").fetchone()[0] == 1
    c.close()
    command.upgrade(cfg, "rcb1a2b3c4d01")
    assert "reconciliation_attempts" in _cols(dbfile)


def test_model_matches_migration_and_check():
    t = ExecutionLog.__table__
    assert t.columns["reconciliation_attempts"].nullable is False
    assert t.columns["last_reconciled_at"].type.timezone is True
    assert t.columns["next_reconcile_at"].type.timezone is True
    ck = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "ck_execlog_reconciliation_attempts_nonneg" in ck


def test_create_all_parity(tmp_path):
    import sqlalchemy as sa
    from database import Base
    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    Base.metadata.create_all(eng)
    cols = {c["name"] for c in _sa_inspect(eng).get_columns("execution_logs")}
    assert {"reconciliation_attempts", "last_reconciled_at", "next_reconcile_at"} <= cols
    eng.dispose()


def test_single_head_rcb():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rwn1a2b3c4d01"]
