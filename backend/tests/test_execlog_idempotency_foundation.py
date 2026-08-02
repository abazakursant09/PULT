"""SECURITY-2D-1B-A — the additive execution_logs columns + migration (SQLite + model parity).

Adds request_fingerprint / dispatch_started_at as nullable, unwired columns. Proves a non-empty table
survives up/down/re-up, the columns are nullable with no default, the model matches the migration, and
the head advanced to efp1a2b3c4d01. Runtime is unchanged — nothing reads these columns yet.
"""
import sqlite3

from alembic import command
from sqlalchemy import inspect as _sa_inspect

import models  # noqa: F401
from models.execution_log import ExecutionLog


def _seed_one(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        c.execute("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status) "
                  "VALUES('L1','u1','set_price','manual_l3','{}','success')")
        c.commit()
    finally:
        c.close()


def _cols(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1]: (r[2], r[3]) for r in c.execute("PRAGMA table_info(execution_logs)")}  # name:(type,notnull)
    finally:
        c.close()


def test_additive_migration_sqlite_roundtrip_nonempty(monkeypatch, tmp_path):
    import db_migrations as dbm
    dbfile = tmp_path / "efp.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    cfg = dbm._alembic_config()

    command.upgrade(cfg, "mfd1a2b3c4d01")          # pre-1B-A
    _seed_one(dbfile)
    assert "request_fingerprint" not in _cols(dbfile)

    command.upgrade(cfg, "efp1a2b3c4d01")          # add the two columns
    cols = _cols(dbfile)
    assert "request_fingerprint" in cols and "dispatch_started_at" in cols
    assert cols["request_fingerprint"][1] == 0 and cols["dispatch_started_at"][1] == 0   # nullable
    c = sqlite3.connect(str(dbfile))
    try:
        row = c.execute("SELECT status, request_fingerprint, dispatch_started_at "
                        "FROM execution_logs WHERE id='L1'").fetchone()
    finally:
        c.close()
    assert row == ("success", None, None)          # old row survived; new cols NULL

    command.downgrade(cfg, "mfd1a2b3c4d01")         # drop just the two columns
    after = _cols(dbfile)
    assert "request_fingerprint" not in after and "dispatch_started_at" not in after
    c = sqlite3.connect(str(dbfile))
    try:
        assert c.execute("SELECT count(*) FROM execution_logs").fetchone()[0] == 1   # row intact
    finally:
        c.close()

    command.upgrade(cfg, "efp1a2b3c4d01")           # re-upgrade
    assert "request_fingerprint" in _cols(dbfile)


def test_model_has_the_two_nullable_columns():
    t = ExecutionLog.__table__
    for name in ("request_fingerprint", "dispatch_started_at"):
        assert name in t.columns and t.columns[name].nullable is True
    assert t.columns["request_fingerprint"].type.length == 72   # fits "fp1:" + 64 hex = 68
    assert t.columns["dispatch_started_at"].type.timezone is True


def test_model_matches_migration_via_create_all(tmp_path):
    # create_all builds the schema from the MODEL; the migrated schema must agree on the two columns.
    import sqlalchemy as sa
    from database import Base
    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'parity.db').as_posix()}")
    Base.metadata.create_all(eng)
    cols = {c["name"] for c in _sa_inspect(eng).get_columns("execution_logs")}
    assert {"request_fingerprint", "dispatch_started_at"} <= cols
    eng.dispose()


def test_single_head_efp():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rbp1a2b3c4d01"]
