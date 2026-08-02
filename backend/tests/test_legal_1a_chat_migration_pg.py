"""LEGAL-1A — real-PostgreSQL 16 proof that removing the "Биржа" chat footprint is data-safe.

A seeded upgrade(rcb→lch) / downgrade(lch→rcb) / re-upgrade run against a REAL PostgreSQL 16 (the
engine production actually uses). This is the gate the SQLite test cannot give: PostgreSQL has a
strict BOOLEAN type, so it catches the classic bug where an integer literal is used for a boolean
column / default. The real Alembic chain does all the work — no create_all, no session_replication_role,
no SQLite fallback — and no other table's FK / CHECK / UNIQUE is disabled.

Proves:
  * rcb pre-state has chat_messages + users.chat_violations (integer) + users.chat_blocked (BOOLEAN);
  * upgrade rcb→lch drops exactly those three, preserves both seeded users + every checked field +
    the unrelated control row, and a fresh auth-compatible user still inserts;
  * downgrade lch→rcb re-adds an EMPTY chat_messages and both columns with a PORTABLE boolean default
    (false — never an integer literal), surviving rows get 0 / false, and the old messages / counters /
    bans are NOT restored (expected);
  * re-upgrade rcb→lch is clean and again drops only the chat table + columns, users intact.
"""
import os

import pytest

_RCB = "rcb1a2b3c4d01"   # lch's down_revision: chat_messages + both columns present here
_LCH = "lch1a2b3c4d01"   # head: the chat footprint is gone


def _pg_sync_url():
    return os.environ.get("PULT_TEST_PG_URL") or os.environ.get("PULT_PG_URL")


def _pg_alembic_url():
    explicit = os.environ.get("PULT_TEST_PG_ALEMBIC_URL")
    if explicit:
        return explicit
    sync = _pg_sync_url() or ""
    return sync.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


def _user_cols(c):
    # name -> (data_type, is_nullable) straight from PostgreSQL's own catalog.
    return {r[0]: (r[1], r[2]) for r in c.exec_driver_sql(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name='users'")}


def _table_exists(c, name):
    return c.exec_driver_sql(f"SELECT to_regclass('public.{name}') IS NOT NULL").scalar()


def _seed_rcb(c):
    # rcb pre-state seed. Real boolean literals (TRUE/FALSE) — PostgreSQL REJECTS an integer 0/1 for a
    # boolean column, which is exactly the portability trap this test exists to catch. Two users with
    # distinct non-zero chat_violations and a real TRUE chat_blocked, plus two messages between them.
    c.exec_driver_sql(
        "INSERT INTO users (id,email,name,hashed_password,plan,chat_violations,chat_blocked,is_verified) "
        "VALUES ('u1','a@b.com','Ann','h1','profi',2,TRUE,TRUE)")
    c.exec_driver_sql(
        "INSERT INTO users (id,email,name,hashed_password,plan,chat_violations,chat_blocked,is_verified) "
        "VALUES ('u2','c@d.com','Bob','h2','maximum',4,FALSE,TRUE)")
    c.exec_driver_sql("INSERT INTO chat_messages (id,user_id,message) VALUES ('m1','u1','hi from u1')")
    c.exec_driver_sql("INSERT INTO chat_messages (id,user_id,message) VALUES ('m2','u2','reply from u2')")
    # Control row in a table the migration MUST NOT touch (unrelated to chat) — proves collateral safety.
    c.exec_driver_sql(
        "INSERT INTO execution_logs (id,user_id,action_type,mode,payload,status) "
        "VALUES ('e1','u1','set_price','manual_l3','{}','success')")


def test_pg_legal1a_seeded_roundtrip(monkeypatch):
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

        # ── A + B. schema at rcb, then seed non-empty data ────────────────────────
        command.upgrade(cfg, _RCB)
        with eng.begin() as c:
            cols = _user_cols(c)
            assert "chat_violations" in cols and "chat_blocked" in cols
            assert cols["chat_blocked"][0] == "boolean"        # a REAL boolean column on PG
            assert cols["chat_violations"][0] == "integer"
            assert _table_exists(c, "chat_messages")
            _seed_rcb(c)
            assert c.exec_driver_sql("SELECT count(*) FROM users").scalar() == 2
            assert c.exec_driver_sql("SELECT count(*) FROM chat_messages").scalar() == 2

        # ── C. upgrade rcb→lch: chat footprint gone, everything else intact ───────
        command.upgrade(cfg, _LCH)
        with eng.begin() as c:
            cols = _user_cols(c)
            assert "chat_violations" not in cols
            assert "chat_blocked" not in cols
            assert not _table_exists(c, "chat_messages")
            # both users + every checked field survive
            rows = {r[0]: r[1] for r in c.exec_driver_sql(
                "SELECT id,email FROM users ORDER BY id")}
            assert rows == {"u1": "a@b.com", "u2": "c@d.com"}
            u1 = c.exec_driver_sql(
                "SELECT plan,is_verified,hashed_password FROM users WHERE id='u1'").first()
            assert tuple(u1) == ("profi", True, "h1")
            # auth-compatible insert of a NEW user still works (no chat columns needed)
            c.exec_driver_sql(
                "INSERT INTO users (id,email,name,hashed_password) VALUES ('u3','e@f.com','Cy','h3')")
            assert c.exec_driver_sql("SELECT count(*) FROM users").scalar() == 3
            # unrelated tables + control row untouched (collateral safety)
            for t in ("workspaces", "execution_logs", "api_credentials"):
                assert _table_exists(c, t), t
            assert c.exec_driver_sql(
                "SELECT status FROM execution_logs WHERE id='e1'").scalar() == "success"

        # ── D. downgrade lch→rcb: empty table back, columns back w/ PORTABLE defaults ─
        command.downgrade(cfg, _RCB)
        with eng.begin() as c:
            cols = _user_cols(c)
            assert cols["chat_violations"] == ("integer", "NO")     # Integer NOT NULL
            assert cols["chat_blocked"] == ("boolean", "NO")        # Boolean NOT NULL (a REAL bool)
            assert _table_exists(c, "chat_messages")
            assert c.exec_driver_sql("SELECT count(*) FROM chat_messages").scalar() == 0   # empty
            # surviving users get the DEFAULTS (0 / false) — old counters / bans NOT restored
            vals = [tuple(r) for r in c.exec_driver_sql(
                "SELECT chat_violations, chat_blocked FROM users WHERE id IN ('u1','u2','u3') "
                "ORDER BY id")]
            assert vals == [(0, False), (0, False), (0, False)]
            # PORTABLE boolean default proven: insert WITHOUT the chat columns → 0 / false, and NO
            # "integer = boolean" error (an integer '0' default literal would have failed here on PG).
            c.exec_driver_sql(
                "INSERT INTO users (id,email,name,hashed_password) VALUES ('u4','g@h.com','Di','h4')")
            u4 = c.exec_driver_sql(
                "SELECT chat_violations, chat_blocked FROM users WHERE id='u4'").first()
            assert tuple(u4) == (0, False)

        # ── E. re-upgrade rcb→lch is clean: chat gone again, users preserved ──────
        command.upgrade(cfg, _LCH)
        with eng.begin() as c:
            cols = _user_cols(c)
            assert "chat_violations" not in cols and "chat_blocked" not in cols
            assert not _table_exists(c, "chat_messages")
            assert c.exec_driver_sql("SELECT count(*) FROM users").scalar() == 4   # u1..u4 preserved
    finally:
        eng.dispose()
