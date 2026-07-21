"""
F1.1 — Marketplace account identity foundation.

Locks the identity boundary that evidence and discovery will later be keyed on:

  * `MarketplaceAccount` is the STABLE identity of an external seller cabinet. It is not
    a credential: it must survive rotation and reconnect with the same id.
  * one VERIFIED external cabinet belongs to at most ONE workspace, GLOBALLY — carried by
    UNIQUE(marketplace, external_account_id), with workspace_id deliberately NOT in the key.
  * `external_account_id IS NULL` until discovery. NULL rows coexist because SQLite and
    PostgreSQL both treat NULLs as distinct in an ordinary UNIQUE constraint, so an
    unverified cabinet sits outside the uniqueness space.
  * discovery will claim ownership by UPDATE, not INSERT — so the constraint must bite on
    UPDATE too. That is asserted here, ahead of the slice that relies on it.
  * legacy connections are backfilled with their OWN unverified_legacy account and NO
    invented external id; connections whose user has no workspace survive with NULL links.
  * F1.1 is inert: no discovery, no marketplace call, no Yandex, no executor change.
"""
import asyncio
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  (registers tables)
from models.api_credential import ApiCredential
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.user import User
from models.workspace import Workspace

from routers.connections import create_connection
from schemas.marketplace import ConnectionCreate
from services.marketplace import executor
from services.workspace_resolver import WorkspaceMissing, resolve_workspace_id

REV = "spl1a2b3c4d01"
PRIOR = "wsp1a2b3c4d01"
TABLE = "marketplace_accounts"
CONNS = "marketplace_connections"


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


def _seed_legacy(sync_url, *, connections):
    """Seed users / workspaces / connections / credentials at PRIOR (before F1.1).

    `connections` is a list of dicts: {marketplace, label, ozon_client_id, with_workspace}.
    `with_workspace=False` seeds an ORPHAN connection — a user_id that resolves to no
    workspace at all, which the missing FK on marketplace_connections.user_id permits.
    """
    eng = sa.create_engine(sync_url)
    seeded = []
    try:
        with eng.begin() as c:
            for i, spec in enumerate(connections):
                user_id = str(uuid.uuid4())
                c.execute(
                    sa.text(
                        "INSERT INTO users (id, email, name, hashed_password, created_at, "
                        "plan, chat_violations, chat_blocked, is_verified, was_referrer, "
                        "was_referred, is_restored) "
                        "VALUES (:id, :email, :name, 'x', :created, 'master', 0, 0, 1, 0, 0, 0)"
                    ),
                    {"id": user_id, "email": f"legacy{i}@b.com", "name": f"L{i}",
                     "created": datetime.utcnow()},
                )

                workspace_id = None
                if spec.get("with_workspace", True):
                    workspace_id = str(uuid.uuid4())
                    c.execute(
                        sa.text("INSERT INTO workspaces (id, owner_user_id, created_at) "
                                "VALUES (:id, :owner, :created)"),
                        {"id": workspace_id, "owner": user_id, "created": datetime.utcnow()},
                    )
                else:
                    # An orphan connection: point it at a user id that does not exist, so
                    # no workspace can ever resolve for it.
                    user_id = str(uuid.uuid4())

                conn_id = str(uuid.uuid4())
                c.execute(
                    sa.text(
                        "INSERT INTO marketplace_connections "
                        "(id, user_id, marketplace, label, status, scopes, ozon_client_id, "
                        " created_at, updated_at) "
                        "VALUES (:id, :user_id, :mp, :label, 'connected', '[]', :ozon, "
                        " :created, :created)"
                    ),
                    {"id": conn_id, "user_id": user_id, "mp": spec["marketplace"],
                     "label": spec.get("label"), "ozon": spec.get("ozon_client_id"),
                     "created": datetime.utcnow()},
                )

                cred_id = str(uuid.uuid4())
                c.execute(
                    sa.text(
                        "INSERT INTO api_credentials "
                        "(id, connection_id, scope, secret_enc, meta, created_at, updated_at) "
                        "VALUES (:id, :conn, 'prices', :blob, '{}', :created, :created)"
                    ),
                    {"id": cred_id, "conn": conn_id, "blob": b"ciphertext",
                     "created": datetime.utcnow()},
                )

                seeded.append({"user_id": user_id, "workspace_id": workspace_id,
                               "connection_id": conn_id, "credential_id": cred_id})
    finally:
        eng.dispose()
    return seeded


def _migrated_db(monkeypatch, *, connections=(), to="head"):
    """Fresh temp DB -> PRIOR -> seed legacy rows -> upgrade to `to`."""
    tmp = os.path.join(tempfile.mkdtemp(), "identity_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"

    import db_migrations as dbm
    cfg = dbm._alembic_config()

    command.upgrade(cfg, PRIOR)
    seeded = _seed_legacy(sync_url, connections=list(connections)) if connections else []
    command.upgrade(cfg, to)
    return cfg, sync_url, seeded


async def _orm_session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _user_with_workspace(db, email="u@b.com"):
    user = User(id=str(uuid.uuid4()), email=email, name="U", hashed_password="x")
    db.add(user)
    workspace = Workspace(id=str(uuid.uuid4()), owner_user_id=user.id,
                          created_at=datetime.utcnow())
    db.add(workspace)
    await db.commit()
    return user, workspace


def _body(**kw):
    base = {"marketplace": "wildberries", "token": "t0ken", "scope": "prices"}
    base.update(kw)
    return ConnectionCreate(**base)


# ── A. schema / model shape ──────────────────────────────────────────────────

def test_alembic_single_head_is_current_revision(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "head_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    import db_migrations as dbm
    heads = ScriptDirectory.from_config(dbm._alembic_config()).get_heads()
    assert heads == [REV], f"expected single head {REV}, got {heads}"


def test_marketplace_accounts_table_shape(monkeypatch):
    """The migrated table carries exactly the approved identity fields and nullability."""
    _cfg, sync_url, _ = _migrated_db(monkeypatch)
    assert TABLE in _tables(sync_url)

    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            cols = {col["name"]: col for col in sa.inspect(c).get_columns(TABLE)}
    finally:
        eng.dispose()

    assert set(cols) == {"id", "workspace_id", "marketplace", "external_account_id",
                         "identity_status", "label", "created_at", "updated_at"}
    assert cols["workspace_id"]["nullable"] is False
    assert cols["marketplace"]["nullable"] is False
    assert cols["identity_status"]["nullable"] is False
    assert cols["external_account_id"]["nullable"] is True   # NULL until discovery
    assert cols["label"]["nullable"] is True

    # the model and the migration describe the same table
    assert set(MarketplaceAccount.__table__.columns.keys()) == set(cols)


def test_workspace_foreign_key_exists(monkeypatch):
    _cfg, sync_url, _ = _migrated_db(monkeypatch)
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            fks = sa.inspect(c).get_foreign_keys(TABLE)
    finally:
        eng.dispose()

    targets = {(fk["referred_table"], tuple(fk["constrained_columns"])) for fk in fks}
    assert ("workspaces", ("workspace_id",)) in targets


def test_unique_key_is_marketplace_plus_external_id_only(monkeypatch):
    """The ownership invariant lives in the DB — and workspace_id is NOT part of it.

    A key containing workspace_id would let the same verified cabinet be claimed by two
    workspaces at once, which is exactly what the launch invariant forbids.
    """
    _cfg, sync_url, _ = _migrated_db(monkeypatch)
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            uniques = sa.inspect(c).get_unique_constraints(TABLE)
            indexes = {ix["name"] for ix in sa.inspect(c).get_indexes(TABLE)}
    finally:
        eng.dispose()

    by_name = {u["name"]: u["column_names"] for u in uniques}
    assert "uq_mp_account_mp_ext" in by_name
    assert by_name["uq_mp_account_mp_ext"] == ["marketplace", "external_account_id"]
    assert "workspace_id" not in by_name["uq_mp_account_mp_ext"]

    # and no other unique key smuggles workspace_id into the identity
    for cols in by_name.values():
        assert "workspace_id" not in cols

    assert "ix_mp_account_ws" in indexes


def test_connection_identity_links(monkeypatch):
    """marketplace_connections gains both identity links, nullable, plus the account index."""
    _cfg, sync_url, _ = _migrated_db(monkeypatch)
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            insp = sa.inspect(c)
            cols = {col["name"]: col for col in insp.get_columns(CONNS)}
            indexes = {ix["name"] for ix in insp.get_indexes(CONNS)}
            fks = {(fk["referred_table"], tuple(fk["constrained_columns"]))
                   for fk in insp.get_foreign_keys(CONNS)}
    finally:
        eng.dispose()

    assert cols["workspace_id"]["nullable"] is True
    assert cols["marketplace_account_id"]["nullable"] is True
    assert "ix_mp_conn_account" in indexes
    assert ("workspaces", ("workspace_id",)) in fks
    assert ("marketplace_accounts", ("marketplace_account_id",)) in fks

    # the pre-F1.1 indexes survived the batch rebuild
    assert {"ix_mp_conn_user", "ix_mp_conn_user_mp"} <= indexes


# ── B. global ownership (the launch invariant) ───────────────────────────────

def _account(workspace_id, marketplace, external_account_id, status="verified"):
    return MarketplaceAccount(
        id=str(uuid.uuid4()), workspace_id=workspace_id, marketplace=marketplace,
        external_account_id=external_account_id, identity_status=status,
    )


def test_same_verified_identity_cannot_exist_in_two_workspaces():
    """ONE verified external cabinet -> at most ONE workspace, globally."""
    async def go():
        db = await _orm_session()
        _u_a, ws_a = await _user_with_workspace(db, "a@b.com")
        _u_b, ws_b = await _user_with_workspace(db, "b@b.com")

        db.add(_account(ws_a.id, "wildberries", "seller_123"))
        await db.commit()

        db.add(_account(ws_b.id, "wildberries", "seller_123"))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
    _run(go())


def test_same_external_id_on_a_different_marketplace_coexists():
    """The key is (marketplace, external_account_id) — ids are only unique per marketplace."""
    async def go():
        db = await _orm_session()
        _u, ws = await _user_with_workspace(db)

        db.add(_account(ws.id, "wildberries", "seller_123"))
        db.add(_account(ws.id, "ozon", "seller_123"))
        await db.commit()

        rows = (await db.execute(sa.select(MarketplaceAccount))).scalars().all()
        assert len(rows) == 2
    _run(go())


def test_null_external_ids_coexist():
    """Unverified cabinets sit OUTSIDE the uniqueness space — NULLs are distinct in
    both SQLite and PostgreSQL, which is what lets the legacy backfill run at all."""
    async def go():
        db = await _orm_session()
        _u_a, ws_a = await _user_with_workspace(db, "a@b.com")
        _u_b, ws_b = await _user_with_workspace(db, "b@b.com")

        db.add(_account(ws_a.id, "wildberries", None, "unverified_legacy"))
        db.add(_account(ws_a.id, "wildberries", None, "unverified_legacy"))
        db.add(_account(ws_b.id, "wildberries", None, "unverified"))
        await db.commit()

        rows = (await db.execute(sa.select(MarketplaceAccount))).scalars().all()
        assert len(rows) == 3
    _run(go())


def test_update_from_null_to_taken_identity_fails():
    """Discovery claims ownership by UPDATE, not INSERT — so the constraint must bite there.

    This is the exact operation a second workspace will run when its cabinet turns out to
    be one that is already owned. The DB refuses it, and the owning row is never read —
    so nothing about its owner can leak.
    """
    async def go():
        db = await _orm_session()
        _u_a, ws_a = await _user_with_workspace(db, "a@b.com")
        _u_b, ws_b = await _user_with_workspace(db, "b@b.com")

        owned = _account(ws_a.id, "wildberries", "seller_123")
        claimant = _account(ws_b.id, "wildberries", None, "unverified")
        db.add_all([owned, claimant])
        await db.commit()

        claimant.external_account_id = "seller_123"   # discovery resolves to a taken cabinet
        claimant.identity_status = "verified"
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
    _run(go())


# ── C. workspace resolver ────────────────────────────────────────────────────

def test_resolver_returns_the_independent_workspace_id():
    async def go():
        db = await _orm_session()
        user, ws = await _user_with_workspace(db)

        resolved = await resolve_workspace_id(db, user.id)
        assert resolved == ws.id
        assert resolved != user.id, "resolver returned the user id — both are uuid4 strings"
    _run(go())


def test_resolver_raises_and_creates_nothing_when_workspace_is_missing():
    async def go():
        db = await _orm_session()
        user = User(id=str(uuid.uuid4()), email="nw@b.com", name="NW", hashed_password="x")
        db.add(user)
        await db.commit()

        with pytest.raises(WorkspaceMissing):
            await resolve_workspace_id(db, user.id)

        after = (await db.execute(sa.select(Workspace))).scalars().all()
        assert after == [], "resolver lazily created a workspace — forbidden"
    _run(go())


# ── D. connection route wiring ───────────────────────────────────────────────

def test_new_connection_gets_workspace_and_one_unverified_account():
    async def go():
        db = await _orm_session()
        user, ws = await _user_with_workspace(db)

        await create_connection(_body(), user, db)

        conns = (await db.execute(sa.select(MarketplaceConnection))).scalars().all()
        accounts = (await db.execute(sa.select(MarketplaceAccount))).scalars().all()

        assert len(conns) == 1 and len(accounts) == 1
        assert conns[0].workspace_id == ws.id
        assert conns[0].marketplace_account_id == accounts[0].id
        assert accounts[0].workspace_id == ws.id
        assert accounts[0].marketplace == "wildberries"
        assert accounts[0].external_account_id is None, "F1.1 must not verify an identity"
        assert accounts[0].identity_status == "unverified"
    _run(go())


def test_repeated_post_reuses_the_same_connection_and_the_same_account():
    """The cabinet's identity must survive a reconnect — that is the point of the table."""
    async def go():
        db = await _orm_session()
        user, _ws = await _user_with_workspace(db)

        await create_connection(_body(scope="prices"), user, db)
        first_conn = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        first_account_id = first_conn.marketplace_account_id

        await create_connection(_body(scope="advert", token="rotated"), user, db)

        conns = (await db.execute(sa.select(MarketplaceConnection))).scalars().all()
        accounts = (await db.execute(sa.select(MarketplaceAccount))).scalars().all()
        assert len(conns) == 1 and len(accounts) == 1
        assert conns[0].id == first_conn.id
        assert conns[0].marketplace_account_id == first_account_id

        # credential behaviour is untouched: one row per scope, both stored
        creds = (await db.execute(sa.select(ApiCredential))).scalars().all()
        assert {c.scope for c in creds} == {"prices", "advert"}
        assert all(c.connection_id == first_conn.id for c in creds)
    _run(go())


def test_route_repairs_a_pre_f1_1_connection_without_identity_links():
    """A connection the migration could not backfill is repaired on the next POST."""
    async def go():
        db = await _orm_session()
        user, ws = await _user_with_workspace(db)

        orphan = MarketplaceConnection(
            id=str(uuid.uuid4()), user_id=user.id, marketplace="ozon",
            status="connected", scopes=["prices"], label="old",
        )
        db.add(orphan)
        await db.commit()
        assert orphan.workspace_id is None and orphan.marketplace_account_id is None

        # Client-Id is required for Ozon (CONNECTION-UI); incidental to the repair being tested.
        await create_connection(_body(marketplace="ozon", scope="prices", ozon_client_id="CID"), user, db)

        accounts = (await db.execute(sa.select(MarketplaceAccount))).scalars().all()
        conns = (await db.execute(sa.select(MarketplaceConnection))).scalars().all()
        assert len(conns) == 1 and conns[0].id == orphan.id     # reused, not replaced
        assert len(accounts) == 1
        assert conns[0].workspace_id == ws.id
        assert conns[0].marketplace_account_id == accounts[0].id
        assert accounts[0].external_account_id is None
    _run(go())


def test_ozon_client_id_is_never_copied_into_external_account_id():
    """ozon_client_id is a credential component the user typed — not a verified cabinet id."""
    async def go():
        db = await _orm_session()
        user, _ws = await _user_with_workspace(db)

        await create_connection(
            _body(marketplace="ozon", ozon_client_id="123456"), user, db)

        conn = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        account = (await db.execute(sa.select(MarketplaceAccount))).scalar_one()
        assert conn.ozon_client_id == "123456"        # still stored where it belongs
        assert account.external_account_id is None
    _run(go())


def test_missing_workspace_fails_neutrally_and_writes_nothing():
    async def go():
        db = await _orm_session()
        user = User(id=str(uuid.uuid4()), email="nw@b.com", name="NW", hashed_password="x")
        db.add(user)
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await create_connection(_body(), user, db)
        assert exc.value.status_code == 500
        assert user.id not in str(exc.value.detail)     # no identifiers in the response

        await db.rollback()
        assert (await db.execute(sa.select(MarketplaceConnection))).scalars().all() == []
        assert (await db.execute(sa.select(MarketplaceAccount))).scalars().all() == []
        assert (await db.execute(sa.select(ApiCredential))).scalars().all() == []
    _run(go())


# ── E. legacy migration ──────────────────────────────────────────────────────

def test_legacy_connection_gets_one_unverified_legacy_account(monkeypatch):
    _cfg, sync_url, seeded = _migrated_db(monkeypatch, connections=[
        {"marketplace": "wildberries", "label": "my wb", "ozon_client_id": None},
    ])
    conn = seeded[0]

    accounts = _rows(sync_url,
                     "SELECT id, workspace_id, marketplace, external_account_id, "
                     "identity_status, label FROM marketplace_accounts")
    assert len(accounts) == 1
    acc_id, ws_id, mp, ext, status, label = accounts[0]
    assert ws_id == conn["workspace_id"]
    assert mp == "wildberries"
    assert ext is None, "the migration invented an external id"
    assert status == "unverified_legacy"
    assert label == "my wb"

    links = _rows(sync_url, "SELECT workspace_id, marketplace_account_id "
                            f"FROM marketplace_connections WHERE id = '{conn['connection_id']}'")
    assert links == [(conn["workspace_id"], acc_id)]


def test_two_legacy_connections_backfill_without_collision(monkeypatch):
    """Same marketplace, NULL identities, different owners — and no merge.

    Their real external identities are unknown; merging them because `marketplace` matches
    would fabricate shared ownership of a cabinet nobody has verified.
    """
    _cfg, sync_url, seeded = _migrated_db(monkeypatch, connections=[
        {"marketplace": "wildberries", "label": "a"},
        {"marketplace": "wildberries", "label": "b"},
    ])

    accounts = _rows(sync_url, "SELECT id, workspace_id, external_account_id "
                               "FROM marketplace_accounts")
    assert len(accounts) == 2                                   # one per connection
    assert len({a[0] for a in accounts}) == 2                   # distinct accounts
    assert {a[1] for a in accounts} == {s["workspace_id"] for s in seeded}
    assert all(a[2] is None for a in accounts)

    linked = _rows(sync_url, "SELECT marketplace_account_id FROM marketplace_connections")
    assert len({row[0] for row in linked}) == 2                 # no shared account


def test_orphan_connection_survives_with_null_links(monkeypatch):
    """user_id carries no FK, so a connection may resolve to no workspace at all."""
    _cfg, sync_url, _ = _migrated_db(monkeypatch, connections=[
        {"marketplace": "ozon", "with_workspace": False},
    ])

    assert _current(sync_url) == REV                            # migration did not fail
    rows = _rows(sync_url, "SELECT workspace_id, marketplace_account_id "
                           "FROM marketplace_connections")
    assert rows == [(None, None)]
    assert _rows(sync_url, "SELECT id FROM marketplace_accounts") == []


def test_credentials_still_point_at_their_connection_after_migration(monkeypatch):
    """The batch rebuild of marketplace_connections must not orphan a single token."""
    _cfg, sync_url, seeded = _migrated_db(monkeypatch, connections=[
        {"marketplace": "wildberries"},
        {"marketplace": "ozon"},
    ])

    creds = _rows(sync_url, "SELECT id, connection_id, scope, secret_enc FROM api_credentials")
    assert len(creds) == 2
    by_id = {c[0]: c for c in creds}
    for s in seeded:
        cred = by_id[s["credential_id"]]
        assert cred[1] == s["connection_id"]
        assert cred[2] == "prices"
        assert cred[3] == b"ciphertext", "credential ciphertext was rewritten"


def test_upgrade_downgrade_upgrade_roundtrip(monkeypatch):
    """Downgrade must leave the pre-F1.1 connection and credential data intact."""
    tmp = os.path.join(tempfile.mkdtemp(), "identity_roundtrip.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"

    import db_migrations as dbm
    cfg = dbm._alembic_config()

    command.upgrade(cfg, PRIOR)
    _seed_legacy(sync_url, connections=[{"marketplace": "wildberries", "label": "keep"}])
    conns_before = _rows(sync_url, "SELECT id, user_id, marketplace, label, status, scopes, "
                                   "ozon_client_id FROM marketplace_connections ORDER BY id")
    creds_before = _rows(sync_url, "SELECT * FROM api_credentials ORDER BY id")

    command.upgrade(cfg, "head")
    assert _current(sync_url) == REV
    assert TABLE in _tables(sync_url)
    assert len(_rows(sync_url, "SELECT id FROM marketplace_accounts")) == 1

    command.downgrade(cfg, PRIOR)
    assert _current(sync_url) == PRIOR
    assert TABLE not in _tables(sync_url)
    assert _rows(sync_url, "SELECT id, user_id, marketplace, label, status, scopes, "
                           "ozon_client_id FROM marketplace_connections ORDER BY id") \
        == conns_before
    assert _rows(sync_url, "SELECT * FROM api_credentials ORDER BY id") == creds_before

    command.upgrade(cfg, "head")
    assert _current(sync_url) == REV
    assert len(_rows(sync_url, "SELECT id FROM marketplace_accounts")) == 1   # re-backfilled


# ── F. inertness: F1.1 changes nothing it was not allowed to change ──────────

def test_executor_still_resolves_a_connection_with_null_identity_links():
    """The executor is untouched and must stay blind to the new columns."""
    async def go():
        db = await _orm_session()
        user, _ws = await _user_with_workspace(db)

        conn = MarketplaceConnection(
            id=str(uuid.uuid4()), user_id=user.id, marketplace="wildberries",
            status="connected", scopes=["prices"],
        )
        db.add(conn)
        await db.commit()

        resolved = await executor._resolve_connection(
            db, user_id=user.id, marketplace="wildberries", connection_id=None)
        assert resolved.id == conn.id
        assert resolved.marketplace_account_id is None
    _run(go())


def test_post_refuses_an_unsupported_marketplace():
    """A marketplace becomes connectable only once it can actually be CHECKED.

    This guard was written against Yandex, which F1.1 deliberately did not enable: it had no
    probe, so such a connection could only have been believed, never verified. Yandex was
    later admitted on its own merits (F1.2d — the Partner API exposes a documented read-only
    token-introspection call). The rule never changed; only Yandex's standing did. So it is
    now asserted against a marketplace that still has no adapter.
    """
    async def go():
        db = await _orm_session()
        user, _ws = await _user_with_workspace(db)

        with pytest.raises(HTTPException) as exc:
            await create_connection(_body(marketplace="megamarket"), user, db)
        assert exc.value.status_code == 422

        assert (await db.execute(sa.select(MarketplaceAccount))).scalars().all() == []
    _run(go())


def test_post_still_creates_only_one_connection_per_marketplace():
    async def go():
        db = await _orm_session()
        user, _ws = await _user_with_workspace(db)

        await create_connection(_body(marketplace="wildberries"), user, db)
        await create_connection(_body(marketplace="wildberries"), user, db)
        # Ozon's credential is a pair, so the route requires the Client-Id (CONNECTION-UI). That is
        # incidental here — what this test pins is the one-connection-per-marketplace upsert.
        await create_connection(_body(marketplace="ozon", ozon_client_id="CID"), user, db)

        conns = (await db.execute(sa.select(MarketplaceConnection))).scalars().all()
        assert {c.marketplace for c in conns} == {"wildberries", "ozon"}
        assert len(conns) == 2, "F1.1 must not enable multi-cabinet creation"
    _run(go())


def test_connection_route_performs_no_marketplace_call():
    """Discovery is a later slice: the route must not reach a marketplace client at all.

    Asserted on the import graph rather than on substrings, because `ozon_client_id` — a
    credential field the route legitimately handles — contains the client's name.
    """
    source = Path(__file__).resolve().parents[1] / "routers" / "connections.py"
    imported = {
        line.strip()
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
    }
    for forbidden in ("wb_client", "ozon_client", "base_client", "httpx",
                      "ozon_performance_auth", "campaign_identity"):
        assert not any(forbidden in line for line in imported), \
            f"connections.py imports {forbidden} — F1.1 must perform no marketplace call"
