"""LEGAL-1A — migration lch1a2b3c4d01 on NON-EMPTY data (SQLite up / down / re-up).

Proves that dropping the "Биржа" footprint (chat_messages table + users.chat_violations +
users.chat_blocked) preserves every other user field and every unrelated table, that downgrade
restores the empty table + the two columns with their original defaults (without claiming the old
messages / counters are back), and that a re-upgrade is clean.
"""
import sqlite3

from alembic import command

import models  # noqa: F401 — registers metadata

_RCB = "rcb1a2b3c4d01"   # lch's down_revision: chat_messages + both columns still present here


def _cfg(monkeypatch, tmp_path):
    import db_migrations as dbm
    dbfile = tmp_path / "legal1a.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{dbfile.as_posix()}")
    return dbm._alembic_config(), dbfile


def _user_cols(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        return {r[1]: (r[2], r[3], r[4]) for r in c.execute("PRAGMA table_info(users)")}  # name:(type,notnull,default)
    finally:
        c.close()


def _table_exists(dbfile, name):
    c = sqlite3.connect(str(dbfile))
    try:
        return c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None
    finally:
        c.close()


def _seed_two_users_and_messages(dbfile):
    c = sqlite3.connect(str(dbfile))
    try:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute(
            "INSERT INTO users (id, email, name, hashed_password, plan, chat_violations, chat_blocked, is_verified) "
            "VALUES ('u1', 'a@b.com', 'Ann', 'h1', 'profi', 2, 0, 1)"
        )
        c.execute(
            "INSERT INTO users (id, email, name, hashed_password, plan, chat_violations, chat_blocked, is_verified) "
            "VALUES ('u2', 'c@d.com', 'Bob', 'h2', 'maximum', 4, 1, 1)"
        )
        c.execute("INSERT INTO chat_messages (id, user_id, message) VALUES ('m1', 'u1', 'hi from u1')")
        c.execute("INSERT INTO chat_messages (id, user_id, message) VALUES ('m2', 'u2', 'reply from u2')")
        c.commit()
    finally:
        c.close()


def test_lch_migration_nonempty_roundtrip(monkeypatch, tmp_path):
    cfg, dbfile = _cfg(monkeypatch, tmp_path)

    # ── pre-state: rcb has chat_messages + the two columns ───────────────────────
    command.upgrade(cfg, _RCB)
    _seed_two_users_and_messages(dbfile)
    pre = _user_cols(dbfile)
    assert "chat_violations" in pre and "chat_blocked" in pre
    assert _table_exists(dbfile, "chat_messages")

    # ── upgrade to head (lch): drops the table + both columns ────────────────────
    command.upgrade(cfg, "head")
    post = _user_cols(dbfile)
    assert "chat_violations" not in post
    assert "chat_blocked" not in post
    assert not _table_exists(dbfile, "chat_messages")

    # every OTHER user field + both rows survive, non-chat data intact
    c = sqlite3.connect(str(dbfile))
    try:
        rows = dict(c.execute("SELECT id, email FROM users ORDER BY id").fetchall())
        assert rows == {"u1": "a@b.com", "u2": "c@d.com"}
        plan1 = c.execute("SELECT plan, is_verified, hashed_password FROM users WHERE id='u1'").fetchone()
        assert plan1 == ("profi", 1, "h1")
        # auth path not broken: a fresh user still inserts (no chat columns required)
        c.execute("INSERT INTO users (id, email, name, hashed_password) VALUES ('u3','e@f.com','Cy','h3')")
        c.commit()
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
        # unrelated tables untouched (spot-check a few that must still exist)
        for t in ("workspaces", "execution_logs", "api_credentials"):
            assert _table_exists(dbfile, t), t
    finally:
        c.close()

    # ── downgrade to rcb: empty table back, columns back with default 0 ──────────
    command.downgrade(cfg, _RCB)
    back = _user_cols(dbfile)
    assert back["chat_violations"][1] == 1      # NOT NULL
    assert back["chat_blocked"][1] == 1         # NOT NULL
    assert _table_exists(dbfile, "chat_messages")
    c = sqlite3.connect(str(dbfile))
    try:
        # messages are NOT restored — the table comes back empty
        assert c.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
        # counters/bans are NOT restored — every surviving row gets the default 0
        vals = c.execute("SELECT chat_violations, chat_blocked FROM users ORDER BY id").fetchall()
        assert all(v == (0, 0) for v in vals)
    finally:
        c.close()

    # ── re-upgrade is clean ──────────────────────────────────────────────────────
    command.upgrade(cfg, "head")
    assert "chat_violations" not in _user_cols(dbfile)
    assert not _table_exists(dbfile, "chat_messages")
