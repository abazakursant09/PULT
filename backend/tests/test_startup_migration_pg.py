"""PG16 startup proof, confined to uniquely named disposable local databases."""
import asyncio
import importlib
import os
import pkgutil
import re
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url


PRIOR = "rob1a2b3c4d01"


def _safe_admin_url(raw):
    try:
        url = make_url(raw)
        allowed = (
            url.drivername == "postgresql+psycopg2"
            and url.host in {"localhost", "127.0.0.1"}
            and url.port == 5432
            and url.database == "pult_explain"
            and url.username == "postgres"
            and not url.query
        )
    except Exception:
        allowed = False
    if not allowed:
        raise ValueError("Refusing unsafe startup-test database configuration") from None
    return url


def _quoted_database(name):
    if not re.fullmatch(r"pult_startup_[0-9a-f]{32}", name):
        raise ValueError("Refusing non-owned database identifier")
    return f'"{name}"'


@pytest.mark.parametrize("change", [
    {"drivername": "postgresql+asyncpg"}, {"host": "example.com"},
    {"host": "::1"}, {"port": 5433}, {"database": "postgres"},
    {"username": "other"}, {"query": {"host": "example.com"}},
    {"query": {"options": "-c search_path=other"}},
])
def test_startup_url_guard_rejects_unsafe_targets(change):
    url = make_url("postgresql+psycopg2://postgres:synthetic@localhost:5432/pult_explain")
    with pytest.raises(ValueError, match="Refusing unsafe"):
        _safe_admin_url(url.set(**change).render_as_string(hide_password=False))


@pytest.mark.parametrize("raw", ["not a URL", "postgresql+psycopg2://postgres@localhost/pult_explain"])
def test_startup_url_guard_rejects_malformed_or_implicit_port(raw):
    with pytest.raises(ValueError, match="Refusing unsafe"):
        _safe_admin_url(raw)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_startup_url_guard_accepts_only_explicit_local_contract(host):
    url = _safe_admin_url(f"postgresql+psycopg2://postgres:synthetic@{host}:5432/pult_explain")
    assert url.host == host


@pytest.mark.parametrize("name", ["pult_explain", "public", 'pult_startup_x"; DROP DATABASE postgres;--'])
def test_startup_database_identifier_guard(name):
    with pytest.raises(ValueError, match="non-owned"):
        _quoted_database(name)


@pytest.fixture
def pg_startup(monkeypatch):
    raw = os.environ.get("PULT_TEST_PG_URL")
    if not raw:
        pytest.skip("PULT_TEST_PG_URL absent; real local PG16 required")
    # Validation happens before importing the runner or opening ANY connection.
    admin_url = _safe_admin_url(raw)
    name = "pult_startup_" + uuid.uuid4().hex
    identifier = _quoted_database(name)
    sync_url = admin_url.set(database=name)
    async_url = sync_url.set(drivername="postgresql+asyncpg")
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool)
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            assert int(connection.scalar(sa.text("SHOW server_version_num"))) // 10000 == 16
            connection.exec_driver_sql(f"CREATE DATABASE {identifier}")
            created = True
        engine = sa.create_engine(sync_url, poolclass=sa.pool.NullPool)
        monkeypatch.setenv("ALEMBIC_DATABASE_URL", async_url.render_as_string(hide_password=False))
        import db_migrations as dbm
        monkeypatch.setattr(dbm.settings, "auto_migrate", True)
        monkeypatch.setattr(dbm.settings, "database_url", async_url.render_as_string(hide_password=False))
        yield engine, dbm
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if created:
                # Never FORCE, terminate sessions, or reset a shared schema.
                with admin.connect() as connection:
                    connection.exec_driver_sql(f"DROP DATABASE {identifier}")
        finally:
            admin.dispose()


def _boot(dbm):
    asyncio.run(dbm.run_migrations())


def _revision(engine):
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _schema(engine):
    inspector = sa.inspect(engine)
    return {
        table: [(c["name"], str(c["type"]), c["nullable"], c.get("default"))
                for c in inspector.get_columns(table)]
        for table in sorted(inspector.get_table_names())
    }


def _seed(engine):
    users = sa.Table("users", sa.MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(users.insert().values(
            id=str(uuid.uuid4()), email="startup-synthetic@example.invalid",
            name="Synthetic startup user", hashed_password="synthetic-not-a-password",
            plan="master", is_verified=True, token_version=7,
        ))


def _rows(engine):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(sa.text("SELECT * FROM users ORDER BY id")).mappings()]


def _prove_repeat(engine, dbm):
    head = ScriptDirectory.from_config(dbm._alembic_config()).get_current_head()
    assert _revision(engine) == head
    before_schema, before_rows = _schema(engine), _rows(engine)
    _boot(dbm)
    assert _revision(engine) == head
    assert _schema(engine) == before_schema
    assert _rows(engine) == before_rows


def test_pg_startup_fresh_and_repeat(pg_startup):
    engine, dbm = pg_startup
    assert _schema(engine) == {}
    _boot(dbm)
    _seed(engine)
    _prove_repeat(engine, dbm)


def test_pg_startup_prior_upgrade_preserves_user_and_repeat(pg_startup):
    engine, dbm = pg_startup
    command.upgrade(dbm._alembic_config(), PRIOR)
    assert _revision(engine) == PRIOR
    assert PRIOR != ScriptDirectory.from_config(dbm._alembic_config()).get_current_head()
    _seed(engine)
    before = _rows(engine)
    _boot(dbm)
    after = _rows(engine)
    assert len(after) == len(before)
    lifecycle_fields = {'closure_policy_version', 'closure_started_at',
                        'closure_restore_requested_at', 'closure_purge_started_at'}
    for old, new in zip(before, after):
        assert {key: new[key] for key in old} == old
        assert set(new) - set(old) == lifecycle_fields
        assert all(new[key] is None for key in lifecycle_fields)
    _prove_repeat(engine, dbm)


def _legacy(engine):
    import models
    from database import Base
    for module in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"{models.__name__}.{module.name}")
    Base.metadata.create_all(engine)
    assert "alembic_version" not in _schema(engine)
    _seed(engine)


def test_pg_startup_compatible_legacy_stamp_preserves_user_and_repeat(pg_startup):
    engine, dbm = pg_startup
    _legacy(engine)
    before_schema, before_rows = _schema(engine), _rows(engine)
    _boot(dbm)
    after = _schema(engine)
    assert "alembic_version" in after
    del after["alembic_version"]
    assert after == before_schema
    assert _rows(engine) == before_rows
    _prove_repeat(engine, dbm)


def test_pg_startup_drift_refused_without_stamp_or_data_change(pg_startup):
    engine, dbm = pg_startup
    _legacy(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN startup_synthetic_drift INTEGER")
    before_schema, before_rows = _schema(engine), _rows(engine)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="incompatible; refusing Alembic stamp"):
            _boot(dbm)
        assert "alembic_version" not in _schema(engine)
        assert _schema(engine) == before_schema
        assert _rows(engine) == before_rows


def test_pg_head_marker_does_not_hide_missing_column(pg_startup):
    engine, dbm = pg_startup
    _boot(dbm)
    _seed(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE users DROP COLUMN telegram_chat_id")
    before_schema, before_rows = _schema(engine), _rows(engine)
    revision = _revision(engine)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="refusing startup"):
            _boot(dbm)
        assert _revision(engine) == revision
        assert _schema(engine) == before_schema
        assert _rows(engine) == before_rows
