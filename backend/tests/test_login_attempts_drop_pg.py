"""SECURITY-2C-3C — real-PostgreSQL proof that the drop migration removes login_attempts (and its PII)
while leaving the throttle and user tables intact, and that downgrade restores only an EMPTY structure.

Runs the REAL Alembic migration on PostgreSQL 16 in the postgres-explain CI job (skipped locally). No
real email/IP is used — the seeded rows are fictional (…@example.invalid / documentation IP ranges).
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


def _tables(c):
    return {r[0] for r in c.exec_driver_sql(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'")}


def _cols(c, table):
    return {r[0] for r in c.exec_driver_sql(
        f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'")}


def test_pg_drop_login_attempts_removes_table_and_pii(monkeypatch):
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

        # ── migrate up to the PRE-drop head: login_attempts still present ──
        command.upgrade(cfg, "mts1a2b3c4d01")
        with eng.begin() as c:
            t = _tables(c)
            assert "login_attempts" in t
            assert "auth_rate_limit_buckets" in t and "users" in t
            arb_cols_before = _cols(c, "auth_rate_limit_buckets")
            # seed FICTIONAL PII rows (documentation domains / IP ranges — never real)
            c.exec_driver_sql(
                "INSERT INTO login_attempts(id,email,ip_address,success,action,reason,created_at) VALUES "
                "('id1','fake1@example.invalid','203.0.113.1',true ,'login', NULL,now()),"
                "('id2','fake2@example.invalid','198.51.100.2',false,'login','x' ,now())")
            seeded = c.exec_driver_sql("SELECT count(*) FROM login_attempts").scalar()
        assert seeded == 2

        # ── the drop migration ──
        command.upgrade(cfg, "lad1a2b3c4d01")
        with eng.begin() as c:
            t = _tables(c)
            assert "login_attempts" not in t                       # table + its 2 PII rows gone
            assert "users" in t and "auth_rate_limit_buckets" in t  # untouched
            assert _cols(c, "auth_rate_limit_buckets") == arb_cols_before

        # ── downgrade restores an EMPTY structure with the original columns + indexes ──
        command.downgrade(cfg, "mts1a2b3c4d01")
        with eng.begin() as c:
            assert "login_attempts" in _tables(c)
            assert c.exec_driver_sql("SELECT count(*) FROM login_attempts").scalar() == 0   # NO data restored
            assert _cols(c, "login_attempts") == {
                "id", "email", "ip_address", "success", "action", "reason", "created_at"}
            idx = {r[0] for r in c.exec_driver_sql(
                "SELECT indexname FROM pg_indexes WHERE tablename='login_attempts'")}
            assert "ix_login_attempts_email" in idx and "ix_login_attempts_created_at" in idx

        # ── re-upgrade drops it again ──
        command.upgrade(cfg, "lad1a2b3c4d01")
        with eng.begin() as c:
            assert "login_attempts" not in _tables(c)
    finally:
        eng.dispose()
