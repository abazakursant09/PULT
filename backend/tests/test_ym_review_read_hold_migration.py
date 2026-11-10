"""Disposable SQLite schema proof; PostgreSQL ordering is a separate CI gate."""
import ast
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext


BACKEND = Path(__file__).resolve().parents[1]
PARENT = "csr1a2b3c4d01"
REVISION = "ymh1a2b3c4d01"


def test_model_declares_additive_utc_fields():
    from models.marketplace_account import MarketplaceAccount

    columns = MarketplaceAccount.__table__.c
    assert "ym_review_read_not_before" in columns
    assert "ym_review_read_indeterminate" in columns
    assert columns.ym_review_read_not_before.nullable is True
    assert columns.ym_review_read_not_before.type.timezone is True
    assert columns.ym_review_read_indeterminate.nullable is False
    assert columns.ym_review_read_indeterminate.server_default is not None


def test_revision_has_pinned_single_parent():
    declarations = {}
    for path in (BACKEND / "alembic/versions").glob("*.py"):
        values = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    values[target.id] = ast.literal_eval(node.value)
        if "revision" in values:
            assert values["revision"] not in declarations
            declarations[values["revision"]] = values["down_revision"]
    assert REVISION in declarations
    assert declarations[REVISION] == PARENT
    parents = set()
    for value in declarations.values():
        parents.update(value if isinstance(value, tuple) else [value])
    assert set(declarations) - parents == {'acl1a2b3c4d01'}


def config_for():
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


@pytest.fixture(scope="module")
def prior_schema(tmp_path_factory):
    location = tmp_path_factory.mktemp("prior-schema") / "prior.sqlite"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ALEMBIC_DATABASE_URL", "sqlite+aiosqlite:///" + location.as_posix())
        command.upgrade(config_for(), PARENT)
    return location


@pytest.fixture
def migrated(tmp_path, monkeypatch, prior_schema):
    location = tmp_path / "owned-hold-schema.sqlite"
    shutil.copyfile(prior_schema, location)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", "sqlite+aiosqlite:///" + location.as_posix())
    cfg = config_for()
    engine = sa.create_engine("sqlite:///" + location.as_posix())
    try:
        with engine.begin() as conn:
            # Valid synthetic identity graph, no keys or external identifiers from users.
            conn.execute(sa.text("INSERT INTO users (id,email,name,hashed_password) "
                                 "VALUES ('hold-user','hold@example.invalid','synthetic','synthetic')"))
            conn.execute(sa.text("INSERT INTO workspaces (id,owner_user_id,created_at) "
                                 "VALUES ('hold-ws','hold-user',CURRENT_TIMESTAMP)"))
            conn.execute(sa.text("INSERT INTO marketplace_accounts "
                                 "(id,workspace_id,marketplace,external_account_id,identity_status,label) "
                                 "VALUES ('hold-account','hold-ws','yandex','synthetic-business','verified','keep')"))
            conn.execute(sa.text("INSERT INTO marketplace_connections "
                                 "(id,user_id,marketplace,status,scopes,workspace_id,marketplace_account_id) "
                                 "VALUES ('hold-connection','hold-user','yandex','connected',"
                                 "'[]','hold-ws','hold-account')"))
            conn.execute(sa.text("INSERT INTO api_credentials (id,connection_id,scope,secret_enc,meta) "
                                 "VALUES ('hold-credential','hold-connection','feedbacks',:secret,'{}')"),
                         {"secret": b"synthetic-ciphertext-not-a-real-key"})
        with engine.connect() as conn:
            before = {table: [dict(row) for row in conn.execute(
                sa.text('SELECT * FROM "' + table + '"')).mappings()]
                for table in ("users", "workspaces", "marketplace_accounts",
                              "marketplace_connections", "api_credentials")}
            assert "ym_review_read_not_before" not in {
                col["name"] for col in sa.inspect(conn).get_columns("marketplace_accounts")}
        command.upgrade(cfg, REVISION)
        yield cfg, engine, before
    finally:
        engine.dispose()


def assert_preserved(conn, before):
    for table, rows in before.items():
        after = [dict(row) for row in conn.execute(sa.text('SELECT * FROM "' + table + '"')).mappings()]
        assert [{key: row[key] for key in rows[0]} for row in after] == rows


def test_seeded_upgrade_repeat_and_safe_empty_downgrade(migrated):
    cfg, engine, before = migrated
    command.upgrade(cfg, REVISION)
    with engine.connect() as conn:
        assert_preserved(conn, before)
        state = conn.execute(sa.text("SELECT ym_review_read_not_before,ym_review_read_indeterminate "
                                     "FROM marketplace_accounts")).one()
        assert state == (None, 0)
        assert MigrationContext.configure(conn).get_current_revision() == REVISION
    command.downgrade(cfg, PARENT)
    with engine.connect() as conn:
        assert_preserved(conn, before)
        assert "ym_review_read_not_before" not in {
            col["name"] for col in sa.inspect(conn).get_columns("marketplace_accounts")}
    command.upgrade(cfg, REVISION)
    with engine.connect() as conn:
        assert_preserved(conn, before)


@pytest.mark.parametrize("deadline,indeterminate", [
    ("2999-01-01 00:00:00", 0), (None, 1),
    ("2000-01-01 00:00:00", 1), ("not-a-date", 0),
])
def test_downgrade_refuses_without_losing_hold_or_revision(migrated, deadline, indeterminate):
    cfg, engine, before = migrated
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE marketplace_accounts SET ym_review_read_not_before=:deadline, "
                             "ym_review_read_indeterminate=:indeterminate"),
                     {"deadline": deadline, "indeterminate": indeterminate})
    with pytest.raises(RuntimeError, match="hold"):
        command.downgrade(cfg, PARENT)
    with engine.connect() as conn:
        assert MigrationContext.configure(conn).get_current_revision() == REVISION
        assert conn.execute(sa.text("SELECT ym_review_read_not_before,ym_review_read_indeterminate "
                                    "FROM marketplace_accounts")).one() == (deadline, indeterminate)
        assert_preserved(conn, before)


def test_expired_hold_downgrade_is_explicitly_safe(migrated):
    cfg, engine, before = migrated
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE marketplace_accounts "
                             "SET ym_review_read_not_before='2000-01-01 00:00:00'"))
    command.downgrade(cfg, PARENT)
    with engine.connect() as conn:
        assert_preserved(conn, before)
        assert MigrationContext.configure(conn).get_current_revision() == PARENT
