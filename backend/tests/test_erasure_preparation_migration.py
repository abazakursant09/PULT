"""Disposable SQLite DRAFT expansion/guarded contraction; native PG is separate.

Run the real revision with Alembic Operations, without the application env or
the full historical schema. Models use an isolated Base, never a configured DB.
"""
from datetime import datetime
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
PARENT = "acl1a2b3c4d01"
REV = "ejp1a2b3c4d01"
TABLE = "erasure_preparations"
USER_IDS = ("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
DRAFT_ID = "11111111-1111-4111-8111-111111111111"
SECOND_ID = "22222222-2222-4222-8222-222222222222"
CHECKS = {
    "ck_erasure_preparations_draft_only": "state = 'DRAFT'",
    "ck_erasure_preparations_id_length": "length(id) = 36",
}


def config():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def source_module(path, name):
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def migration():
    return source_module(ROOT / "alembic" / "versions" / "ejp1a2b3c4d01_erasure_preparation.py", "isolated_ejp_revision")


@pytest.fixture(scope="module")
def models():
    database = ModuleType("database")
    database.Base = declarative_base()
    # Load the actual model files, not models/__init__ or configured database.py.
    with patch.dict(sys.modules, database=database):
        user = source_module(ROOT / "models" / "user.py", "isolated_ejp_user")
        draft = source_module(ROOT / "models" / "erasure_preparation.py", "isolated_ejp_model")
    return SimpleNamespace(user=user.User.__table__, draft=draft.ErasurePreparation.__table__)


def old_users():
    rows = []
    for index, user_id in enumerate(USER_IDS):
        rows.append(dict(
            id=user_id, email=f"synthetic-{index}@example.invalid", name=f"Synthetic legacy user {index}",
            hashed_password=f"synthetic-not-a-password-{index}", created_at=datetime(2024, 1, 2, 3, 4, 5 + index),
            plan="profi" if index else "master", is_verified=bool(index),
            verification_token=f"synthetic-verification-{index}", reset_token=f"synthetic-reset-{index}",
            reset_token_expires=datetime(2026, 11, 22, 1, 2, 3), token_version=7 + index,
            telegram_chat_id=f"synthetic-chat-{index}", referral_code=f"synthetic-ref-{index}",
            referred_by_id=USER_IDS[0] if index else None, deleted_at=datetime(2026, 10, 1, 1, 2, 3),
            closure_policy_version="normal-close-30d-v1" if index else None,
            closure_started_at=datetime(2026, 10, 1, 1, 2, 3) if index else None,
            closure_restore_requested_at=datetime(2026, 10, 2, 1, 2, 3) if index else None,
            closure_purge_started_at=datetime(2026, 11, 2, 1, 2, 3) if index else None,
            was_referrer=bool(index), was_referred=not bool(index), is_restored=bool(index),
            registered_ip=f"192.0.2.{index + 1}", subscription_end_date=datetime(2027, 1, 2, 3, 4, 5),
        ))
    return rows


def snapshot(engine, table):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(sa.select(table).order_by(table.c.id)).mappings()]


def user_schema(engine):
    inspector = sa.inspect(engine)
    return [(column["name"], str(column["type"]), column["nullable"], column["default"], column["primary_key"])
            for column in inspector.get_columns("users")]


def run_revision(engine, function):
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            function()


@pytest.fixture
def prior(models):
    engine = sa.create_engine("sqlite://")

    @sa.event.listens_for(engine, "connect")
    def foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    users = models.user.to_metadata(sa.MetaData())
    users.create(engine)
    rows = old_users()
    assert all(set(row) == set(users.c.keys()) for row in rows), "Seed every User column explicitly"
    with engine.begin() as conn:
        conn.execute(users.insert(), rows)
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    state = SimpleNamespace(engine=engine, users=users, before=snapshot(engine, users), schema=user_schema(engine))
    try:
        yield state
    finally:
        engine.dispose()


@pytest.fixture
def migrated(prior, migration):
    run_revision(prior.engine, migration.upgrade)
    prior.queue = sa.Table(TABLE, sa.MetaData(), autoload_with=prior.engine)
    return prior


def draft_values(**changes):
    # Reflected table inserts have no model-side defaults: omission tests SQL DRAFT.
    values = dict(id=DRAFT_ID, user_id=USER_IDS[0], closure_policy_version="normal-close-30d-v1",
                  closure_started_at=datetime(2026, 10, 1, 1, 2, 3), prepared_at=datetime(2026, 11, 21, 1, 2, 3))
    values.update(changes)
    return values


def sql_default(value):
    if value is None:
        return None
    return str(value).strip().strip("()").strip("'")


def test_single_actual_alembic_head_and_pinned_parent(migration):
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == [REV]
    assert script.get_revision(REV).down_revision == PARENT
    assert script.get_revision(PARENT).down_revision == "ymh1a2b3c4d01"
    assert (migration.revision, migration.down_revision) == (REV, PARENT)
    assert migration.branch_labels is None and migration.depends_on is None


def test_upgrade_preserves_every_user_field_and_never_backfills_queue(migrated):
    assert snapshot(migrated.engine, migrated.users) == migrated.before
    assert user_schema(migrated.engine) == migrated.schema
    assert snapshot(migrated.engine, migrated.queue) == []
    assert migrated.before[0]["deleted_at"] is not None
    assert migrated.before[0]["closure_policy_version"] is None
    assert migrated.before[1]["closure_policy_version"] == "normal-close-30d-v1"


def test_schema_matches_model_columns_defaults_keys_and_closed_constraints(migrated, models):
    inspector = sa.inspect(migrated.engine)
    columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
    assert set(columns) == set(models.draft.c.keys()) == {
        "id", "user_id", "closure_policy_version", "closure_started_at", "prepared_at", "state"}
    for name, model in models.draft.c.items():
        column = columns[name]
        assert str(column["type"]) == model.type.compile(dialect=migrated.engine.dialect)
        assert column["nullable"] is model.nullable is False
        expected_default = model.server_default.arg if model.server_default is not None else None
        assert sql_default(column["default"]) == sql_default(expected_default)
    assert sql_default(columns["state"]["default"]) == "DRAFT"
    assert models.draft.c.state.default.arg == "DRAFT"
    assert models.draft.c.closure_started_at.type.timezone is True
    assert models.draft.c.prepared_at.type.timezone is True
    assert inspector.get_pk_constraint(TABLE)["constrained_columns"] == ["id"]
    checks = {item["name"]: " ".join(item["sqltext"].split()) for item in inspector.get_check_constraints(TABLE)}
    model_checks = {item.name: " ".join(str(item.sqltext).split()) for item in models.draft.constraints if isinstance(item, sa.CheckConstraint)}
    assert checks == model_checks == CHECKS
    unique = {(item["name"], tuple(item["column_names"])) for item in inspector.get_unique_constraints(TABLE)}
    model_unique = {(item.name, tuple(column.name for column in item.columns)) for item in models.draft.constraints if isinstance(item, sa.UniqueConstraint)}
    assert unique == model_unique == {("uq_erasure_preparations_user", ("user_id",))}
    foreign = inspector.get_foreign_keys(TABLE)
    assert len(foreign) == 1
    assert foreign[0]["constrained_columns"] == ["user_id"]
    assert (foreign[0]["referred_table"], foreign[0]["referred_columns"], foreign[0]["options"].get("ondelete")) == ("users", ["id"], "RESTRICT")
    model_foreign = list(models.draft.foreign_keys)
    assert len(model_foreign) == 1
    assert (model_foreign[0].target_fullname, model_foreign[0].ondelete) == ("users.id", "RESTRICT")


def test_database_default_is_draft_and_user_is_unique(migrated):
    with migrated.engine.begin() as conn:
        conn.execute(migrated.queue.insert(), draft_values())
        assert conn.execute(sa.select(migrated.queue.c.state)).scalar_one() == "DRAFT"
    with pytest.raises(sa.exc.IntegrityError):
        with migrated.engine.begin() as conn:
            conn.execute(migrated.queue.insert(), draft_values(id=SECOND_ID))
    assert len(snapshot(migrated.engine, migrated.queue)) == 1


@pytest.mark.parametrize("state", ["PREPARED", "QUEUED", "DISPATCHED", "ACKED", "ERASED", "FAILED", "draft", ""])
def test_database_rejects_every_non_draft_state(migrated, state):
    with pytest.raises(sa.exc.IntegrityError):
        with migrated.engine.begin() as conn:
            conn.execute(migrated.queue.insert(), draft_values(state=state))
    assert snapshot(migrated.engine, migrated.queue) == []


@pytest.mark.parametrize("column", ["id", "user_id", "closure_policy_version", "closure_started_at", "prepared_at", "state"])
def test_all_queue_fields_are_required(migrated, column):
    with pytest.raises(sa.exc.IntegrityError):
        with migrated.engine.begin() as conn:
            conn.execute(migrated.queue.insert(), draft_values(**{column: None}))


def test_database_rejects_bad_id_length_and_restricts_user_foreign_key(migrated):
    for changes in (dict(id="short"), dict(user_id="99999999-9999-4999-8999-999999999999")):
        with pytest.raises(sa.exc.IntegrityError):
            with migrated.engine.begin() as conn:
                conn.execute(migrated.queue.insert(), draft_values(**changes))
    with migrated.engine.begin() as conn:
        conn.execute(migrated.queue.insert(), draft_values())
    with pytest.raises(sa.exc.IntegrityError):
        with migrated.engine.begin() as conn:
            conn.execute(migrated.users.delete().where(migrated.users.c.id == USER_IDS[0]))
    assert snapshot(migrated.engine, migrated.users) == migrated.before


def test_empty_downgrade_preserves_every_user_field(migrated, migration):
    run_revision(migrated.engine, migration.downgrade)
    assert not sa.inspect(migrated.engine).has_table(TABLE)
    assert snapshot(migrated.engine, migrated.users) == migrated.before
    assert user_schema(migrated.engine) == migrated.schema


@pytest.mark.parametrize("user_id,prepared_at", [(USER_IDS[0], datetime(2000, 1, 1)), (USER_IDS[1], datetime(2026, 11, 21))])
def test_any_draft_blocks_downgrade_and_retains_all_evidence(migrated, migration, user_id, prepared_at):
    with migrated.engine.begin() as conn:
        conn.execute(migrated.queue.insert(), draft_values(user_id=user_id, prepared_at=prepared_at))
    before = snapshot(migrated.engine, migrated.queue)
    assert before[0]["state"] == "DRAFT"
    with pytest.raises(RuntimeError, match="Erasure preparation evidence exists; retain table"):
        run_revision(migrated.engine, migration.downgrade)
    assert sa.inspect(migrated.engine).has_table(TABLE)
    assert snapshot(migrated.engine, migrated.queue) == before
    assert snapshot(migrated.engine, migrated.users) == migrated.before
    assert user_schema(migrated.engine) == migrated.schema


def test_offline_downgrade_refuses_before_emitting_sql(migration):
    output = io.StringIO()
    context = MigrationContext.configure(url="sqlite://", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        with pytest.raises(RuntimeError, match="offline downgrade"):
            migration.downgrade()
    assert output.getvalue() == ""


@pytest.mark.parametrize("dialect", ["mysql", "mariadb", "oracle"])
def test_unsupported_dialect_refuses_before_sql_or_drop(migration, monkeypatch, dialect):
    execute = Mock(side_effect=AssertionError("Unsupported dialect must not execute SQL"))
    drop = Mock(side_effect=AssertionError("Unsupported dialect must not drop evidence"))
    bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect), execute=execute)
    operations = SimpleNamespace(get_context=lambda: SimpleNamespace(as_sql=False), get_bind=lambda: bind, drop_table=drop)
    monkeypatch.setattr(migration, "op", operations)
    with pytest.raises(RuntimeError, match="Unsupported erasure preparation downgrade dialect"):
        migration.downgrade()
    execute.assert_not_called()
    drop.assert_not_called()
