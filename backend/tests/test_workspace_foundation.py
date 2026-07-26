"""
F1.0 — Workspace ownership foundation.

Locks the ownership boundary that marketplace accounts and (later) evidence will be
keyed on:

  * `workspaces.id` is an INDEPENDENT uuid4 — never equal to `users.id`. Both are
    String(36) uuid4 strings, so a shared value would make them silently
    interchangeable, and workspace_id will enter the evidence fact key.
  * exactly one workspace per user, enforced by UNIQUE(owner_user_id) in the DB.
  * the M0 backfill gives every existing user a workspace, modifies no `users` row,
    and is idempotent by result.
  * a NEW user gets a workspace in the SAME transaction as the user itself, so a
    failed registration leaves no orphan.
  * account deletion is a SOFT delete and re-registration restores the same user row,
    so the FK needs no ON DELETE clause and restoration must not create a 2nd workspace.
"""
import asyncio
import os
import tempfile
import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  (registers tables)
from models.user import User
from models.workspace import Workspace

import routers.auth as auth
from routers.auth import register
from schemas.auth import UserRegister

REV = "pev1a2b3c4d01"      # current alembic head; these tests upgrade to "head"
PRIOR = "mcs1a2b3c4d01"    # the revision before F1.0 — where `workspaces` does not exist yet
TABLE = "workspaces"


# ── helpers (native repo pattern: temp sqlite + alembic command API) ──────────

def _run(c):
    return asyncio.run(c)


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


def _rows(sync_url, stmt):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return c.execute(sa.text(stmt)).fetchall()
    finally:
        eng.dispose()


def _seed_users(sync_url, n):
    """Insert n users directly, at the PRIOR revision (before `workspaces` exists)."""
    eng = sa.create_engine(sync_url)
    ids = [str(uuid.uuid4()) for _ in range(n)]
    try:
        with eng.begin() as c:
            for i, uid in enumerate(ids):
                c.execute(
                    sa.text(
                        "INSERT INTO users (id, email, name, hashed_password, created_at, "
                        "plan, chat_violations, chat_blocked, is_verified, was_referrer, "
                        "was_referred, is_restored) "
                        "VALUES (:id, :email, :name, 'x', :created, 'master', 0, 0, 1, 0, 0, 0)"
                    ),
                    {"id": uid, "email": f"u{i}@b.com", "name": f"U{i}",
                     "created": datetime.utcnow()},
                )
    finally:
        eng.dispose()
    return ids


def _migrated_db(monkeypatch, *, users=0, to="head"):
    """Upgrade a fresh temp DB to PRIOR, seed users, then upgrade to `to`."""
    tmp = os.path.join(tempfile.mkdtemp(), "workspace_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"

    import db_migrations as dbm
    cfg = dbm._alembic_config()

    command.upgrade(cfg, PRIOR)
    user_ids = _seed_users(sync_url, users) if users else []
    command.upgrade(cfg, to)
    return cfg, sync_url, user_ids


async def _orm_session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _Req:
    headers: dict = {}

    class _C:
        host = "1.2.3.4"
    client = _C()


def _patch_email(monkeypatch):
    async def _noop(*a, **kw):
        return True
    monkeypatch.setattr(auth, "send_verification_email", _noop)
    return None


# ── A. alembic head / single head ────────────────────────────────────────────

def test_alembic_single_head_is_current_revision(monkeypatch):
    """(12) exactly one head; (13) it is the current head revision."""
    tmp = os.path.join(tempfile.mkdtemp(), "head_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    import db_migrations as dbm
    heads = ScriptDirectory.from_config(dbm._alembic_config()).get_heads()
    assert heads == [REV], f"expected single head {REV}, got {heads}"


# ── B. existing-user backfill ────────────────────────────────────────────────

def test_backfill_gives_every_existing_user_exactly_one_workspace(monkeypatch):
    """(1) N users before M0 -> exactly N workspaces after, 1:1, no loss."""
    _cfg, sync_url, user_ids = _migrated_db(monkeypatch, users=3)

    ws = _rows(sync_url, "SELECT id, owner_user_id FROM workspaces")
    assert len(ws) == 3
    assert {w[1] for w in ws} == set(user_ids)          # 1:1 onto the seeded users
    assert len({w[0] for w in ws}) == 3                 # distinct workspace ids


def test_workspace_id_is_independent_of_user_id(monkeypatch):
    """(2) workspaces.id must NEVER equal owner_user_id — the whole point of the PK choice."""
    _cfg, sync_url, user_ids = _migrated_db(monkeypatch, users=3)

    ws = _rows(sync_url, "SELECT id, owner_user_id FROM workspaces")
    for wid, uid in ws:
        assert wid != uid, "workspaces.id must be an independent uuid4, not the user id"
    # and no workspace id collides with ANY user id
    assert {w[0] for w in ws}.isdisjoint(set(user_ids))


def test_backfill_is_idempotent_by_result(monkeypatch):
    """(3) re-running the backfill selector finds nothing left to insert."""
    _cfg, sync_url, _ = _migrated_db(monkeypatch, users=3)

    remaining = _rows(
        sync_url,
        "SELECT u.id FROM users u "
        "WHERE NOT EXISTS (SELECT 1 FROM workspaces w WHERE w.owner_user_id = u.id)",
    )
    assert remaining == [], "a second backfill pass would insert rows -> not idempotent"
    assert len(_rows(sync_url, "SELECT id FROM workspaces")) == 3


def test_unique_owner_user_id_forbids_second_workspace(monkeypatch):
    """(4) UNIQUE(owner_user_id) enforces the launch invariant at the DB level."""
    _cfg, sync_url, user_ids = _migrated_db(monkeypatch, users=1)

    eng = sa.create_engine(sync_url)
    try:
        with pytest.raises(IntegrityError):
            with eng.begin() as c:
                c.execute(
                    sa.text("INSERT INTO workspaces (id, owner_user_id, created_at) "
                            "VALUES (:id, :owner, :created)"),
                    {"id": str(uuid.uuid4()), "owner": user_ids[0],
                     "created": datetime.utcnow()},
                )
    finally:
        eng.dispose()


def test_backfill_does_not_modify_users(monkeypatch):
    """(5) M0 must not touch a single row of `users`."""
    tmp = os.path.join(tempfile.mkdtemp(), "workspace_users_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"

    import db_migrations as dbm
    cfg = dbm._alembic_config()

    command.upgrade(cfg, PRIOR)
    _seed_users(sync_url, 3)
    before = _rows(sync_url, "SELECT * FROM users ORDER BY id")

    command.upgrade(cfg, "head")
    after = _rows(sync_url, "SELECT * FROM users ORDER BY id")

    assert before == after, "M0 modified `users` — it must be additive only"


# ── C. migration roundtrip ───────────────────────────────────────────────────

def test_upgrade_downgrade_upgrade_roundtrip(monkeypatch):
    """(10) upgrade -> downgrade -> upgrade; (11) downgrade leaves `users` intact."""
    tmp = os.path.join(tempfile.mkdtemp(), "workspace_roundtrip.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"

    import db_migrations as dbm
    cfg = dbm._alembic_config()

    command.upgrade(cfg, PRIOR)
    _seed_users(sync_url, 2)
    users_before = _rows(sync_url, "SELECT * FROM users ORDER BY id")

    command.upgrade(cfg, "head")
    assert _current(sync_url) == REV
    assert TABLE in _tables(sync_url)
    assert len(_rows(sync_url, "SELECT id FROM workspaces")) == 2

    command.downgrade(cfg, PRIOR)
    assert _current(sync_url) == PRIOR
    assert TABLE not in _tables(sync_url)
    assert _rows(sync_url, "SELECT * FROM users ORDER BY id") == users_before  # (11)

    command.upgrade(cfg, "head")
    assert _current(sync_url) == REV
    assert TABLE in _tables(sync_url)
    assert len(_rows(sync_url, "SELECT id FROM workspaces")) == 2   # re-backfilled


def test_model_matches_migration_shape(monkeypatch):
    """(9) the SQLAlchemy model and the migrated table describe the same thing.

    (The repo-wide model==head invariant is owned by tests/test_schema_drift.py; this
    asserts the F1.0 specifics: columns, and the unique index that carries the invariant.)
    """
    _cfg, sync_url, _ = _migrated_db(monkeypatch, users=1)

    assert set(Workspace.__table__.columns.keys()) == {"id", "owner_user_id", "created_at"}

    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            insp = sa.inspect(c)
            cols = {col["name"] for col in insp.get_columns(TABLE)}
            assert cols == {"id", "owner_user_id", "created_at"}
            uniques = {ix["name"] for ix in insp.get_indexes(TABLE) if ix["unique"]}
            assert "uq_workspace_owner" in uniques
    finally:
        eng.dispose()


# ── D. new-user registration ─────────────────────────────────────────────────

def test_new_user_registration_creates_exactly_one_workspace(monkeypatch):
    """(6) a newly registered user owns exactly one workspace."""
    _patch_email(monkeypatch)

    async def go():
        db = await _orm_session()
        await register(UserRegister(email="n@b.com", name="N", password="Passw0rd"), _Req(), db)

        user = (await db.execute(sa.select(User).where(User.email == "n@b.com"))).scalar_one()
        ws = (await db.execute(
            sa.select(Workspace).where(Workspace.owner_user_id == user.id))).scalars().all()

        assert len(ws) == 1
        assert ws[0].id != user.id                      # independent uuid4
        assert ws[0].created_at is not None
    _run(go())


def test_user_and_workspace_are_created_in_one_transaction(monkeypatch):
    """(7)+(8) the workspace lives in the SAME transaction as the user: if the
    registration transaction never commits, neither row survives — no orphan workspace."""
    _patch_email(monkeypatch)

    async def go():
        db = await _orm_session()

        async def _boom():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", _boom)

        with pytest.raises(RuntimeError):
            await register(UserRegister(email="rb@b.com", name="RB", password="Passw0rd"),
                           _Req(), db)

        await db.rollback()

        users = (await db.execute(
            sa.select(User).where(User.email == "rb@b.com"))).scalars().all()
        workspaces = (await db.execute(sa.select(Workspace))).scalars().all()

        assert users == [], "user survived a failed registration"
        assert workspaces == [], "ORPHAN WORKSPACE: workspace outlived a failed registration"
    _run(go())


# ── E. account deletion / restoration (the FK checkpoint) ────────────────────

def test_soft_delete_and_restore_keep_exactly_one_workspace(monkeypatch):
    """(14) Account deletion is a SOFT delete (routers/referrals.py sets users.deleted_at;
    the row is never removed) and re-registration RESTORES the same user row
    (routers/auth.py) rather than creating a new one.

    Therefore: the FK needs no ON DELETE clause (the referenced row never disappears),
    the workspace survives deletion, and restoration must NOT create a second workspace —
    which UNIQUE(owner_user_id) would reject with an IntegrityError anyway."""
    _patch_email(monkeypatch)

    async def go():
        db = await _orm_session()

        await register(UserRegister(email="sd@b.com", name="SD", password="Passw0rd"),
                       _Req(), db)
        user = (await db.execute(sa.select(User).where(User.email == "sd@b.com"))).scalar_one()
        original_ws = (await db.execute(
            sa.select(Workspace).where(Workspace.owner_user_id == user.id))).scalar_one()

        # soft delete — exactly what DELETE /api/account does (routers/referrals.py)
        user.deleted_at = datetime.utcnow()
        await db.commit()

        # the user row (and so the FK target) still exists; the workspace is untouched
        still = (await db.execute(
            sa.select(Workspace).where(Workspace.owner_user_id == user.id))).scalars().all()
        assert len(still) == 1
        assert still[0].id == original_ws.id

        # re-registration restores the SAME user row -> must not mint a 2nd workspace
        await register(UserRegister(email="sd@b.com", name="SD", password="Passw0rd"),
                       _Req(), db)

        restored = (await db.execute(
            sa.select(User).where(User.email == "sd@b.com"))).scalar_one()
        assert restored.id == user.id                   # same row reused
        assert restored.deleted_at is None
        assert restored.is_restored is True

        after = (await db.execute(
            sa.select(Workspace).where(Workspace.owner_user_id == user.id))).scalars().all()
        assert len(after) == 1, "restoration created a second workspace"
        assert after[0].id == original_ws.id            # the original workspace, preserved
    _run(go())
