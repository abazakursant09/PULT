"""LEGAL-1B — real-PostgreSQL 16 proof that dropping the behavioural stores is data-safe.

Seeded upgrade(lch→rbp) / downgrade(rbp→lch) / re-upgrade on a real PostgreSQL 16 (the engine
production uses). Proves the SQLite guarantees hold on the strict engine: user_events +
operator_decisions are dropped, both seeded users + the objective decision_memory table survive, a
fresh auth insert still works, and downgrade re-adds BOTH tables EMPTY with their exact columns +
indexes (booleans as real booleans — never integer literals) without restoring the deleted history.
No create_all, no session_replication_role, no SQLite fallback; no other table is touched.
"""
import os

import pytest

_LCH = "lch1a2b3c4d01"   # rbp's down_revision: both behavioural tables present here
_RBP = "rbp1a2b3c4d01"   # head: the behavioural stores are gone


def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_alembic_url():
    explicit = os.environ.get("PULT_TEST_PG_ALEMBIC_URL")
    if explicit:
        return explicit
    sync = _pg_sync_url() or ""
    return sync.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


def _table_exists(c, name):
    return c.exec_driver_sql(f"SELECT to_regclass('public.{name}') IS NOT NULL").scalar()


def _cols(c, table):
    return {r[0]: (r[1], r[2]) for r in c.exec_driver_sql(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        f"WHERE table_name='{table}'")}


def _indexes(c, table):
    return {r[0] for r in c.exec_driver_sql(
        f"SELECT indexname FROM pg_indexes WHERE tablename='{table}'")}


def _seed(c):
    for uid, plan in (("u1", "profi"), ("u2", "maximum")):
        c.exec_driver_sql(
            "INSERT INTO users (id,email,name,hashed_password,plan,is_verified) "
            f"VALUES ('{uid}','{uid}@b.com','{uid.upper()}','h','{plan}',TRUE)")
    c.exec_driver_sql("INSERT INTO user_events (id,user_id,event_type,event_scope,created_at) "
                      "VALUES ('e1','u1','insight_opened','action_engine','2026-01-01 00:00:00')")
    c.exec_driver_sql("INSERT INTO user_events (id,user_id,event_type,event_scope,created_at) "
                      "VALUES ('e2','u2','copilot_dismissed','dashboard','2026-01-02 00:00:00')")
    # operator_decisions.accepted/ignored are real BOOLEANs — TRUE/FALSE, never int 0/1 on PG.
    c.exec_driver_sql("INSERT INTO operator_decisions (id,user_id,insight_type,action_taken,accepted,ignored) "
                      "VALUES ('d1','u1','margin_crisis','accepted',TRUE,FALSE)")
    c.exec_driver_sql("INSERT INTO operator_decisions (id,user_id,insight_type,action_taken,accepted,ignored) "
                      "VALUES ('d2','u2','stockout','dismissed_again',FALSE,TRUE)")


def test_pg_legal1b_seeded_roundtrip(monkeypatch):
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

        # ── pre-state at lch, seeded ─────────────────────────────────────────────
        command.upgrade(cfg, _LCH)
        with eng.begin() as c:
            assert _table_exists(c, "user_events") and _table_exists(c, "operator_decisions")
            assert _cols(c, "operator_decisions")["accepted"][0] == "boolean"    # real boolean on PG
            _seed(c)
            assert c.exec_driver_sql("SELECT count(*) FROM user_events").scalar() == 2
            assert c.exec_driver_sql("SELECT count(*) FROM operator_decisions").scalar() == 2

        # ── upgrade lch→rbp: both behavioural tables gone, everything else intact ─
        command.upgrade(cfg, _RBP)
        with eng.begin() as c:
            assert not _table_exists(c, "user_events")
            assert not _table_exists(c, "operator_decisions")
            assert _table_exists(c, "decision_memory")          # objective learning preserved
            rows = {r[0]: r[1] for r in c.exec_driver_sql("SELECT id,email FROM users ORDER BY id")}
            assert rows == {"u1": "u1@b.com", "u2": "u2@b.com"}
            c.exec_driver_sql("INSERT INTO users (id,email,name,hashed_password) "
                              "VALUES ('u3','u3@b.com','U3','h')")
            assert c.exec_driver_sql("SELECT count(*) FROM users").scalar() == 3
            for t in ("workspaces", "execution_logs", "api_credentials", "decision_memory"):
                assert _table_exists(c, t), t

        # ── downgrade rbp→lch: both tables recreated EMPTY w/ exact schema + indexes ─
        command.downgrade(cfg, _LCH)
        with eng.begin() as c:
            assert _table_exists(c, "user_events") and _table_exists(c, "operator_decisions")
            ue = _cols(c, "user_events")
            assert set(ue) == {"id", "user_id", "event_type", "event_scope", "entity_id",
                               "metadata_json", "created_at"}
            assert ue["created_at"][1] == "NO"                  # NOT NULL preserved
            od = _cols(c, "operator_decisions")
            assert od["accepted"] == ("boolean", "NO") and od["ignored"] == ("boolean", "NO")
            assert {"ix_user_events_user_created", "ix_user_events_user_id",
                    "ix_user_events_user_type"} <= _indexes(c, "user_events")
            assert {"ix_op_decision_user_created", "ix_op_decision_user_type"} \
                <= _indexes(c, "operator_decisions")
            # history NOT restored — tables come back empty; users preserved
            assert c.exec_driver_sql("SELECT count(*) FROM user_events").scalar() == 0
            assert c.exec_driver_sql("SELECT count(*) FROM operator_decisions").scalar() == 0
            assert c.exec_driver_sql("SELECT count(*) FROM users").scalar() == 3

        # ── re-upgrade is clean ──────────────────────────────────────────────────
        command.upgrade(cfg, _RBP)
        with eng.begin() as c:
            assert not _table_exists(c, "user_events") and not _table_exists(c, "operator_decisions")
            assert c.exec_driver_sql("SELECT count(*) FROM users").scalar() == 3
    finally:
        eng.dispose()
