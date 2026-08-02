"""SECURITY-2D-1C-A — additive recovery/fencing columns + migration (SQLite + model parity).

Adds claim_generation (fencing token) + reconciliation_status (recovery classification), both UNWIRED.
Proves a non-empty table survives up/down/re-up, the CHECKs enforce their contract, the 1B-B partial
UNIQUE is preserved across the SQLite batch recreate, the model matches the migration, and the head
advanced to rcv1a2b3c4d01. Runtime is unchanged — nothing reads these columns yet.
"""
import sqlite3

from alembic import command
from sqlalchemy import inspect as _sa_inspect

import models  # noqa: F401
from models.execution_log import ExecutionLog, _RECON_STATUSES


_V1 = "v1:client:3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _seed(dbfile, rid, key, status="success", gen=None, recon=None):
    c = sqlite3.connect(str(dbfile))
    try:
        cols = "id,user_id,action_type,mode,payload,status,idempotency_key"
        vals = [rid, "u1", "set_price", "manual_l3", "{}", status, key]
        if gen is not None:
            cols += ",claim_generation"
            vals.append(gen)
        if recon is not None:
            cols += ",reconciliation_status"
            vals.append(recon)
        c.execute(f"INSERT INTO execution_logs({cols}) VALUES({','.join('?' * len(vals))})", vals)
        c.commit()
    finally:
        c.close()


def _cols(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1]: (r[2], r[3]) for r in c.execute("PRAGMA table_info(execution_logs)")}  # name:(type,notnull)
    finally:
        c.close()


def _indexes(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1] for r in c.execute("PRAGMA index_list(execution_logs)")}
    finally:
        c.close()


def test_additive_migration_sqlite_roundtrip_nonempty(monkeypatch, tmp_path):
    import db_migrations as dbm
    dbfile = tmp_path / "rcv.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    cfg = dbm._alembic_config()

    command.upgrade(cfg, "uqc1a2b3c4d01")
    _seed(dbfile, "L1", _V1)                          # a v1 claim row
    _seed(dbfile, "L0", "price:p:100")                # a legacy content-key row
    assert "claim_generation" not in _cols(dbfile)

    command.upgrade(cfg, "rcv1a2b3c4d01")
    cols = _cols(dbfile)
    assert cols["claim_generation"][1] == 1          # NOT NULL
    assert cols["reconciliation_status"][1] == 0      # nullable
    c = sqlite3.connect(str(dbfile))
    try:
        row = c.execute("SELECT status, claim_generation, reconciliation_status "
                        "FROM execution_logs WHERE id='L1'").fetchone()
    finally:
        c.close()
    assert row == ("success", 0, None)                # existing row survived; defaults applied

    command.downgrade(cfg, "uqc1a2b3c4d01")
    after = _cols(dbfile)
    assert "claim_generation" not in after and "reconciliation_status" not in after
    c = sqlite3.connect(str(dbfile))
    try:
        assert c.execute("SELECT count(*) FROM execution_logs").fetchone()[0] == 2   # rows intact
    finally:
        c.close()

    command.upgrade(cfg, "rcv1a2b3c4d01")
    assert "claim_generation" in _cols(dbfile)


def test_partial_unique_1bb_preserved_across_batch(monkeypatch, tmp_path):
    import db_migrations as dbm
    dbfile = tmp_path / "uq.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    cfg = dbm._alembic_config()
    command.upgrade(cfg, "rcv1a2b3c4d01")
    assert "uq_execlog_op_claim" in _indexes(dbfile)
    _seed(dbfile, "A", _V1)
    # same (user, v1 key) must still be rejected (partial UNIQUE survived the batch recreate)
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        _seed(dbfile, "B", _V1, status="pending")
    _seed(dbfile, "C", "price:p:1")                   # legacy keys still allowed to repeat
    _seed(dbfile, "D", "price:p:1")


def test_checks_reject_bad_values(monkeypatch, tmp_path):
    import pytest
    import db_migrations as dbm
    dbfile = tmp_path / "ck.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    command.upgrade(dbm._alembic_config(), "rcv1a2b3c4d01")

    def _ins(rid, extra_col, val):
        c = sqlite3.connect(str(dbfile))
        try:
            c.execute(f"INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,{extra_col}) "
                      f"VALUES(?,?,?,?,?,?,?)", [rid, "u", "a", "m", "{}", "pending", val])
            c.commit()
        finally:
            c.close()

    for extra, val in [("claim_generation", -1), ("reconciliation_status", "bogus"),
                       ("reconciliation_status", "")]:
        with pytest.raises(sqlite3.IntegrityError):
            _ins("bad", extra, val)


def test_checks_accept_all_seven_and_null(monkeypatch, tmp_path):
    import db_migrations as dbm
    dbfile = tmp_path / "ok.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    command.upgrade(dbm._alembic_config(), "rcv1a2b3c4d01")
    assert len(_RECON_STATUSES) == 7
    c = sqlite3.connect(str(dbfile))
    try:
        for i, v in enumerate(_RECON_STATUSES):
            c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                      "reconciliation_status) VALUES(?,?,?,?,?,?,?)",
                      [f"g{i}", "u", "a", "m", "{}", "pending", v])
        c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status) "
                  "VALUES('n','u','a','m','{}','pending')")   # NULL reconciliation_status is allowed
        c.commit()
    finally:
        c.close()


def test_enum_length_fits_column():
    length = ExecutionLog.__table__.columns["reconciliation_status"].type.length
    assert max(len(v) for v in _RECON_STATUSES) <= length     # 16 <= 20


def test_model_has_columns_and_checks():
    t = ExecutionLog.__table__
    assert t.columns["claim_generation"].nullable is False
    assert t.columns["reconciliation_status"].nullable is True
    ck = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "ck_execlog_claim_generation_nonneg" in ck
    assert "ck_execlog_reconciliation_status" in ck


def test_model_matches_migration_via_create_all(tmp_path):
    import sqlalchemy as sa
    from database import Base
    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'parity.db').as_posix()}")
    Base.metadata.create_all(eng)
    cols = {c["name"] for c in _sa_inspect(eng).get_columns("execution_logs")}
    assert {"claim_generation", "reconciliation_status"} <= cols
    eng.dispose()


def test_single_head_rcv():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rcb1a2b3c4d01"]
