import pytest
import sqlalchemy as sa
from alembic import command
from alembic.runtime.migration import MigrationContext


def test_incomplete_legacy_refuses_stamp_without_changing_data(tmp_path, monkeypatch):
    import db_migrations as dbm

    path = tmp_path / "legacy.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)")
            conn.exec_driver_sql("INSERT INTO users VALUES ('synthetic-marker')")
        for _ in range(2):
            with pytest.raises(RuntimeError, match="refusing Alembic stamp"):
                dbm._migrate_sync()
            with engine.connect() as conn:
                assert MigrationContext.configure(conn).get_current_revision() is None
                assert conn.exec_driver_sql("SELECT id FROM users").scalars().all() == [
                    "synthetic-marker"
                ]
                assert sa.inspect(conn).get_table_names() == ["users"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("mutation", [
    "DROP TABLE consent_records",
    "ALTER TABLE users ADD COLUMN unexpected_legacy_column TEXT",
    "CREATE TABLE unexpected_legacy_table (id INTEGER PRIMARY KEY)",
])
def test_legacy_drift_refuses_stamp(tmp_path, monkeypatch, mutation):
    import db_migrations as dbm

    path = tmp_path / "drift.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(dbm._alembic_config(), "head")
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE alembic_version")
            conn.exec_driver_sql(mutation)
        with pytest.raises(RuntimeError, match="refusing Alembic stamp"):
            dbm._migrate_sync()
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() is None
            assert "alembic_version" not in sa.inspect(conn).get_table_names()
    finally:
        engine.dispose()


def test_compatible_unversioned_schema_can_stamp_and_restart(tmp_path, monkeypatch):
    import db_migrations as dbm

    path = tmp_path / "compatible.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    cfg = dbm._alembic_config()
    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            revision = MigrationContext.configure(conn).get_current_revision()
            conn.exec_driver_sql("DROP TABLE alembic_version")
        dbm._migrate_sync()
        dbm._migrate_sync()
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() == revision
    finally:
        engine.dispose()


@pytest.mark.parametrize("drift", ["type", "nullable", "index"])
def test_legacy_column_and_index_drift_refuses_stamp(tmp_path, monkeypatch, drift):
    import importlib
    import pkgutil

    import db_migrations as dbm
    import models
    from database import Base

    for module in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"{models.__name__}.{module.name}")
    metadata = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    users = metadata.tables["users"]
    if drift == "type":
        users.c.email.type = sa.Integer()
    elif drift == "nullable":
        users.c.email.nullable = not users.c.email.nullable
    else:
        assert users.indexes, "test requires an existing model index"
        users.indexes.pop()
    path = tmp_path / "column-drift.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        metadata.create_all(engine)
        with pytest.raises(RuntimeError, match="refusing Alembic stamp"):
            dbm._migrate_sync()
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() is None
            assert "alembic_version" not in sa.inspect(conn).get_table_names()
    finally:
        engine.dispose()
