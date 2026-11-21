"""DRAFT-only preparation: disposable two-table SQLite, never journal dispatch.

PostgreSQL coverage below is SQL compilation only, not a concurrency claim.
The normal repository conftest remains in force; no pytest-asyncio is required.
"""
import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import inspect as python_inspect
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid1, uuid4

import pytest
from sqlalchemy import delete, event, func, insert, inspect, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.erasure_preparation import ErasurePreparation
from models.user import User
from services import account_closure as lifecycle
from services import erasure_preparation as preparation
from services.account_closure_policy import POLICY_VERSION

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
EPOCH = NOW - timedelta(days=30)


@pytest.fixture
def preparation_db(monkeypatch):
    loop = asyncio.new_event_loop()
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, 'connect')
    def foreign_keys(connection, _record):
        cursor = connection.cursor()
        try: cursor.execute('PRAGMA foreign_keys=ON')
        finally: cursor.close()

    uid = str(uuid4())

    async def setup():
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(
                sync, tables=[User.__table__, ErasurePreparation.__table__]))
            await connection.execute(insert(User).values(id=uid, email='draft@example.invalid',
                name='SYNTHETIC-NAME', hashed_password='SYNTHETIC-PASSWORD-HASH',
                verification_token='SYNTHETIC-VERIFY-TOKEN', reset_token='SYNTHETIC-RESET-TOKEN',
                registered_ip='192.0.2.1', deleted_at=EPOCH, closure_policy_version=POLICY_VERSION,
                closure_started_at=EPOCH, closure_restore_requested_at=None, closure_purge_started_at=None))
        return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()

    db = loop.run_until_complete(setup())
    statements, orm_statements = [], []

    def record_sql(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    def record_orm(execution): orm_statements.append(execution.statement)

    event.listen(engine.sync_engine, 'before_cursor_execute', record_sql)
    event.listen(db.sync_session, 'do_orm_execute', record_orm)
    monkeypatch.setattr(lifecycle, 'utc_now', lambda: NOW)
    fixture = SimpleNamespace(loop=loop, engine=engine, db=db, uid=uid,
        run=loop.run_until_complete, statements=statements, orm_statements=orm_statements)
    try:
        yield fixture
    finally:
        event.remove(db.sync_session, 'do_orm_execute', record_orm)
        event.remove(engine.sync_engine, 'before_cursor_execute', record_sql)
        loop.run_until_complete(db.close())
        loop.run_until_complete(engine.dispose())
        loop.close()


async def count_drafts(db):
    return await db.scalar(select(func.count()).select_from(ErasurePreparation))


async def user_snapshot(db, uid):
    return dict((await db.execute(select(User.__table__).where(User.id == uid))).mappings().one())


def test_exact_30_day_boundary_returns_frozen_nonadmitting_draft(preparation_db):
    f = preparation_db

    async def exercise():
        async with f.db.begin():
            result = await preparation.prepare_erasure_draft(f.db, f.uid)
            assert type(result) is preparation.DraftPreparation
            assert result.status == 'DRAFT_ONLY_CALLER_COMMIT_REQUIRED'
            assert result.subject_candidate_id == f.uid
            assert result.subject_enrolled is False
            assert result.journal_dispatch_admitted is False
            assert result.purge_or_restore_admitted is False
            assert UUID(result.request_id).version == 4
            assert result.request_id not in repr(result) and f.uid not in repr(result)
            with pytest.raises(FrozenInstanceError): result.status = 'COMMITTED'
            row = await f.db.get(ErasurePreparation, result.request_id)
            assert row.state == 'DRAFT' and row.user_id == f.uid
            assert row.prepared_at.replace(tzinfo=timezone.utc) == NOW
            assert row.closure_started_at.replace(tzinfo=timezone.utc) == EPOCH
    f.run(exercise())


def test_one_microsecond_before_30_days_is_not_review_due(preparation_db, monkeypatch):
    f = preparation_db
    monkeypatch.setattr(lifecycle, 'utc_now', lambda: NOW - timedelta(microseconds=1))

    async def exercise():
        async with f.db.begin():
            with pytest.raises(preparation.PreparationError, match='^ordinary_closure_not_review_due$'):
                await preparation.prepare_erasure_draft(f.db, f.uid)
            assert await count_drafts(f.db) == 0
    f.run(exercise())


def test_active_legacy_recovered_future_and_purge_started_are_refused(preparation_db):
    f = preparation_db

    async def exercise():
        cases = [dict(deleted_at=None), dict(closure_policy_version=None, closure_started_at=None),
            dict(deleted_at=None, is_restored=True, closure_restore_requested_at=EPOCH),
            dict(closure_started_at=NOW + timedelta(seconds=1)), dict(closure_purge_started_at=NOW)]
        for changes in cases:
            async with f.db.begin():
                await f.db.execute(update(User).where(User.id == f.uid).values(deleted_at=EPOCH,
                    closure_policy_version=POLICY_VERSION, closure_started_at=EPOCH,
                    closure_restore_requested_at=None, closure_purge_started_at=None, is_restored=False))
                await f.db.execute(update(User).where(User.id == f.uid).values(**changes))
            async with f.db.begin():
                with pytest.raises(preparation.PreparationError, match='^ordinary_closure_not_review_due$'):
                    await preparation.prepare_erasure_draft(f.db, f.uid)
                assert await count_drafts(f.db) == 0
    f.run(exercise())


def test_missing_and_noncanonical_account_ids_refuse_without_draft(preparation_db):
    f = preparation_db

    async def exercise():
        async with f.db.begin():
            for value in (None, True, uuid4(), str(uuid1()), 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA', 'not-an-id'):
                with pytest.raises(preparation.PreparationError, match='^canonical_account_uuid_required$'):
                    await preparation.prepare_erasure_draft(f.db, value)
            with pytest.raises(preparation.PreparationError, match='^account_not_found$'):
                await preparation.prepare_erasure_draft(f.db, str(uuid4()))
            assert await count_drafts(f.db) == 0
    f.run(exercise())


def test_explicit_caller_transaction_required_without_autobegin(preparation_db):
    f = preparation_db
    assert not f.db.in_transaction()
    with pytest.raises(preparation.PreparationError, match='^caller_transaction_required$'):
        f.run(preparation.prepare_erasure_draft(f.db, f.uid))
    assert not f.db.in_transaction() and not f.statements


def test_new_dirty_and_deleted_session_states_refuse_without_autoflush(preparation_db):
    f = preparation_db

    async def refused():
        f.statements.clear()
        with pytest.raises(preparation.PreparationError, match='^clean_session_required$'):
            await preparation.prepare_erasure_draft(f.db, f.uid)
        assert not f.statements

    async def exercise():
        async with f.db.begin():
            f.db.add(User(email='pending@example.invalid', name='pending', hashed_password='NOT-FLUSHED'))
            await refused()
            await f.db.rollback()
        async with f.db.begin():
            cached = await f.db.get(User, f.uid); cached.name = 'DIRTY-NOT-FLUSHED'
            await refused()
            await f.db.rollback()
        async with f.db.begin():
            result = await preparation.prepare_erasure_draft(f.db, f.uid)
        async with f.db.begin():
            row = await f.db.get(ErasurePreparation, result.request_id)
            await f.db.delete(row)
            await refused()
            await f.db.rollback()
    f.run(exercise())


def test_same_epoch_draft_is_idempotent_in_and_after_caller_commit(preparation_db):
    f = preparation_db

    async def exercise():
        async with f.db.begin():
            first = await preparation.prepare_erasure_draft(f.db, f.uid)
            second = await preparation.prepare_erasure_draft(f.db, f.uid)
            assert first == second and await count_drafts(f.db) == 1
        async with f.db.begin():
            third = await preparation.prepare_erasure_draft(f.db, f.uid)
            assert third == first and await count_drafts(f.db) == 1
        assert sum(s.lstrip().upper().startswith('INSERT INTO ERASURE_PREPARATIONS') for s in f.statements) == 1
    f.run(exercise())


def test_caller_rollback_atomically_discards_draft_without_touching_user(preparation_db):
    f = preparation_db

    async def exercise():
        before = await user_snapshot(f.db, f.uid); await f.db.rollback()
        await f.db.begin()
        tentative = await preparation.prepare_erasure_draft(f.db, f.uid)
        assert await count_drafts(f.db) == 1
        await f.db.rollback()
        assert await count_drafts(f.db) == 0
        assert await user_snapshot(f.db, f.uid) == before
        await f.db.rollback()
        async with f.db.begin():
            fresh = await preparation.prepare_erasure_draft(f.db, f.uid)
            assert fresh.request_id != tentative.request_id
    f.run(exercise())


def test_only_caller_commits_and_result_never_upgrades_external_claim(preparation_db, monkeypatch):
    f = preparation_db
    commit = AsyncMock(wraps=f.db.commit); rollback = AsyncMock(wraps=f.db.rollback)
    monkeypatch.setattr(f.db, 'commit', commit); monkeypatch.setattr(f.db, 'rollback', rollback)

    async def exercise():
        await f.db.begin()
        result = await preparation.prepare_erasure_draft(f.db, f.uid)
        commit.assert_not_awaited(); rollback.assert_not_awaited()
        assert f.db.in_transaction()
        await f.db.commit(); commit.assert_awaited_once()
        assert (await f.db.get(ErasurePreparation, result.request_id)).state == 'DRAFT'
        assert result.status == 'DRAFT_ONLY_CALLER_COMMIT_REQUIRED' and not result.subject_enrolled
    f.run(exercise())


def test_old_closure_epoch_draft_blocks_new_epoch_without_overwrite(preparation_db):
    f = preparation_db

    async def exercise():
        async with f.db.begin(): first = await preparation.prepare_erasure_draft(f.db, f.uid)
        async with f.db.begin():
            await f.db.execute(update(User).where(User.id == f.uid).values(closure_started_at=EPOCH-timedelta(days=1)))
        f.statements.clear()
        async with f.db.begin():
            with pytest.raises(preparation.PreparationError, match='^earlier_preparation_requires_review$'):
                await preparation.prepare_erasure_draft(f.db, f.uid)
            row = await f.db.get(ErasurePreparation, first.request_id)
            assert row.closure_started_at.replace(tzinfo=timezone.utc) == EPOCH
            assert await count_drafts(f.db) == 1
        assert all(s.lstrip().upper().startswith('SELECT') for s in f.statements)
    f.run(exercise())


def test_stale_user_identity_map_cannot_hide_fresh_recovery(preparation_db):
    f = preparation_db

    async def exercise():
        cached = await f.db.get(User, f.uid)
        assert cached.deleted_at is not None
        await f.db.execute(update(User).where(User.id == f.uid).values(deleted_at=None)
                           .execution_options(synchronize_session=False))
        await f.db.commit()
        assert cached.deleted_at is not None and cached not in f.db.dirty
        async with f.db.begin():
            with pytest.raises(preparation.PreparationError, match='^ordinary_closure_not_review_due$'):
                await preparation.prepare_erasure_draft(f.db, f.uid)
            assert await count_drafts(f.db) == 0
        assert cached.deleted_at is not None  # scalar read, not a cached User refresh
    f.run(exercise())


def test_only_two_tables_no_secret_select_and_no_user_mutations(preparation_db):
    f = preparation_db

    async def exercise():
        async with f.engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        assert set(tables) == {'users', 'erasure_preparations'}
        before = await user_snapshot(f.db, f.uid); await f.db.rollback()
        f.statements.clear()
        async with f.db.begin(): await preparation.prepare_erasure_draft(f.db, f.uid)
        observed = list(f.statements)
        assert observed and all(s.lstrip().upper().startswith(('SELECT', 'INSERT INTO ERASURE_PREPARATIONS')) for s in observed)
        for statement in observed:
            assert not any(name in statement.lower() for name in
                ('hashed_password', 'verification_token', 'reset_token', 'registered_ip', 'users.email', 'users.name'))
        assert await user_snapshot(f.db, f.uid) == before
    f.run(exercise())


def test_stale_draft_identity_map_cannot_hide_changed_database_epoch(preparation_db):
    f = preparation_db

    async def exercise():
        async with f.db.begin():
            original = await preparation.prepare_erasure_draft(f.db, f.uid)
            cached = await f.db.get(ErasurePreparation, original.request_id)
        changed = EPOCH - timedelta(days=1)
        async with f.db.begin():
            await f.db.execute(update(ErasurePreparation).where(ErasurePreparation.id == original.request_id)
                .values(closure_started_at=changed).execution_options(synchronize_session=False))
        assert cached.closure_started_at.replace(tzinfo=timezone.utc) == EPOCH
        f.statements.clear()
        async with f.db.begin():
            with pytest.raises(preparation.PreparationError, match='^earlier_preparation_requires_review$'):
                await preparation.prepare_erasure_draft(f.db, f.uid)
            assert cached.closure_started_at.replace(tzinfo=timezone.utc) == changed
            assert await count_drafts(f.db) == 1
        assert f.statements and all(s.lstrip().upper().startswith('SELECT') for s in f.statements)
    f.run(exercise())


def test_actual_selects_compile_to_postgresql_row_locks_only(preparation_db):
    f = preparation_db

    async def exercise():
        f.orm_statements.clear()
        async with f.db.begin(): await preparation.prepare_erasure_draft(f.db, f.uid)
        statements = [s for s in f.orm_statements if getattr(s, 'is_select', False)]
        assert len(statements) == 2
        sql = [str(s.compile(dialect=postgresql.dialect())) for s in statements]
        assert 'FOR UPDATE OF users' in sql[0] and 'FOR UPDATE' in sql[1]
        assert [column.key for column in statements[0].selected_columns] == [
            'id', 'deleted_at', 'closure_policy_version', 'closure_started_at',
            'closure_restore_requested_at', 'closure_purge_started_at']
    f.run(exercise())


def test_model_enforces_draft_unique_user_foreign_key_and_required_fields(preparation_db):
    f = preparation_db

    def values(**changes):
        result = dict(id=str(uuid4()), user_id=f.uid, closure_policy_version=POLICY_VERSION,
                      closure_started_at=EPOCH, prepared_at=NOW, state='DRAFT')
        result.update(changes); return result

    async def reject(row):
        with pytest.raises(IntegrityError):
            async with f.db.begin(): await f.db.execute(insert(ErasurePreparation).values(**row))

    async def exercise():
        for invalid in (values(state='COMMITTED'), values(id='short'), values(user_id=str(uuid4())),
                values(closure_policy_version=None), values(closure_started_at=None), values(prepared_at=None), values(state=None)):
            await reject(invalid)
        async with f.db.begin(): await f.db.execute(insert(ErasurePreparation).values(**values()))
        await reject(values())
        with pytest.raises(IntegrityError):
            async with f.db.begin(): await f.db.execute(delete(User).where(User.id == f.uid))
        async with f.db.begin(): assert await count_drafts(f.db) == 1
    f.run(exercise())


def test_request_nonce_is_server_uuid4_and_no_caller_id_timestamp_or_phase_api(preparation_db, monkeypatch):
    f = preparation_db; nonce = uuid4(); generator = Mock(return_value=nonce)
    monkeypatch.setattr(preparation, 'uuid4', generator)
    assert list(python_inspect.signature(preparation.prepare_erasure_draft).parameters) == ['db', 'user_id']

    async def exercise():
        async with f.db.begin():
            for argument in ('request_id', 'subject_id', 'phase', 'prepared_at', 'state', 'attempt_id'):
                with pytest.raises(TypeError):
                    await preparation.prepare_erasure_draft(f.db, f.uid, **{argument: 'CALLER-VALUE'})
            result = await preparation.prepare_erasure_draft(f.db, f.uid)
            again = await preparation.prepare_erasure_draft(f.db, f.uid)
            assert result.request_id == str(nonce) and result == again
            assert str(UUID(result.request_id)) == result.request_id and UUID(result.request_id).version == 4
            generator.assert_called_once_with()
    f.run(exercise())


def test_existing_codec_accepts_candidate_ids_without_any_dispatch(preparation_db, monkeypatch):
    f = preparation_db
    path = Path(__file__).resolve().parents[2] / 'ops/backup/custodian_v2/codec/event_codec.py'
    codec = ModuleType('preparation_codec_format_only')
    monkeypatch.setitem(sys.modules, codec.__name__, codec)
    exec(compile(path.read_bytes(), '<existing-pure-event-codec>', 'exec'), codec.__dict__)

    async def exercise():
        async with f.db.begin():
            result = await preparation.prepare_erasure_draft(f.db, f.uid)
            candidate = codec.Event(result.request_id, result.subject_candidate_id, 'intent')
            assert candidate.purge_id == result.request_id and candidate.subject_id == f.uid
            assert candidate.phase == 'intent' and result.journal_dispatch_admitted is False
            assert result.request_id not in repr(candidate) and f.uid not in repr(candidate)
    f.run(exercise())


def test_storage_failure_is_sanitized_no_retry_and_caller_must_rollback(preparation_db, monkeypatch):
    f = preparation_db
    flush = AsyncMock(side_effect=SQLAlchemyError('SYNTHETIC-PRIVATE-SQL-PARAMETERS'))
    commit = AsyncMock(wraps=f.db.commit); rollback = AsyncMock(wraps=f.db.rollback)
    nonce = Mock(return_value=uuid4())
    monkeypatch.setattr(f.db, 'flush', flush); monkeypatch.setattr(f.db, 'commit', commit)
    monkeypatch.setattr(f.db, 'rollback', rollback); monkeypatch.setattr(preparation, 'uuid4', nonce)

    async def exercise():
        await f.db.begin()
        with pytest.raises(preparation.PreparationError, match='^preparation_storage_failed$') as error:
            await preparation.prepare_erasure_draft(f.db, f.uid)
        assert error.value.__suppress_context__ is True and 'PRIVATE' not in str(error.value)
        flush.assert_awaited_once(); nonce.assert_called_once_with()
        commit.assert_not_awaited(); rollback.assert_not_awaited()
        assert f.db.in_transaction() and len(f.db.new) == 1
        await f.db.rollback(); rollback.assert_awaited_once()
        assert not f.db.new and await count_drafts(f.db) == 0
    f.run(exercise())


def test_reuses_review_and_refuses_naive_server_preparation_clock(preparation_db, monkeypatch):
    f = preparation_db; review = Mock(wraps=lifecycle.review_erasure_eligibility)
    clock = Mock(side_effect=[NOW, NOW.replace(tzinfo=None)])
    monkeypatch.setattr(lifecycle, 'review_erasure_eligibility', review)
    monkeypatch.setattr(lifecycle, 'utc_now', clock)

    async def exercise():
        async with f.db.begin():
            with pytest.raises(preparation.PreparationError, match='^timezone_aware_clock_required$'):
                await preparation.prepare_erasure_draft(f.db, f.uid)
            assert await count_drafts(f.db) == 0
        review.assert_called_once()
        scalar = review.call_args.args[0]
        assert not isinstance(scalar, User) and scalar.id == f.uid
        assert clock.call_count == 2
    f.run(exercise())
