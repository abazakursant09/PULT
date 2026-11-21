"""Eight real-PG16 cases, only in an explicitly owned ephemeral loopback DB.

The trusted native harness/CI pre-creates the owner, nonce database and ordinary
NOINHERIT LOGIN reader (local trust or the fixed fixture-only password below).
It verifies the fresh server data directory before supplying these three pins.
No admin URL, role creation, application URL fallback, schema reset or live IO.
An explicit gate must assert eight cases and zero skips in its JUnit result.
"""
import asyncio
from dataclasses import FrozenInstanceError
from datetime import timedelta
import os
from pathlib import Path
import re
import sys
import traceback
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backup.erasure_operator import DraftObservation, ReviewError, _epoch
from services.account_closure_policy import POLICY_VERSION
from services.erasure_operator_review import CommittedDraftReviewer


_ENV = ('PULT_ERASURE_OPERATOR_TEST_URL', 'PULT_ERASURE_OPERATOR_TEST_DATA',
        'PULT_ERASURE_OPERATOR_TEST_DATABASE_OID')
_READER = 'erasure_operator_reader'
_SECRET = 'SYNTHETIC-OPERATOR-SECRET-DO-NOT-DISCLOSE'


def _owned_configuration():
    values = [os.environ.get(name) for name in _ENV]
    if not any(name in os.environ for name in _ENV):
        pytest.skip('Explicit owned ephemeral PULT_ERASURE_OPERATOR_TEST_URL/DATA/DATABASE_OID required')
    try:
        raw, data, oid = values
        url = make_url(raw)
        path = Path(data)
        valid = (url.drivername == 'postgresql+asyncpg' and url.host == '127.0.0.1'
            and url.username == 'erasure_operator_owner'
            and url.password in (None, 'erasure-operator-fixture-only')
            and type(url.port) is int and 1024 < url.port <= 65535 and not url.query
            and re.fullmatch(r'erasure_operator_test_[0-9a-f]{32}', url.database or '')
            and path.is_absolute() and path.name == 'data'
            and type(oid) is str and oid.isascii() and oid.isdigit()
            and str(int(oid)) == oid and 16384 <= int(oid) < 2**32)
        if valid:
            return url, int(oid)
    except (ValueError, TypeError, AttributeError, ArgumentError):
        pass
    pytest.fail('Invalid explicit owned operator PG test configuration', pytrace=False)


async def _owned_database():
    url, oid = _owned_configuration()
    owner = create_async_engine(url, poolclass=NullPool, echo=False, hide_parameters=True)
    reader = create_async_engine(url.set(username=_READER), poolclass=NullPool,
                                 echo=False, hide_parameters=True)
    try:
        async with owner.begin() as db:
            identity = (await db.execute(text("""
                SELECT current_database(), d.oid, pg_get_userbyid(d.datdba),
                       session_user, current_user, r.rolsuper, r.rolcreatedb,
                       r.rolcreaterole, r.rolreplication, r.rolbypassrls
                FROM pg_database AS d JOIN pg_roles AS r ON r.rolname=session_user
                WHERE d.datname=current_database()
            """))).one()
            assert identity == (url.database, oid, url.username, url.username,
                                url.username, False, False, False, False, False)
            version = int((await db.execute(text('SHOW server_version_num'))).scalar_one())
            assert 160000 <= version < 170000
            attributes = (await db.execute(text("""
                SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                       rolinherit, rolreplication, rolbypassrls
                FROM pg_roles WHERE rolname='erasure_operator_reader'
            """))).one()
            assert attributes == (True, False, False, False, False, False, False)
            # Intentionally permissive state/time columns let the reader prove
            # fail-closed handling of future/corrupt drafts without schema edits.
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS public.users (
                    id varchar(36) PRIMARY KEY, deleted_at timestamp,
                    closure_policy_version varchar(32), closure_started_at timestamptz,
                    closure_restore_requested_at timestamptz,
                    closure_purge_started_at timestamptz,
                    email text, name text, hashed_password text,
                    verification_token text, reset_token text, registered_ip text)
            """))
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS public.erasure_preparations (
                    id varchar(36) PRIMARY KEY,
                    user_id varchar(36) NOT NULL UNIQUE REFERENCES public.users(id),
                    closure_policy_version varchar(32) NOT NULL,
                    closure_started_at timestamptz NOT NULL,
                    prepared_at timestamptz NOT NULL, state varchar(16) NOT NULL)
            """))
            await db.execute(text('GRANT USAGE ON SCHEMA public TO erasure_operator_reader'))
            await db.execute(text('GRANT SELECT ON public.users, public.erasure_preparations TO erasure_operator_reader'))
        review = CommittedDraftReviewer(reader, database_name=url.database,
                                       database_oid=oid, reader_role=_READER)
        return SimpleNamespace(owner=owner, reader=reader, review=review, url=url, oid=oid)
    except BaseException:
        await reader.dispose()
        await owner.dispose()
        raise


@pytest.fixture
def pg():
    loop = asyncio.new_event_loop()
    try:
        value = loop.run_until_complete(_owned_database())
        value.run = loop.run_until_complete
        yield value
    finally:
        if 'value' in locals():
            loop.run_until_complete(value.reader.dispose())
            loop.run_until_complete(value.owner.dispose())
        loop.close()


async def _seed(pg, *, draft=True):
    account, draft_id = str(uuid4()), str(uuid4())
    async with pg.owner.begin() as db:
        now = (await db.execute(text('SELECT transaction_timestamp()'))).scalar_one()
        epoch, prepared = now - timedelta(days=31), now - timedelta(hours=1)
        await db.execute(text("""
            INSERT INTO public.users (id, deleted_at, closure_policy_version,
                closure_started_at, email, name, hashed_password,
                verification_token, reset_token, registered_ip)
            VALUES (:id, :deleted, :policy, :epoch, :secret, :secret,
                :secret, :secret, :secret, :secret)
        """), dict(id=account, deleted=epoch.replace(tzinfo=None), policy=POLICY_VERSION,
                   epoch=epoch, secret=_SECRET))
        if draft:
            await _insert_draft(db, account, draft_id, epoch, prepared)
    return SimpleNamespace(account=account, draft=draft_id, epoch=epoch, prepared=prepared)


async def _insert_draft(db, account, draft, epoch, prepared):
    await db.execute(text("""
        INSERT INTO public.erasure_preparations
            (id, user_id, closure_policy_version, closure_started_at, prepared_at, state)
        VALUES (:draft, :account, :policy, :epoch, :prepared, 'DRAFT')
    """), dict(draft=draft, account=account, policy=POLICY_VERSION, epoch=epoch, prepared=prepared))


async def _observe(pg, row):
    return await pg.review(row.account, row.draft, nonce=str(uuid4()))


async def _refused(pg, row, reason='committed_draft_not_reviewable'):
    with pytest.raises(ReviewError, match='^' + reason + '$'):
        await _observe(pg, row)


async def _snapshot(pg, row):
    async with pg.owner.connect() as db:
        user = (await db.execute(text('SELECT * FROM public.users WHERE id=:id'),
                                 {'id': row.account})).one()
        draft = (await db.execute(text('SELECT * FROM public.erasure_preparations WHERE id=:id'),
                                  {'id': row.draft})).one_or_none()
        return tuple(user), None if draft is None else tuple(draft)


def test_pg_operator_uncommitted_commit_and_rollback_visibility(pg):
    async def scenario():
        from services.erasure_preparation import prepare_erasure_draft

        row = await _seed(pg, draft=False)
        async with AsyncSession(pg.owner, expire_on_commit=False) as writer:
            await writer.begin()
            tentative = await prepare_erasure_draft(writer, row.account)
            row.draft = tentative.request_id
            assert tentative.status == 'DRAFT_ONLY_CALLER_COMMIT_REQUIRED'
            await _refused(pg, row)  # Another physical connection cannot see it.
            await writer.commit()
        observation = await _observe(pg, row)
        assert type(observation) is DraftObservation
        assert (observation.account_id, observation.draft_id) == (row.account, row.draft)
        assert observation.status == 'COMMITTED_DRAFT_REVIEW_ONLY'
        assert observation.identity_enrollment_admitted is False
        assert observation.journal_dispatch_admitted is observation.purge_or_restore_admitted is False
        assert observation.policy == POLICY_VERSION
        assert _epoch(observation.closure_epoch) == row.epoch
        assert _epoch(observation.reviewed_at) >= row.prepared
        assert row.account not in repr(observation) and row.draft not in repr(observation)
        with pytest.raises(FrozenInstanceError):
            observation.status = 'ADMITTED'
        rolled_back = await _seed(pg, draft=False)
        async with AsyncSession(pg.owner, expire_on_commit=False) as writer:
            await writer.begin()
            tentative = await prepare_erasure_draft(writer, rolled_back.account)
            rolled_back.draft = tentative.request_id
            await _refused(pg, rolled_back)
            await writer.rollback()
        await _refused(pg, rolled_back)
        assert (await _snapshot(pg, rolled_back))[1] is None
    pg.run(scenario())


def test_pg_operator_matching_policy_epoch_and_prepared_at_fail_closed(pg):
    async def scenario():
        row, other = await _seed(pg), await _seed(pg)
        await _refused(pg, SimpleNamespace(account=row.account, draft=other.draft))
        await _refused(pg, SimpleNamespace(account=str(uuid4()), draft=row.draft))
        await _refused(pg, SimpleNamespace(account=row.account, draft=str(uuid4())))
        statements = (
            "UPDATE public.erasure_preparations SET state='FUTURE' WHERE id=:draft",
            "UPDATE public.erasure_preparations SET closure_policy_version='unknown' WHERE id=:draft",
            "UPDATE public.users SET closure_policy_version='unknown' WHERE id=:account",
            "UPDATE public.erasure_preparations SET closure_started_at=closure_started_at-interval '1 second' WHERE id=:draft",
            "UPDATE public.erasure_preparations SET prepared_at=closure_started_at-interval '1 second' WHERE id=:draft",
            "UPDATE public.erasure_preparations SET prepared_at=closure_started_at+interval '29 days' WHERE id=:draft",
            "UPDATE public.erasure_preparations SET prepared_at=transaction_timestamp()+interval '1 day' WHERE id=:draft",
        )
        for sql in statements:
            async with pg.owner.connect() as writer:
                await writer.begin()
                await writer.execute(text(sql), dict(account=row.account, draft=row.draft))
                await writer.commit()
            await _refused(pg, row)
            async with pg.owner.begin() as writer:
                await writer.execute(text("""
                    UPDATE public.erasure_preparations SET state='DRAFT',
                        closure_policy_version=:policy, closure_started_at=:epoch,
                        prepared_at=:prepared WHERE id=:draft
                """), dict(policy=POLICY_VERSION, epoch=row.epoch, prepared=row.prepared, draft=row.draft))
                await writer.execute(text('UPDATE public.users SET closure_policy_version=:policy WHERE id=:account'),
                                     dict(policy=POLICY_VERSION, account=row.account))
        await _observe(pg, row)
        await _recovery_failures(pg)
    pg.run(scenario())


def _recovery_failures(pg):
    async def scenario():
        for sql in (
                'UPDATE public.users SET deleted_at=NULL WHERE id=:id',
                'UPDATE public.users SET closure_policy_version=NULL WHERE id=:id',
                'UPDATE public.users SET closure_started_at=NULL WHERE id=:id',
                'UPDATE public.users SET closure_restore_requested_at=transaction_timestamp() WHERE id=:id',
                'UPDATE public.users SET closure_purge_started_at=transaction_timestamp() WHERE id=:id'):
            row = await _seed(pg)
            async with pg.owner.begin() as writer:
                await writer.execute(text(sql), {'id': row.account})
            await _refused(pg, row)
        for days in (29, -1):
            row = await _seed(pg)
            async with pg.owner.begin() as writer:
                epoch = (await writer.execute(text(
                    'SELECT transaction_timestamp()-make_interval(days => :days)'), {'days': days})).scalar_one()
                await writer.execute(text('UPDATE public.users SET closure_started_at=:epoch WHERE id=:id'),
                                     dict(epoch=epoch, id=row.account))
                await writer.execute(text('UPDATE public.erasure_preparations SET closure_started_at=:epoch WHERE id=:id'),
                                     dict(epoch=epoch, id=row.draft))
            await _refused(pg, row)
    return scenario()


def test_pg_operator_pins_database_oid_and_login_role(pg):
    async def scenario():
        row = await _seed(pg)
        with pytest.raises(ReviewError, match='^trusted_review_engine_required$'):
            CommittedDraftReviewer(pg.owner, database_name=pg.url.database,
                                   database_oid=pg.oid, reader_role=_READER)
        with pytest.raises(ReviewError, match='^trusted_review_engine_required$'):
            CommittedDraftReviewer(pg.reader, database_name='wrong_database',
                                   database_oid=pg.oid, reader_role=_READER)
        wrong_oid = CommittedDraftReviewer(pg.reader, database_name=pg.url.database,
                                           database_oid=pg.oid+1, reader_role=_READER)
        with pytest.raises(ReviewError, match='^review_database_identity_refused$'):
            await wrong_oid(row.account, row.draft, nonce=str(uuid4()))
        # Even an explicitly misconfigured owner pin cannot pass the DB/role audit.
        owner = CommittedDraftReviewer(pg.owner, database_name=pg.url.database,
                                       database_oid=pg.oid, reader_role=pg.url.username)
        with pytest.raises(ReviewError, match='^review_reader_privileges_refused$'):
            await owner(row.account, row.draft, nonce=str(uuid4()))
        # Native connect_args can override URL routing; server-side pins must
        # still reject either misroute before selecting any application rows.
        for override in ({'user': pg.url.username}, {'database': 'postgres'}):
            misrouted = create_async_engine(pg.url.set(username=_READER),
                poolclass=NullPool, hide_parameters=True, connect_args=override)
            try:
                review = CommittedDraftReviewer(misrouted, database_name=pg.url.database,
                                               database_oid=pg.oid, reader_role=_READER)
                with pytest.raises(ReviewError, match='^review_database_identity_refused$'):
                    await review(row.account, row.draft, nonce=str(uuid4()))
            finally:
                await misrouted.dispose()
    pg.run(scenario())


def _target_write_grants_refused(pg):
    async def scenario():
        row = await _seed(pg)
        for grant, revoke in (
            ('GRANT UPDATE ON public.users TO erasure_operator_reader',
             'REVOKE UPDATE ON public.users FROM erasure_operator_reader'),
            ('GRANT INSERT ON public.erasure_preparations TO erasure_operator_reader',
             'REVOKE INSERT ON public.erasure_preparations FROM erasure_operator_reader'),
            ('GRANT UPDATE (state) ON public.erasure_preparations TO erasure_operator_reader',
             'REVOKE UPDATE (state) ON public.erasure_preparations FROM erasure_operator_reader'),
        ):
            async with pg.owner.begin() as writer:
                await writer.execute(text(grant))
            try:
                await _refused(pg, row, 'review_reader_privileges_refused')
            finally:
                async with pg.owner.begin() as writer:
                    await writer.execute(text(revoke))
        await _observe(pg, row)
    return scenario()


def test_pg_operator_reader_cannot_write_and_review_preserves_rows(pg):
    async def scenario():
        row = await _seed(pg)
        before = await _snapshot(pg, row)
        for sql in (
            "UPDATE public.users SET name='forbidden' WHERE id=:id",
            'DELETE FROM public.users WHERE id=:id',
            "INSERT INTO public.users(id) VALUES (:id)",
            "UPDATE public.erasure_preparations SET state='forbidden' WHERE id=:id",
            'DELETE FROM public.erasure_preparations WHERE id=:id',
            'TRUNCATE public.erasure_preparations',
        ):
            async with pg.reader.connect() as reader:
                with pytest.raises(DBAPIError) as error:
                    await reader.execute(text(sql), {'id': row.account})
                assert getattr(error.value.orig, 'sqlstate', None) == '42501'
        selected = []
        def record(_conn, _cursor, sql, _parameters, _context, _many):
            selected.append(sql)
        event.listen(pg.reader.sync_engine, 'before_cursor_execute', record)
        try:
            await _observe(pg, row)
        finally:
            event.remove(pg.reader.sync_engine, 'before_cursor_execute', record)
        assert await _snapshot(pg, row) == before
        assert selected and all(sql.lstrip().upper().startswith(('SELECT', 'SET ')) for sql in selected)
        for sql in selected:
            assert not any(secret in sql.lower() for secret in (
                'hashed_password', 'verification_token', 'reset_token', 'registered_ip', 'u.email', 'u.name'))
        await _target_write_grants_refused(pg)
        assert await _snapshot(pg, row) == before
    pg.run(scenario())


def test_pg_operator_fresh_connections_do_not_retain_stale_snapshot(pg):
    async def scenario():
        row = await _seed(pg)
        pids = []
        def connected(dbapi, _record):
            pids.append(dbapi.driver_connection.get_server_pid())
        event.listen(pg.reader.sync_engine, 'connect', connected)
        try:
            first = await _observe(pg, row)
            async with pg.reader.connect() as stale:
                await stale.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                query = text('SELECT deleted_at FROM public.users WHERE id=:id')
                assert (await stale.execute(query, {'id': row.account})).scalar_one() is not None
                async with pg.owner.begin() as writer:
                    await writer.execute(text('UPDATE public.users SET deleted_at=NULL WHERE id=:id'), {'id': row.account})
                assert (await stale.execute(query, {'id': row.account})).scalar_one() is not None
                await _refused(pg, row)
            assert len(pids) == len(set(pids)) == 3
            assert first.status == 'COMMITTED_DRAFT_REVIEW_ONLY'  # Not a live fence.
        finally:
            event.remove(pg.reader.sync_engine, 'connect', connected)
    pg.run(scenario())


def test_pg_operator_readonly_repeatable_read_and_local_timeouts(pg):
    async def scenario():
        row = await _seed(pg)
        settings = text("""SELECT current_setting('transaction_read_only'),
            current_setting('transaction_isolation'), current_setting('statement_timeout'),
            current_setting('lock_timeout'), current_setting('idle_in_transaction_session_timeout')""")
        async with pg.reader.connect() as connection:
            defaults = (await connection.execute(settings)).one()
        observed = []
        def inspect_transaction(connection, _cursor, sql, _parameters, _context, _many):
            if 'FROM public.users AS u' in sql:
                observed.append(tuple(connection.execute(settings).one()))
        event.listen(pg.reader.sync_engine, 'before_cursor_execute', inspect_transaction)
        try:
            await _observe(pg, row)
        finally:
            event.remove(pg.reader.sync_engine, 'before_cursor_execute', inspect_transaction)
        assert observed == [('on', 'repeatable read', '2s', '500ms', '3s')]
        async with pg.reader.connect() as connection:
            assert (await connection.execute(settings)).one() == defaults
    pg.run(scenario())


def test_pg_operator_validates_arguments_and_engine_before_connect(pg):
    async def scenario():
        row = await _seed(pg)
        opened = []
        def connected(_dbapi, _record):
            opened.append(True)
        event.listen(pg.reader.sync_engine, 'connect', connected)
        try:
            for bad in (None, True, uuid4(), 'not-a-uuid', str(uuid4()).upper(),
                        '6ba7b810-9dad-11d1-80b4-00c04fd430c8'):
                for key in ('account_id', 'draft_id', 'nonce'):
                    values = dict(account_id=row.account, draft_id=row.draft, nonce=str(uuid4()))
                    values[key] = bad
                    with pytest.raises(ReviewError, match='^canonical_review_uuid_required$'):
                        await pg.review(**values)
            with pytest.raises(ReviewError, match='^review_identity_alias_refused$'):
                await pg.review(row.account, row.draft, nonce=row.account)
            pooled = create_async_engine(pg.url.set(username=_READER), hide_parameters=True)
            sqlite = create_async_engine('sqlite+aiosqlite:///:memory:',
                                          poolclass=NullPool, hide_parameters=True)
            unhidden = create_async_engine(pg.url.set(username=_READER), poolclass=NullPool)
            try:
                async with AsyncSession(pg.reader) as session:
                    for engine in (session, pooled, sqlite, unhidden):
                        with pytest.raises(ReviewError, match='^trusted_review_engine_required$'):
                            CommittedDraftReviewer(engine, database_name=pg.url.database,
                                                   database_oid=pg.oid, reader_role=_READER)
            finally:
                await unhidden.dispose()
                await sqlite.dispose()
                await pooled.dispose()
            assert not opened
            await _observe(pg, row)
            assert len(opened) == 1
        finally:
            event.remove(pg.reader.sync_engine, 'connect', connected)
    pg.run(scenario())


def test_pg_operator_sanitizes_storage_and_cleanup_failures(pg, monkeypatch):
    async def scenario():
        row = await _seed(pg)
        before = await _snapshot(pg, row)
        def broken(_conn, _cursor, _sql, _parameters, _context, _many):
            raise RuntimeError(_SECRET)
        async def refused():
            with pytest.raises(ReviewError, match='^review_storage_failed$') as error:
                await _observe(pg, row)
            rendered = ''.join(traceback.format_exception(error.value))
            for value in (_SECRET, row.account, row.draft, str(pg.url), str(pg.oid)):
                assert value not in str(error.value) and value not in rendered
        event.listen(pg.reader.sync_engine, 'before_cursor_execute', broken)
        try:
            await refused()
        finally:
            event.remove(pg.reader.sync_engine, 'before_cursor_execute', broken)
        for cls in (AsyncTransaction, AsyncConnection):
            original = cls.__aexit__
            async def cleanup_then_fail(self, *args):
                await original(self, *args)
                raise RuntimeError(_SECRET)
            with monkeypatch.context() as patch:
                patch.setattr(cls, '__aexit__', cleanup_then_fail)
                await refused()
        async def slow_checkout(self, *args, **kwargs):
            await asyncio.sleep(30)
            raise AssertionError('whole-call deadline did not cancel checkout')
        with monkeypatch.context() as patch:
            patch.setattr(AsyncConnection, 'start', slow_checkout)
            started = asyncio.get_running_loop().time()
            await refused()
            elapsed = asyncio.get_running_loop().time() - started
            assert 4.5 <= elapsed < 7
        assert await _snapshot(pg, row) == before
        await _observe(pg, row)
    pg.run(scenario())
