"""Real PG16 ordering/migration proof in the existing postgres-explain job.

Only explicitly supplied local CI PostgreSQL, in an owned disposable database.
Full offline suite may opt out; an explicit invocation of this file MUST NOT skip.
No providers, default application engine, shared-schema reset, or new CI service.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import os
from pathlib import Path
import uuid

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from test_startup_migration_pg import pg_startup  # noqa: F401 — owned DB lifecycle/URL guard
from test_ym_review_read_hold import bound, response, seed, service, utc, FUTURE, SENTINEL


PARENT = "csr1a2b3c4d01"
REVISION = "ymh1a2b3c4d01"


@pytest.fixture
def pg_hold(request, monkeypatch):
    explicit = any(Path(str(arg).split("::", 1)[0]).name == Path(__file__).name
                   for arg in request.config.args)
    raw = os.environ.get("PULT_TEST_PG_URL")
    if not raw:
        if explicit:
            pytest.fail("Dedicated B1 PG invocation requires PULT_TEST_PG_URL; proof cannot skip")
        pytest.skip("Real PG16 opt-in absent in ordinary offline suite")
    # The existing fixture validates exact localhost/port/user/database before connecting,
    # creates a unique owned database, and never drops a shared schema or terminates sessions.
    engine, dbm = request.getfixturevalue("pg_startup")
    import database

    def deny(*args, **kwargs):
        raise AssertionError("B1 PG proof forbids default engine/provider transport")

    sa.event.listen(database.engine.sync_engine, "do_connect", deny)
    monkeypatch.setattr(httpx.AsyncClient, "request", deny)
    try:
        yield engine, dbm
    finally:
        sa.event.remove(database.engine.sync_engine, "do_connect", deny)


def config(dbm):
    cfg = dbm._alembic_config()
    assert ScriptDirectory.from_config(cfg).get_heads() == ['acl1a2b3c4d01']
    return cfg


@asynccontextmanager
async def sessions(engine):
    url = engine.url.set(drivername="postgresql+asyncpg")
    async_engine = create_async_engine(url, pool_size=8, max_overflow=0)
    try:
        S = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
        yield S
    finally:
        await async_engine.dispose()


async def wait_lock(S, pid):
    """Prove a separate backend is blocked, rather than relying on a timed sleep."""
    async with asyncio.timeout(1.5):
        async with S() as observer:
            while True:
                blockers = (await observer.execute(sa.text(
                    "SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid})).scalar_one()
                if blockers:
                    return
                await asyncio.sleep(0.01)


def tracked_factory(S, queue, *, gate=None):
    @asynccontextmanager
    async def factory():
        async with S() as db:
            pid = (await db.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one()
            queue.put_nowait(pid)
            if gate is not None:
                original = db.commit

                async def commit():
                    gate[0].set()
                    await gate[1].wait()
                    await original()
                db.commit = commit
            yield db
    return factory


def execute_case(pg_hold, case):
    engine, dbm = pg_hold
    command.upgrade(config(dbm), "head")

    async def go():
        async with sessions(engine) as S:
            rows = await seed(S)
            await case(service(), S, rows)
    asyncio.run(go())


def test_pg_committed_writer_before_admission_denies_and_identity_rechecks_under_lock(pg_hold):
    async def case(T, S, rows):
        client, queue = object(), asyncio.Queue()
        async with S() as holder:
            # The authority row lock is real and held by a distinct physical PG backend.
            await holder.execute(sa.text(
                "UPDATE marketplace_accounts SET ym_review_read_not_before=:until WHERE id=:id"),
                {"until": FUTURE, "id": rows[0].account_id})

            async def admit():
                async with bound(T, S, rows[0], client, factory=tracked_factory(S, queue)):
                    await T.admit_selected_read(client)
            task = asyncio.create_task(admit())
            try:
                pid = await asyncio.wait_for(queue.get(), 1)
                await wait_lock(S, pid)
                assert not task.done()
                await holder.commit()
                with pytest.raises(T.YMReviewReadDeferred) as exc:
                    await task
                assert exc.value.reason == "hold" and utc(exc.value.not_before) == FUTURE
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        # Context is resolved before the changed identity commits; locked validation
        # must not silently reinterpret a once-managed account as an exclusion.
        queue = asyncio.Queue()
        async with S() as holder:
            await holder.execute(sa.text(
                "UPDATE marketplace_accounts SET identity_status='unverified' WHERE id=:id"),
                {"id": rows[0].account_id})
            task = asyncio.create_task(admit())
            try:
                pid = await asyncio.wait_for(queue.get(), 1)
                await wait_lock(S, pid)
                await holder.commit()
                with pytest.raises(T.YMReviewReadDeferred) as exc:
                    await task
                assert exc.value.reason == "identity"
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    execute_case(pg_hold, case)


def test_pg_admission_before_writer_is_pre_admitted_and_writer_serializes(pg_hold):
    async def case(T, S, rows):
        client = object()
        entered, release = asyncio.Event(), asyncio.Event()
        admitted_pid, observed_pid = asyncio.Queue(), asyncio.Queue()

        async def admit():
            async with bound(T, S, rows[0], client,
                             factory=tracked_factory(S, admitted_pid, gate=(entered, release))):
                await T.admit_selected_read(client)

        async def observe():
            async with bound(T, S, rows[0], client, factory=tracked_factory(S, observed_pid)):
                with pytest.raises(T.YMReviewReadDeferred):
                    await T.observe_selected_read(client, response([
                        ("X-RateLimit-Resource-Until", format_datetime(FUTURE, usegmt=True))]))

        admission = asyncio.create_task(admit())
        observation = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            observation = asyncio.create_task(observe())
            pid = await asyncio.wait_for(observed_pid.get(), 1)
            await wait_lock(S, pid)
            assert not observation.done()
            release.set()
            await admission  # allowed: committed admission precedes the observation
            await observation
            async with bound(T, S, rows[0], client):
                with pytest.raises(T.YMReviewReadDeferred):
                    await T.admit_selected_read(client)
        finally:
            release.set()
            for task in (admission, observation):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*[t for t in (admission, observation) if t is not None],
                                 return_exceptions=True)
    execute_case(pg_hold, case)


def test_pg_clock_is_evaluated_after_lock_not_transaction_start(pg_hold):
    async def case(T, S, rows):
        client, queue = object(), asyncio.Queue()
        async with S() as holder:
            until = (await holder.execute(sa.text(
                "UPDATE marketplace_accounts SET ym_review_read_not_before=clock_timestamp()+interval '300 milliseconds' "
                "WHERE id=:id RETURNING ym_review_read_not_before"),
                {"id": rows[0].account_id})).scalar_one()

            async def admit():
                async with bound(T, S, rows[0], client, factory=tracked_factory(S, queue)):
                    await T.admit_selected_read(client)
            task = asyncio.create_task(admit())
            try:
                pid = await asyncio.wait_for(queue.get(), 1)
                await wait_lock(S, pid)
                # A database-driven time barrier, not a fake PG ordering assertion.
                while utc((await holder.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()) <= utc(until):
                    await asyncio.sleep(0.01)
                await holder.commit()
                await task  # fresh clock admits; transaction-start timestamp would falsely deny
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    execute_case(pg_hold, case)


def test_pg_atomic_max_sticky_indeterminate_and_caller_rollback(pg_hold):
    async def case(T, S, rows):
        client = object()
        async def one(headers):
            async with bound(T, S, rows[0], client):
                with pytest.raises(T.YMReviewReadDeferred):
                    await T.observe_selected_read(client, response(headers))
        await asyncio.gather(*[
            one([("X-RateLimit-Resource-Until", format_datetime(FUTURE - timedelta(days=i), usegmt=True))])
            for i in range(6)])
        async with bound(T, S, rows[0], client) as caller:
            # Pending unrelated business write must NOT get committed by the observer.
            await caller.execute(sa.text("UPDATE marketplace_connections SET label='UNCOMMITTED' WHERE id=:id"),
                                 {"id": rows[0].connection_id})
            with pytest.raises(T.YMReviewReadDeferred):
                await T.observe_selected_read(client, response([("Retry-After", "bad")]))
            await caller.rollback()
        await asyncio.gather(one([("Retry-After", "0")]), one([("Retry-After", "1")]))
        async with S() as db:
            saved = (await db.execute(sa.text(
                "SELECT ym_review_read_not_before, ym_review_read_indeterminate, updated_at "
                "FROM marketplace_accounts WHERE id=:id"), {"id": rows[0].account_id})).one()
            assert utc(saved[0]) == FUTURE and saved[1] is True
            assert utc(saved[2]) == SENTINEL.replace(tzinfo=timezone.utc)
            assert (await db.execute(sa.text("SELECT label FROM marketplace_connections WHERE id=:id"),
                                     {"id": rows[0].connection_id})).scalar_one() is None
        # Fresh session/context represents a restart; neither success nor a shorter hint clears it.
        async with bound(T, S, rows[0], client):
            with pytest.raises(T.YMReviewReadDeferred) as exc:
                await T.admit_selected_read(client)
            assert exc.value.reason == "indeterminate"
    execute_case(pg_hold, case)


def test_pg_timeout_cleanup_does_not_commit_caller_and_releases_waiter(pg_hold):
    async def case(T, S, rows):
        client = object()
        async with S() as holder:
            await holder.execute(sa.text("UPDATE marketplace_accounts SET label='UNCOMMITTED' WHERE id=:id"),
                                 {"id": rows[0].account_id})
            async with bound(T, S, rows[0], client):
                with pytest.raises(T.YMReviewReadDeferred) as exc:
                    await asyncio.wait_for(T.observe_selected_read(client, response([("Retry-After", "3600")])), 3)
                assert exc.value.reason == "authority"
            await holder.rollback()
        async with bound(T, S, rows[0], client):
            await T.admit_selected_read(client)
        async with S() as db:
            saved = (await db.execute(sa.text(
                "SELECT label, ym_review_read_not_before FROM marketplace_accounts WHERE id=:id"),
                {"id": rows[0].account_id})).one()
            assert saved == ("Synthetic", None)
    execute_case(pg_hold, case)


def test_pg_seeded_additive_migration_old_reader_compatibility_and_downgrade_guards(pg_hold):
    engine, dbm = pg_hold
    cfg = config(dbm)
    command.upgrade(cfg, PARENT)
    uid, wid, aid, cid = [str(uuid.uuid4()) for _ in range(4)]
    with engine.begin() as db:
        db.execute(sa.text("INSERT INTO users(id,email,name,hashed_password) VALUES (:id,:email,'Synthetic','synthetic')"),
                   {"id": uid, "email": uid + "@example.invalid"})
        db.execute(sa.text("INSERT INTO workspaces(id,owner_user_id,created_at) VALUES (:id,:uid,clock_timestamp())"),
                   {"id": wid, "uid": uid})
        db.execute(sa.text("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,external_account_id,identity_status,label) "
                           "VALUES (:id,:wid,'yandex','101','verified','before')"), {"id": aid, "wid": wid})
        db.execute(sa.text("INSERT INTO marketplace_connections(id,user_id,marketplace,workspace_id,marketplace_account_id,scopes) "
                           "VALUES (:id,:uid,'yandex',:wid,:aid,'[\"feedbacks\"]')"),
                   {"id": cid, "uid": uid, "wid": wid, "aid": aid})
    command.upgrade(cfg, "head")
    assert ScriptDirectory.from_config(cfg).get_heads() == ['acl1a2b3c4d01']
    with engine.begin() as db:
        saved = db.execute(sa.text("SELECT label,ym_review_read_not_before,ym_review_read_indeterminate "
                                  "FROM marketplace_accounts WHERE id=:id"), {"id": aid}).one()
        assert saved == ("before", None, False)
        # Old-style writer names only old fields; additive expansion does not reset hold.
        db.execute(sa.text("UPDATE marketplace_accounts SET ym_review_read_not_before=clock_timestamp()+interval '1 hour' WHERE id=:id"), {"id": aid})
        db.execute(sa.text("UPDATE marketplace_accounts SET label='old-writer' WHERE id=:id"), {"id": aid})
    with pytest.raises(RuntimeError, match="Refusing downgrade: live or indeterminate"):
        command.downgrade(cfg, PARENT)
    assert {c["name"] for c in sa.inspect(engine).get_columns("marketplace_accounts")} >= {
        "ym_review_read_not_before", "ym_review_read_indeterminate"}
    with engine.begin() as db:
        db.execute(sa.text("UPDATE marketplace_accounts SET ym_review_read_not_before=clock_timestamp()-interval '1 hour', "
                           "ym_review_read_indeterminate=true WHERE id=:id"), {"id": aid})
    with pytest.raises(RuntimeError, match="Refusing downgrade: live or indeterminate"):
        command.downgrade(cfg, PARENT)
    with engine.begin() as db:
        db.execute(sa.text("UPDATE marketplace_accounts SET ym_review_read_indeterminate=false WHERE id=:id"), {"id": aid})
    command.downgrade(cfg, PARENT)
    with engine.connect() as db:
        assert db.execute(sa.text("SELECT label FROM marketplace_accounts WHERE id=:id"), {"id": aid}).scalar_one() == "old-writer"
        assert db.execute(sa.text("SELECT user_id FROM marketplace_connections WHERE id=:id"), {"id": cid}).scalar_one() == uid
    command.upgrade(cfg, "head")
