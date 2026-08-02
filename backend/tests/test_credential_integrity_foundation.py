"""
F1.2a — Credential integrity and honest verification state.

Two independent axes on a connection, and the whole point is that they do not collapse:

  * `status` — the EXECUTION GATE. `connected` is what the executor, the measurement
    bridge and the WB review sync require. This slice does not touch it, and the tests
    below prove all three still work. Flipping it would have taken every seller's
    pricing, advertising and review publishing offline.
  * `verification_status` — did a marketplace ever CONFIRM these credentials? Storing
    ciphertext is not verifying it: POST /connections encrypts whatever string it is
    handed and calls nothing. So the honest answer is always `unverified` here, and any
    credential write resets it.

Plus the integrity `api_credentials` always assumed but never enforced: one credential per
(connection, scope), and no credential without a connection. The migration DETECTS
violations and refuses to run — it never repairs them, because repairing means choosing
which encrypted secret to destroy, and it cannot decrypt them to find out.
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
from models.api_credential import ApiCredential
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.user import User
from models.workspace import Workspace

from routers.connections import create_connection
from schemas.marketplace import ConnectionCreate, ConnectionOut
from services.marketplace import credential_vault, executor
from services import execution_measurement_bridge

REV = "lch1a2b3c4d01"
PRIOR = "mpa1a2b3c4d01"
CIPHERTEXT = b"gAAAAA-fake-fernet-ciphertext-bytes"


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


def _rows(sync_url, stmt):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return c.execute(sa.text(stmt)).fetchall()
    finally:
        eng.dispose()


def _seed(sync_url, *, connections):
    """Seed users / workspaces / connections / credentials at PRIOR (before F1.2a).

    Each spec: {status, marketplace, creds: [scope, ...], extra_dupe_scope, orphan_cred}
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
                        "plan, is_verified, was_referrer, "
                        "was_referred, is_restored) "
                        "VALUES (:id, :email, :name, 'x', :created, 'master', 1, 0, 0, 0)"
                    ),
                    {"id": user_id, "email": f"c{i}@b.com", "name": f"C{i}",
                     "created": datetime.utcnow()},
                )
                c.execute(
                    sa.text("INSERT INTO workspaces (id, owner_user_id, created_at) "
                            "VALUES (:id, :owner, :created)"),
                    {"id": str(uuid.uuid4()), "owner": user_id, "created": datetime.utcnow()},
                )

                conn_id = str(uuid.uuid4())
                c.execute(
                    sa.text(
                        "INSERT INTO marketplace_connections "
                        "(id, user_id, marketplace, status, scopes, created_at, updated_at) "
                        "VALUES (:id, :user_id, :mp, :status, '[]', :created, :created)"
                    ),
                    {"id": conn_id, "user_id": user_id,
                     "mp": spec.get("marketplace", "wildberries"),
                     "status": spec.get("status", "connected"), "created": datetime.utcnow()},
                )

                cred_ids = []
                for scope in spec.get("creds", ["prices"]):
                    cid = str(uuid.uuid4())
                    c.execute(
                        sa.text(
                            "INSERT INTO api_credentials "
                            "(id, connection_id, scope, secret_enc, meta, created_at, updated_at) "
                            "VALUES (:id, :conn, :scope, :blob, '{}', :created, :created)"
                        ),
                        {"id": cid, "conn": conn_id, "scope": scope, "blob": CIPHERTEXT,
                         "created": datetime.utcnow()},
                    )
                    cred_ids.append(cid)

                # a second row for the SAME (connection, scope) — the duplicate preflight target
                if spec.get("extra_dupe_scope"):
                    c.execute(
                        sa.text(
                            "INSERT INTO api_credentials "
                            "(id, connection_id, scope, secret_enc, meta, created_at, updated_at) "
                            "VALUES (:id, :conn, :scope, :blob, '{}', :created, :created)"
                        ),
                        {"id": str(uuid.uuid4()), "conn": conn_id,
                         "scope": spec["extra_dupe_scope"], "blob": b"other-ciphertext",
                         "created": datetime.utcnow()},
                    )

                # a credential pointing at a connection that does not exist — orphan preflight
                if spec.get("orphan_cred"):
                    c.execute(
                        sa.text(
                            "INSERT INTO api_credentials "
                            "(id, connection_id, scope, secret_enc, meta, created_at, updated_at) "
                            "VALUES (:id, :conn, 'prices', :blob, '{}', :created, :created)"
                        ),
                        {"id": str(uuid.uuid4()), "conn": str(uuid.uuid4()),
                         "blob": CIPHERTEXT, "created": datetime.utcnow()},
                    )

                seeded.append({"user_id": user_id, "connection_id": conn_id,
                               "credential_ids": cred_ids})
    finally:
        eng.dispose()
    return seeded


def _migrated_db(monkeypatch, *, connections=(), to="head"):
    tmp = os.path.join(tempfile.mkdtemp(), "credint_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"

    import db_migrations as dbm
    cfg = dbm._alembic_config()

    command.upgrade(cfg, PRIOR)
    seeded = _seed(sync_url, connections=list(connections)) if connections else []
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
    db.add(Workspace(id=str(uuid.uuid4()), owner_user_id=user.id, created_at=datetime.utcnow()))
    await db.commit()
    return user


def _body(**kw):
    base = {"marketplace": "wildberries", "token": "t0ken", "scope": "prices"}
    base.update(kw)
    return ConnectionCreate(**base)


# ── A. schema ────────────────────────────────────────────────────────────────

def test_alembic_single_head_is_credential_integrity_revision(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "head_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    import db_migrations as dbm
    heads = ScriptDirectory.from_config(dbm._alembic_config()).get_heads()
    assert heads == [REV], f"expected single head {REV}, got {heads}"


def test_verification_columns_shape(monkeypatch):
    _cfg, sync_url, _ = _migrated_db(monkeypatch)
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            cols = {col["name"]: col
                    for col in sa.inspect(c).get_columns("marketplace_connections")}
    finally:
        eng.dispose()

    assert cols["verification_status"]["nullable"] is False
    assert "unverified" in str(cols["verification_status"]["default"])
    assert cols["verified_at"]["nullable"] is True
    # the execution gate is still there, untouched
    assert cols["status"]["nullable"] is False


def test_credential_fk_and_unique_constraint(monkeypatch):
    _cfg, sync_url, _ = _migrated_db(monkeypatch)
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            insp = sa.inspect(c)
            fks = {(fk["referred_table"], tuple(fk["constrained_columns"]))
                   for fk in insp.get_foreign_keys("api_credentials")}
            uniques = {u["name"]: u["column_names"]
                       for u in insp.get_unique_constraints("api_credentials")}
    finally:
        eng.dispose()

    assert ("marketplace_connections", ("connection_id",)) in fks
    assert uniques["uq_apicred_conn_scope"] == ["connection_id", "scope"]


# ── B. two-axis state ────────────────────────────────────────────────────────

def test_legacy_connected_row_keeps_status_and_becomes_unverified(monkeypatch):
    """The whole point of the slice: honesty is added WITHOUT closing the execution gate."""
    _cfg, sync_url, _ = _migrated_db(monkeypatch, connections=[{"status": "connected"}])

    rows = _rows(sync_url,
                 "SELECT status, verification_status, verified_at FROM marketplace_connections")
    assert rows == [("connected", "unverified", None)]


def test_migration_preserves_every_status_value(monkeypatch):
    """invalid / revoked are failure states — the backfill must not launder them."""
    _cfg, sync_url, _ = _migrated_db(monkeypatch, connections=[
        {"status": "connected"}, {"status": "invalid"}, {"status": "revoked"},
    ])

    rows = _rows(sync_url, "SELECT status, verification_status FROM marketplace_connections")
    assert sorted(r[0] for r in rows) == ["connected", "invalid", "revoked"]
    assert {r[1] for r in rows} == {"unverified"}      # verification history is absent for all


def test_new_connection_is_connected_but_unverified():
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)

        out = await create_connection(_body(), user, db)

        conn = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        assert conn.status == "connected"                  # execution contract preserved
        assert conn.verification_status == "unverified"    # no marketplace was asked
        assert conn.verified_at is None

        payload = ConnectionOut.model_validate(out)
        assert payload.verification_status == "unverified"
        assert payload.verified_at is None
        assert payload.status == "connected"
        assert not hasattr(payload, "token")
    _run(go())


def test_credential_replacement_resets_verification():
    """A secret the marketplace has never seen must not inherit an earlier verification."""
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)

        await create_connection(_body(), user, db)
        conn = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()

        # simulate a future verifier having succeeded
        conn.verification_status = "verified"
        conn.verified_at = datetime.utcnow()
        await db.commit()

        await create_connection(_body(token="rotated"), user, db)

        await db.refresh(conn)
        assert conn.verification_status == "unverified"
        assert conn.verified_at is None
        assert conn.status == "connected"     # still usable — only the CLAIM was reset
    _run(go())


def test_adding_a_new_scope_also_resets_verification():
    """verification_status is connection-level, so a new scope's unchecked secret resets it."""
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)

        await create_connection(_body(scope="prices"), user, db)
        conn = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        conn.verification_status = "verified"
        conn.verified_at = datetime.utcnow()
        await db.commit()

        await create_connection(_body(scope="advert"), user, db)

        await db.refresh(conn)
        assert conn.verification_status == "unverified"
        assert conn.verified_at is None
        assert sorted(conn.scopes) == ["advert", "prices"]     # scope merge still correct
    _run(go())


def test_f1_2a_never_writes_verified():
    """No code path in this slice may claim verification — there is no verifier."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel in ("routers/connections.py",
                "alembic/versions/cri1a2b3c4d01_credential_integrity.py"):
        text = (root / rel).read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]          # ignore explanatory comments
            assert 'verification_status = "verified"' not in code.replace("'", '"')
            assert "verified_at = datetime" not in code


# ── C. credential integrity ──────────────────────────────────────────────────

def _conn_with_cred(db, user, *, marketplace="wildberries"):
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=user.id, marketplace=marketplace,
        status="connected", scopes=["prices"],
    )
    db.add(conn)
    return conn


def test_duplicate_connection_scope_is_rejected_by_the_database():
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)
        conn = _conn_with_cred(db, user)
        await db.commit()

        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id,
                             scope="prices", secret_enc=CIPHERTEXT))
        await db.commit()

        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id,
                             scope="prices", secret_enc=b"second"))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
    _run(go())


def test_different_scopes_on_one_connection_coexist():
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)
        conn = _conn_with_cred(db, user)
        await db.commit()

        for scope in ("prices", "advert", "feedbacks"):
            db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id,
                                 scope=scope, secret_enc=CIPHERTEXT))
        await db.commit()

        creds = (await db.execute(sa.select(ApiCredential))).scalars().all()
        assert len(creds) == 3
    _run(go())


def test_same_scope_on_different_connections_coexists():
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)
        a = _conn_with_cred(db, user, marketplace="wildberries")
        b = _conn_with_cred(db, user, marketplace="ozon")
        await db.commit()

        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=a.id,
                             scope="prices", secret_enc=CIPHERTEXT))
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=b.id,
                             scope="prices", secret_enc=CIPHERTEXT))
        await db.commit()

        assert len((await db.execute(sa.select(ApiCredential))).scalars().all()) == 2
    _run(go())


def test_orphan_credential_is_rejected_by_the_foreign_key(monkeypatch):
    """A credential must not outlive its connection and stay decryptable forever."""
    _cfg, sync_url, _ = _migrated_db(monkeypatch)

    eng = sa.create_engine(sync_url)
    try:
        with eng.begin() as c:
            c.execute(sa.text("PRAGMA foreign_keys=ON"))   # SQLite enforces FKs only on request
            with pytest.raises(IntegrityError):
                c.execute(
                    sa.text("INSERT INTO api_credentials "
                            "(id, connection_id, scope, secret_enc, meta, created_at) "
                            "VALUES (:id, :conn, 'prices', :blob, '{}', :created)"),
                    {"id": str(uuid.uuid4()), "conn": str(uuid.uuid4()),
                     "blob": CIPHERTEXT, "created": datetime.utcnow()},
                )
    finally:
        eng.dispose()


def test_ciphertext_survives_migration_byte_for_byte(monkeypatch):
    _cfg, sync_url, seeded = _migrated_db(monkeypatch, connections=[
        {"creds": ["prices", "advert"]},
    ])

    creds = _rows(sync_url, "SELECT id, connection_id, scope, secret_enc FROM api_credentials")
    assert len(creds) == 2
    for cred_id, conn_id, _scope, blob in creds:
        assert blob == CIPHERTEXT, "migration rewrote credential ciphertext"
        assert conn_id == seeded[0]["connection_id"]       # links preserved
        assert cred_id in seeded[0]["credential_ids"]      # ids preserved


def test_migration_never_decrypts(monkeypatch):
    """The migration must not touch the vault: it has no business reading a secret."""
    calls = []
    monkeypatch.setattr(credential_vault, "decrypt",
                        lambda blob: calls.append(blob) or "PLAINTEXT")

    _cfg, sync_url, _ = _migrated_db(monkeypatch, connections=[{"creds": ["prices"]}])

    assert calls == [], "migration called credential_vault.decrypt"
    assert _current(sync_url) == REV


# ── D. preflight: detect, never repair ───────────────────────────────────────

def test_duplicate_preflight_fails_loudly_and_deletes_nothing(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "dupe.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"
    import db_migrations as dbm
    cfg = dbm._alembic_config()

    command.upgrade(cfg, PRIOR)
    _seed(sync_url, connections=[{"creds": ["prices"], "extra_dupe_scope": "prices"}])
    before = _rows(sync_url, "SELECT id, secret_enc FROM api_credentials ORDER BY id")
    assert len(before) == 2

    with pytest.raises(Exception) as exc:
        command.upgrade(cfg, "head")
    assert "duplicate" in str(exc.value).lower()

    # nothing was destroyed and the schema did not move
    assert _rows(sync_url, "SELECT id, secret_enc FROM api_credentials ORDER BY id") == before
    assert _current(sync_url) == PRIOR


def test_orphan_preflight_fails_loudly_and_deletes_nothing(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "orphan.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"
    import db_migrations as dbm
    cfg = dbm._alembic_config()

    command.upgrade(cfg, PRIOR)
    _seed(sync_url, connections=[{"creds": ["prices"], "orphan_cred": True}])
    before = _rows(sync_url, "SELECT id, secret_enc FROM api_credentials ORDER BY id")
    assert len(before) == 2

    with pytest.raises(Exception) as exc:
        command.upgrade(cfg, "head")
    assert "exist" in str(exc.value).lower()

    assert _rows(sync_url, "SELECT id, secret_enc FROM api_credentials ORDER BY id") == before
    assert _current(sync_url) == PRIOR


# ── E. EXECUTION SAFETY — the reason `status` was left alone ─────────────────

def test_executor_still_accepts_a_connected_but_unverified_connection():
    """The gate is `status`, not `verification_status`. Flipping it would have killed
    every marketplace write in the product."""
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)

        conn = MarketplaceConnection(
            id=str(uuid.uuid4()), user_id=user.id, marketplace="wildberries",
            status="connected", scopes=["prices"],
        )
        db.add(conn)
        await db.commit()
        assert conn.verification_status == "unverified"

        resolved = await executor._resolve_connection(
            db, user_id=user.id, marketplace="wildberries", connection_id=None)
        assert resolved.id == conn.id
    _run(go())


def test_executor_resolves_a_token_for_an_unverified_connection():
    """Pricing and advertising both reach the marketplace through this exact path."""
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)

        await create_connection(_body(scope="prices"), user, db)
        conn = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        assert conn.verification_status == "unverified"

        token = await executor._resolve_token(db, conn.id, "prices")
        assert token == "t0ken"        # decrypted through the untouched vault
    _run(go())


def test_measurement_bridge_still_resolves_a_token():
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)
        await create_connection(_body(scope="prices"), user, db)

        token = await execution_measurement_bridge._resolve_token(
            db, user.id, "wildberries", "prices")
        assert token == "t0ken"
    _run(go())


def test_reviews_connection_lookup_still_finds_a_connected_cabinet():
    """routers/reviews.py filters on status == "connected" in SQL — prove it still matches."""
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)
        await create_connection(_body(marketplace="wildberries", scope="feedbacks"), user, db)

        found = (await db.execute(
            sa.select(MarketplaceConnection).where(
                MarketplaceConnection.user_id == user.id,
                MarketplaceConnection.marketplace == "wildberries",
                MarketplaceConnection.status == "connected",
            )
        )).scalars().first()
        assert found is not None, "the WB review sync would now 409 for every seller"
        assert found.verification_status == "unverified"
    _run(go())


# ── F. F1.1 regression + inertness ───────────────────────────────────────────

def test_repeated_post_still_reuses_connection_account_and_updates_credential():
    async def go():
        db = await _orm_session()
        user = await _user_with_workspace(db)

        await create_connection(_body(scope="prices"), user, db)
        first = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        first_account = first.marketplace_account_id

        await create_connection(_body(scope="prices", token="rotated"), user, db)

        conns = (await db.execute(sa.select(MarketplaceConnection))).scalars().all()
        accounts = (await db.execute(sa.select(MarketplaceAccount))).scalars().all()
        creds = (await db.execute(sa.select(ApiCredential))).scalars().all()

        assert len(conns) == 1 and conns[0].id == first.id
        assert len(accounts) == 1 and conns[0].marketplace_account_id == first_account
        assert len(creds) == 1, "credential replacement duplicated a row"
        assert credential_vault.decrypt(creds[0].secret_enc) == "rotated"

        assert accounts[0].external_account_id is None
        assert accounts[0].identity_status != "verified"
    _run(go())


def test_no_redis_and_no_advisory_runtime_registration():
    """F1.2a must not reach for a queue it does not have, nor for the diagnosis contour."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]

    reqs = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "redis" not in reqs and "aioredis" not in reqs

    for rel in ("routers/connections.py", "models/api_credential.py",
                "models/marketplace_connection.py",
                "alembic/versions/cri1a2b3c4d01_credential_integrity.py"):
        text = (root / rel).read_text(encoding="utf-8")
        imports = [ln for ln in text.splitlines() if ln.startswith(("import ", "from "))]
        for forbidden in ("redis", "advisory_runtime", "ProducerSpec", "AdvisoryRun",
                          "wb_client", "httpx"):
            assert not any(forbidden in ln for ln in imports), \
                f"{rel} imports {forbidden} — outside the F1.2a boundary"
