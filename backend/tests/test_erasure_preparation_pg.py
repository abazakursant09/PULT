"""Real PG concurrency gate, only an explicitly owned ephemeral loopback cluster.

No application URL fallback, schema reset, S3, credentials or journal dispatch.
The private test harness supplies the verified data-directory and nonce DB.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.user import User
from models.erasure_preparation import ErasurePreparation
from services import account_closure as lifecycle
from services import erasure_preparation as preparation
from services.account_closure_policy import POLICY_VERSION

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
EPOCH = NOW - timedelta(days=31)


async def owned_database(app_name):
    raw = os.environ.get('PULT_ERASURE_PREP_TEST_URL')
    if not raw:
        pytest.skip('Explicit owned ephemeral PULT_ERASURE_PREP_TEST_URL required')
    url = make_url(raw)
    data = Path(os.environ['PULT_ERASURE_PREP_TEST_DATA']).resolve()
    oid = os.environ['PULT_ERASURE_PREP_TEST_DATABASE_OID']
    assert oid.isascii() and oid.isdigit() and 16384 <= int(oid) < 2**32
    # The trusted orchestrator verifies ownership of its fresh cluster before
    # supplying this server-side path. In CI it is the pinned job's service;
    # the native harness independently checks its fresh local data directory.
    assert (url.drivername == 'postgresql+asyncpg' and url.host == '127.0.0.1'
        and url.username == 'erasure_prep_owner'
        and url.password in (None, 'erasure-prep-fixture-only')
        and type(url.port) is int and 1024 < url.port <= 65535
        and not url.query and re.fullmatch('erasure_prep_test_[0-9a-f]{32}', url.database or '')
        and data.is_absolute() and data.name == 'data')
    engine = create_async_engine(url, connect_args={'server_settings': {'application_name': app_name}})
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(text('SELECT current_database()'))).scalar_one() == url.database
            assert (await connection.execute(text('SELECT session_user'))).scalar_one() == url.username
            # Restricted data_directory is checked by the trusted orchestrator,
            # not by granting pg_read_all_settings/superuser to this test role.
            identity = (await connection.execute(text(
                'SELECT oid, pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()'))).one()
            assert identity == (int(oid), url.username)
            version = int((await connection.execute(text('SHOW server_version_num'))).scalar_one())
            assert 160000 <= version < 170000
            await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=[User.__table__, ErasurePreparation.__table__]))
        sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        uid = str(uuid4())
        async with sessions.begin() as db:
            await db.execute(insert(User).values(id=uid, email=uid+'@example.invalid',
                name='synthetic', hashed_password='not-a-password', deleted_at=EPOCH.replace(tzinfo=None),
                closure_policy_version=POLICY_VERSION, closure_started_at=EPOCH))
        return engine, sessions, uid
    except BaseException:
        await engine.dispose()
        raise


def test_pg_two_preparers_get_one_server_owned_draft(monkeypatch):
    monkeypatch.setattr(lifecycle, 'utc_now', lambda: NOW)
    async def scenario():
        engine, sessions, uid = await owned_database('erasure-prep-pair-'+uuid4().hex)
        try:
            async def prepare():
                async with sessions.begin() as db:
                    value = await preparation.prepare_erasure_draft(db, uid)
                    assert value.journal_dispatch_admitted is value.purge_or_restore_admitted is False
                    await asyncio.sleep(.02)
                    return value.request_id
            first, second = await asyncio.wait_for(asyncio.gather(prepare(), prepare()), 5)
            assert first == second
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(ErasurePreparation).where(ErasurePreparation.user_id == uid)) == 1
                assert await db.scalar(select(ErasurePreparation.id).where(ErasurePreparation.user_id == uid)) == first
        finally:
            await engine.dispose()
    asyncio.run(scenario())


def test_pg_preparer_waits_for_locked_recovery_then_refuses(monkeypatch):
    monkeypatch.setattr(lifecycle, 'utc_now', lambda: NOW)
    async def scenario():
        app = 'erasure-prep-lock-'+uuid4().hex
        engine, sessions, uid = await owned_database(app)
        task = None
        try:
            async with sessions() as owner:
                async with owner.begin():
                    await owner.execute(select(User.id).where(User.id == uid).with_for_update())
                    async def blocked():
                        async with sessions.begin() as waiter:
                            with pytest.raises(preparation.PreparationError, match='^ordinary_closure_not_review_due$'):
                                await preparation.prepare_erasure_draft(waiter, uid)
                    task = asyncio.create_task(blocked())
                    # Observe an actual PG Lock wait, not merely a sleeping task.
                    async with engine.connect() as observer:
                        end = asyncio.get_running_loop().time()+3
                        while True:
                            waiting = (await observer.execute(text(
                                "SELECT count(*) FROM pg_stat_activity WHERE application_name=:app AND wait_event_type='Lock'"),
                                {'app': app})).scalar_one()
                            # Do not reuse a transaction-cached statistics view.
                            await observer.commit()
                            if waiting:
                                break
                            assert not task.done() and asyncio.get_running_loop().time() < end
                            await asyncio.sleep(.01)
                    await owner.execute(update(User).where(User.id == uid).values(deleted_at=None))
                await asyncio.wait_for(task, 3)
                task = None
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(ErasurePreparation).where(ErasurePreparation.user_id == uid)) == 0
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await engine.dispose()
    asyncio.run(scenario())


def test_pg_caller_rollback_retains_no_committed_draft(monkeypatch):
    monkeypatch.setattr(lifecycle, 'utc_now', lambda: NOW)
    async def scenario():
        engine, sessions, uid = await owned_database('erasure-prep-rollback-'+uuid4().hex)
        try:
            async with sessions() as db:
                await db.begin()
                tentative = await preparation.prepare_erasure_draft(db, uid)
                await db.rollback()
            async with sessions.begin() as db:
                assert await db.scalar(select(func.count()).select_from(ErasurePreparation).where(ErasurePreparation.user_id == uid)) == 0
                committed = await preparation.prepare_erasure_draft(db, uid)
                assert tentative.request_id != committed.request_id
                assert committed.status == 'DRAFT_ONLY_CALLER_COMMIT_REQUIRED'
            async with sessions() as db:
                assert await db.scalar(select(ErasurePreparation.id).where(ErasurePreparation.user_id == uid)) == committed.request_id
        finally:
            await engine.dispose()
    asyncio.run(scenario())
