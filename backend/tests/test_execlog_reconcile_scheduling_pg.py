"""SECURITY-2D-1C-B — real-PostgreSQL proof the reconciliation-scheduling migration is data-safe."""
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


def test_pg_additive_reconcile_scheduling(monkeypatch):
    sync_url = _pg_sync_url()
    if not sync_url or not sync_url.startswith("postgres"):
        pytest.skip("BLOCKED_ENVIRONMENT: no PostgreSQL; runs in the postgres-explain CI job.")
    import sqlalchemy as sa
    from alembic import command
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _pg_alembic_url())
    import db_migrations as dbm
    cfg = dbm._alembic_config()

    eng = sa.create_engine(sync_url)
    try:
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        command.upgrade(cfg, "rcv1a2b3c4d01")
        with eng.begin() as c:
            assert "reconciliation_attempts" not in _cols(c)
            c.exec_driver_sql(
                "INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,idempotency_key) "
                "VALUES('L1','u1','set_price','manual_l3','{}','ambiguous',"
                "'v1:client:3f2504e0-4f89-41d3-9a0c-0305e82c3301')")

        command.upgrade(cfg, "rcb1a2b3c4d01")
        with eng.begin() as c:
            cols = _cols(c)
            assert {"reconciliation_attempts", "last_reconciled_at", "next_reconcile_at"} <= cols
            row = c.exec_driver_sql("SELECT status, reconciliation_attempts, last_reconciled_at, "
                                    "next_reconcile_at FROM execution_logs WHERE id='L1'").first()
            assert tuple(row) == ("ambiguous", 0, None, None)

        with eng.connect() as c:
            trans = c.begin()
            with pytest.raises(Exception):
                c.exec_driver_sql("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                                  "reconciliation_attempts) VALUES('b','u','a','m','{}','pending',-1)")
            trans.rollback()

        command.downgrade(cfg, "rcv1a2b3c4d01")
        with eng.begin() as c:
            cols = _cols(c)
            assert not ({"reconciliation_attempts", "last_reconciled_at", "next_reconcile_at"} & cols)
            assert "reconciliation_status" in cols                      # 1C-A column preserved
            assert c.exec_driver_sql("SELECT count(*) FROM execution_logs").scalar() == 1

        command.upgrade(cfg, "rcb1a2b3c4d01")
        with eng.begin() as c:
            assert "reconciliation_attempts" in _cols(c)
    finally:
        eng.dispose()
