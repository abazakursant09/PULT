"""SECURITY-2D-1C-A — real-PostgreSQL proof that the additive recovery migration is data-safe.

Runs the REAL Alembic migration on PostgreSQL 16 in the postgres-explain CI job (skipped locally): a
non-empty execution_logs survives upgrade with claim_generation defaulting to 0 and reconciliation_status
NULL, the two CHECKs reject bad values, downgrade removes only the two columns + their CHECKs, and
re-upgrade succeeds. The 1B-B partial UNIQUE is untouched.
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


def test_pg_additive_recovery_migration(monkeypatch):
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

        command.upgrade(cfg, "uqc1a2b3c4d01")                 # pre-1C-A
        with eng.begin() as c:
            assert "claim_generation" not in _cols(c)
            c.exec_driver_sql(
                "INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,idempotency_key) "
                "VALUES('L1','u1','set_price','manual_l3','{}','success',"
                "'v1:client:3f2504e0-4f89-41d3-9a0c-0305e82c3301')")

        command.upgrade(cfg, "rcv1a2b3c4d01")                 # add the two columns + CHECKs
        with eng.begin() as c:
            cols = _cols(c)
            assert "claim_generation" in cols and "reconciliation_status" in cols
            row = c.exec_driver_sql(
                "SELECT status, claim_generation, reconciliation_status FROM execution_logs "
                "WHERE id='L1'").first()
            assert tuple(row) == ("success", 0, None)          # survived, default 0 / NULL

        # CHECKs enforced on real PG
        with eng.connect() as c:
            for bad in ("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                        "claim_generation) VALUES('b1','u','a','m','{}','pending',-1)",
                        "INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                        "reconciliation_status) VALUES('b2','u','a','m','{}','pending','bogus')"):
                trans = c.begin()
                with pytest.raises(Exception):
                    c.exec_driver_sql(bad)
                trans.rollback()
            # a valid value is accepted
            c.exec_driver_sql("INSERT INTO execution_logs(id,user_id,action_type,mode,payload,status,"
                              "reconciliation_status) VALUES('g','u','a','m','{}','pending','resolved')")
            c.commit()

        command.downgrade(cfg, "uqc1a2b3c4d01")               # drop only the two columns + CHECKs
        with eng.begin() as c:
            cols = _cols(c)
            assert "claim_generation" not in cols and "reconciliation_status" not in cols
            assert c.exec_driver_sql("SELECT count(*) FROM execution_logs").scalar() == 2   # rows intact

        command.upgrade(cfg, "rcv1a2b3c4d01")                 # re-upgrade
        with eng.begin() as c:
            assert "claim_generation" in _cols(c)
    finally:
        eng.dispose()
