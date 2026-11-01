import pytest
import sqlalchemy as sa
from alembic import command
from alembic.runtime.migration import MigrationContext


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE users DROP COLUMN telegram_chat_id",
    "DROP TABLE consent_records",
    "ALTER TABLE users ADD COLUMN unexpected_column TEXT",
])
def test_head_marker_does_not_hide_schema_drift(tmp_path, monkeypatch, mutation):
    import db_migrations as dbm

    path = tmp_path / "stamped.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(dbm._alembic_config(), "head")
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO users (id,email,name,hashed_password,is_verified) VALUES ('synthetic-marker','audit@example.com','Synthetic','not-a-login-hash',1)")
            conn.exec_driver_sql(mutation)
            revision = MigrationContext.configure(conn).get_current_revision()
        for _ in range(2):
            with pytest.raises(RuntimeError, match="refusing startup"):
                dbm._migrate_sync()
            with engine.connect() as conn:
                assert MigrationContext.configure(conn).get_current_revision() == revision
                assert conn.exec_driver_sql("SELECT id FROM users").scalars().all() == ["synthetic-marker"]
    finally:
        engine.dispose()


def test_healthy_head_still_restarts(tmp_path, monkeypatch):
    import db_migrations as dbm

    path = tmp_path / "healthy.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(dbm._alembic_config(), "head")
    dbm._migrate_sync()
    dbm._migrate_sync()
