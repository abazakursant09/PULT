"""
Root-fix test (schema drift): a DB pinned at an older revision must be brought
to head automatically by the startup migration runner — reproducing and proving
the review_responses drift can no longer cause a runtime 500.
"""
import os
import tempfile

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext

PRIOR = "b1f3c0de5a01"   # one revision before ME-1 (no review_responses ME columns)


def _cols(sync_url, table):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return [col["name"] for col in sa.inspect(c).get_columns(table)]
    finally:
        eng.dispose()


def _current(sync_url):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return MigrationContext.configure(c).get_current_revision()
    finally:
        eng.dispose()


def test_startup_upgrades_old_db_to_head(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "drift_test.db")
    async_url = f"sqlite+aiosqlite:///{tmp}"
    sync_url = f"sqlite:///{tmp}"
    # both env.py (async upgrade) and our inspector read this
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", async_url)

    import db_migrations as dbm
    cfg = dbm._alembic_config()

    # 1) build DB at the OLD revision (pre-ME-1)
    command.upgrade(cfg, PRIOR)
    assert _current(sync_url) == PRIOR
    assert "external_review_id" not in _cols(sync_url, "review_responses"), \
        "precondition: old DB must lack the ME-1 column"

    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert _current(sync_url) != head, "old DB must be behind head"

    # 2) startup runner brings it to head automatically (no manual ALTER)
    dbm._migrate_sync()

    # 3) schema now matches head — the column exists, no drift, no 500 possible
    assert _current(sync_url) == head
    assert "external_review_id" in _cols(sync_url, "review_responses")
    assert "execution_log_id" in _cols(sync_url, "review_responses")


def test_fresh_empty_db_built_from_scratch(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "fresh_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"

    import db_migrations as dbm
    # empty file DB -> runner applies all migrations from scratch
    open(tmp, "a").close()
    dbm._migrate_sync()

    head = ScriptDirectory.from_config(dbm._alembic_config()).get_current_head()
    assert _current(sync_url) == head
    # ME tables + ME-1 columns present
    assert "execution_logs" in sa.inspect(sa.create_engine(sync_url)).get_table_names()
    assert "external_review_id" in _cols(sync_url, "review_responses")
