"""
Alembic roundtrip for the Operations Signal Foundation migration (ops1a2b3c4d01).

Proves the migration is reversible and non-destructive against the rest of the
chain: upgrade to the operations revision creates `operations_signal` with the
expected indexes; downgrade back to the prior head (tm1c1a2b3c4d02) drops it
cleanly; a final `upgrade head` restores it; and the history still has exactly one
head. Mirrors the lightweight `command.upgrade` + sqlalchemy-inspect style of
test_startup_migration.py (no subprocess, no heavy harness).
"""
import os
import tempfile

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext

REV = "ops1a2b3c4d01"          # Operations Signal Foundation (Slice 1)
PRIOR = "tm1c1a2b3c4d02"       # the head this revision builds on
TABLE = "operations_signal"
EXPECTED_INDEXES = {
    "ix_operations_signal_user_listing",
    "ix_operations_signal_insight",
    "ix_operations_signal_status",
}


def _current(sync_url):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return MigrationContext.configure(c).get_current_revision()
    finally:
        eng.dispose()


def _tables(sync_url):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return set(sa.inspect(c).get_table_names())
    finally:
        eng.dispose()


def _indexes(sync_url, table):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return {ix["name"] for ix in sa.inspect(c).get_indexes(table)}
    finally:
        eng.dispose()


def test_operations_signal_migration_roundtrip(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "ops_migration_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"

    import db_migrations as dbm
    cfg = dbm._alembic_config()

    # (5) history has exactly one head, and it is the operations revision
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert heads == [REV], f"expected single head {REV}, got {heads}"

    # (1) upgrade to ops1a2b3c4d01 creates the table
    command.upgrade(cfg, REV)
    assert _current(sync_url) == REV
    assert TABLE in _tables(sync_url)

    # (2) expected indexes present
    assert EXPECTED_INDEXES <= _indexes(sync_url, TABLE), \
        f"missing indexes: {EXPECTED_INDEXES - _indexes(sync_url, TABLE)}"

    # (3) downgrade to the prior head drops the table cleanly
    command.downgrade(cfg, PRIOR)
    assert _current(sync_url) == PRIOR
    assert TABLE not in _tables(sync_url)

    # (4) upgrade head again restores it (idempotent, re-runnable)
    command.upgrade(cfg, "head")
    assert _current(sync_url) == REV
    assert TABLE in _tables(sync_url)
