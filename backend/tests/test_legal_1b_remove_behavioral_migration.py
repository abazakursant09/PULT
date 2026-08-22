"""LEGAL-1B — migration rbp1a2b3c4d01 on NON-EMPTY data (SQLite up / down / re-up).

Proves that dropping the behavioural stores (user_events + operator_decisions) preserves every user
and the objective outcome-learning table (decision_memory), that downgrade restores BOTH tables EMPTY
with their exact original columns + indexes (without claiming the old events / choices are back), and
that a re-upgrade is clean.
"""
import sqlite3

from alembic import command

import models  # noqa: F401 — registers metadata

_LCH = "lch1a2b3c4d01"   # rbp's down_revision: user_events + operator_decisions still present here


def _cfg(monkeypatch, tmp_path):
    import db_migrations as dbm
    dbfile = tmp_path / "legal1b.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    return dbm._alembic_config(), dbfile


def _tables(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()


def _indexes(dbfile, table):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1] for r in c.execute(f"PRAGMA index_list({table})")}
    finally:
        c.close()


def _cols(dbfile, table):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1]: (r[2], r[3]) for r in c.execute(f"PRAGMA table_info({table})")}  # name:(type,notnull)
    finally:
        c.close()


def _seed(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        for uid, plan in (("u1", "profi"), ("u2", "maximum")):
            c.execute("INSERT INTO users (id,email,name,hashed_password,plan,is_verified) "
                      "VALUES (?,?,?,?,?,1)", (uid, f"{uid}@b.com", uid.upper(), "h", plan))
        # behavioural rows that MUST be dropped
        c.execute("INSERT INTO user_events (id,user_id,event_type,event_scope,created_at) "
                  "VALUES ('e1','u1','insight_opened','action_engine','2026-01-01 00:00:00')")
        c.execute("INSERT INTO user_events (id,user_id,event_type,event_scope,created_at) "
                  "VALUES ('e2','u2','copilot_dismissed','dashboard','2026-01-02 00:00:00')")
        c.execute("INSERT INTO operator_decisions (id,user_id,insight_type,action_taken,accepted,ignored) "
                  "VALUES ('d1','u1','margin_crisis','accepted',1,0)")
        c.execute("INSERT INTO operator_decisions (id,user_id,insight_type,action_taken,accepted,ignored) "
                  "VALUES ('d2','u2','stockout','dismissed_again',0,1)")
        c.commit()
    finally:
        c.close()


def test_rbp_migration_nonempty_roundtrip(monkeypatch, tmp_path):
    cfg, dbfile = _cfg(monkeypatch, tmp_path)

    # ── pre-state: lch has both behavioural tables ───────────────────────────────
    command.upgrade(cfg, _LCH)
    _seed(dbfile)
    pre = _tables(dbfile)
    assert {"user_events", "operator_decisions", "decision_memory", "users"} <= pre

    # ── upgrade to head (rbp): both behavioural tables dropped ────────────────────
    command.upgrade(cfg, "head")
    post = _tables(dbfile)
    assert "user_events" not in post
    assert "operator_decisions" not in post
    # objective outcome-learning + users survive
    assert "decision_memory" in post
    c = sqlite3.connect(str(dbfile))
    try:
        rows = dict(c.execute("SELECT id, email FROM users ORDER BY id").fetchall())
        assert rows == {"u1": "u1@b.com", "u2": "u2@b.com"}
        # auth-compatible insert of a new user still works (no behavioural columns needed)
        c.execute("INSERT INTO users (id,email,name,hashed_password) VALUES ('u3','u3@b.com','U3','h')")
        c.commit()
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
        # unrelated tables untouched (spot-check)
        for t in ("workspaces", "execution_logs", "api_credentials", "decision_memory"):
            assert t in post, t
    finally:
        c.close()

    # ── downgrade to lch: both tables recreated EMPTY with exact schema + indexes ─
    command.downgrade(cfg, _LCH)
    back = _tables(dbfile)
    assert {"user_events", "operator_decisions"} <= back
    ue_cols = _cols(dbfile, "user_events")
    assert set(ue_cols) == {"id", "user_id", "event_type", "event_scope", "entity_id",
                            "metadata_json", "created_at"}
    assert ue_cols["created_at"][1] == 1 and ue_cols["event_scope"][1] == 1     # NOT NULL preserved
    od_cols = _cols(dbfile, "operator_decisions")
    assert set(od_cols) == {"id", "user_id", "insight_type", "marketplace", "product_name",
                            "action_taken", "accepted", "ignored", "resolved_after_days",
                            "created_at", "effect_observed", "effect_duration_days",
                            "recurrence_after_days", "validated_at"}
    assert {"ix_user_events_user_created", "ix_user_events_user_id", "ix_user_events_user_type"} \
        <= _indexes(dbfile, "user_events")
    assert {"ix_op_decision_user_created", "ix_op_decision_user_type"} \
        <= _indexes(dbfile, "operator_decisions")
    c = sqlite3.connect(str(dbfile))
    try:
        # events / choices are NOT restored — the tables come back empty
        assert c.execute("SELECT COUNT(*) FROM user_events").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM operator_decisions").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3   # users preserved
    finally:
        c.close()

    # ── re-upgrade is clean and again drops only the two behavioural tables ───────
    command.upgrade(cfg, "head")
    fin = _tables(dbfile)
    assert "user_events" not in fin and "operator_decisions" not in fin
    assert "decision_memory" in fin

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["csr1a2b3c4d01"]
