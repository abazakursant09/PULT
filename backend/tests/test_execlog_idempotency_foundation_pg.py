"""SECURITY-2D-1B-A — real-PostgreSQL proof that the additive execution_logs migration is data-safe.

Runs the REAL Alembic migration on PostgreSQL 16 in the postgres-explain CI job (skipped locally): a
non-empty execution_logs survives upgrade with the two new columns NULL, downgrade removes only those
columns and keeps the row, and re-upgrade succeeds. No UNIQUE / CHECK / status change.
"""
import os

import pytest


def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_alembic_url():
    explicit = os.environ.get("PULT_TEST_PG_ALEMBIC_URL")
    if explicit:
        return explicit
    sync = _pg_sync_url() or ""
    return sync.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


def _cols(c):
    return {r[0] for r in c.exec_driver_sql(
        "SELECT column_name FROM information_schema.columns WHERE table_name='execution_logs'")}


def test_pg_additive_migration_preserves_rows(monkeypatch):
    sync_url = _pg_sync_url()
    if not sync_url or not sync_url.startswith("postgres"):
        pytest.skip("BLOCKED_ENVIRONMENT: no PostgreSQL (PULT_TEST_PG_URL unset); runs on real "
                    "PostgreSQL 16 in the postgres-explain CI job.")
    import sqlalchemy as sa
    from alembic import command
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    import db_migrations as dbm
    cfg = dbm._alembic_config()

    eng = sa.create_engine(sync_url)
    try:
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")

        command.upgrade(cfg, "mfd1a2b3c4d01")                 # pre-1B-A
        with eng.begin() as c:
            assert "request_fingerprint" not in _cols(c)
            c.exec_driver_sql(
                "INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status) "
                "VALUES('L1','u1','set_price','manual_l3','{}','success')")

        command.upgrade(cfg, "efp1a2b3c4d01")                 # add the two columns
        with eng.begin() as c:
            cols = _cols(c)
            assert "request_fingerprint" in cols and "dispatch_started_at" in cols
            row = c.exec_driver_sql(
                "SELECT status, request_fingerprint, dispatch_started_at FROM execution_logs "
                "WHERE id='L1'").first()
            assert tuple(row) == ("success", None, None)      # survived, new cols NULL

        command.downgrade(cfg, "mfd1a2b3c4d01")               # drop only the two columns
        with eng.begin() as c:
            cols = _cols(c)
            assert "request_fingerprint" not in cols and "dispatch_started_at" not in cols
            assert c.exec_driver_sql("SELECT count(*) FROM execution_logs").scalar() == 1   # row intact

        command.upgrade(cfg, "efp1a2b3c4d01")                 # re-upgrade
        with eng.begin() as c:
            assert "request_fingerprint" in _cols(c)
    finally:
        eng.dispose()
