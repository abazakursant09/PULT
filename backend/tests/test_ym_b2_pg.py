"""Real PG16 B2 transaction/permit barriers; CI-only owned disposable database."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
from models.marketplace_account import MarketplaceAccount
from models.user import User
from services.marketplace import credential_vault, executor
from services.marketplace import ym_review_read_hold as T
from test_startup_migration_pg import pg_startup  # noqa: F401
from test_ym_b2_claim_admission import (
    FUTURE, install_rollback_fault, mint_permit, own_log, prepared, seed_claim_rows,
)
from test_ym_review_read_hold_pg import wait_lock


@pytest.fixture
def pg_b2(request, monkeypatch):
    explicit = any(Path(str(arg).split("::", 1)[0]).name == Path(__file__).name
                   for arg in request.config.args)
    if not os.environ.get("PULT_TEST_PG_URL"):
        if explicit:
            pytest.fail("Dedicated B2 PG invocation requires PULT_TEST_PG_URL; cannot skip")
        pytest.skip("Real PG16 opt-in absent in ordinary offline suite")
    # Existing lifecycle strictly validates loopback/postgres/pult_explain/5432,
    # creates only pult_startup_<uuid>, never FORCE or resets public/shared DB.
    engine, dbm = request.getfixturevalue("pg_startup")
    import database

    def deny(*args, **kwargs):
        raise AssertionError("B2 PG proof forbids provider/default application engine")

    sa.event.listen(database.engine.sync_engine, "do_connect", deny)
    monkeypatch.setattr(httpx.AsyncClient, "request", deny)
    try:
        command.upgrade(dbm._alembic_config(), "head")
        yield engine
    finally:
        sa.event.remove(database.engine.sync_engine, "do_connect", deny)


@asynccontextmanager
async def owned_state(pg_b2, *, held=False):
    engine = create_async_engine(pg_b2.url.set(drivername="postgresql+asyncpg"), pool_size=6, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            state = await seed_claim_rows(db, held=held)
            state.factory, state.engine, state.sent = factory, engine, []
            yield state
    finally:
        await engine.dispose()


def test_pg_denial_savepoint_releases_account_lock_preserves_caller_work(pg_b2):
    async def run():
        async with owned_state(pg_b2, held=True) as state:
            async with prepared(state) as (binding, intent, _, _):
                await state.db.execute(sa.update(User).values(name="Pending"))
                with pytest.raises(T.YMReviewReadDeferred):
                    async with T.claim_admission(state.db, binding=binding, intent=intent) as grant:
                        await grant.allow_new_claim()
                async with state.factory() as other:
                    await other.execute(sa.text("SET LOCAL lock_timeout='300ms'"))
                    await other.execute(sa.update(MarketplaceAccount).values(label="Other transaction"))
                    await other.commit()  # Would time out if savepoint rollback retained authority lock.
                    assert (await other.execute(sa.select(User.name))).scalar_one() == "Synthetic"
                    assert (await other.execute(sa.select(ExecutionLog))).scalars().all() == []
                assert (await state.db.execute(sa.select(User.name))).scalar_one() == "Pending"
                await state.db.rollback()
    asyncio.run(run())


def test_pg_release_retains_lock_through_original_claim_commit_and_earlier_permit_survives(pg_b2):
    async def run():
        async with owned_state(pg_b2) as state:
            async with prepared(state) as (binding, intent, request, client):
                async with T.claim_admission(state.db, binding=binding, intent=intent) as grant:
                    await grant.allow_new_claim()
                    row = own_log(state)
                    state.db.add(row)
                    await state.db.flush([row])
                    stamp = T.ClaimStamp(row.id, row.idempotency_key, row.request_fingerprint, row.claim_generation)
                    queue = asyncio.Queue()

                    async def writer():
                        async with state.factory() as db:
                            queue.put_nowait((await db.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one())
                            await db.execute(sa.update(MarketplaceAccount).values(ym_review_read_not_before=FUTURE))
                            await db.commit()

                    task = asyncio.create_task(writer())
                    try:
                        await wait_lock(state.factory, await queue.get())
                        permit = await grant.commit_claim(state.db.commit, stamp=stamp)
                        await task
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                # Simulate the unchanged owned fence; later committed hold cannot revoke admission.
                fenced = (await state.db.execute(executor._FENCE_CAS, {
                    "id": stamp.log_id, "now": FUTURE, "gen": stamp.claim_generation})).first()
                await state.db.commit()
                assert fenced is not None
                with T.activate_first_write(permit, stamp=stamp):
                    with T.selected_yandex_operation("publish_feedback_answer", client=client, business_id="BIZ1"):
                        await T.admit_yandex_attempt(client, token="SYNTHETIC", attempt=1, **request)
                assert permit.used
    asyncio.run(run())


@pytest.mark.parametrize("expires_during_wait", [False, True])
def test_pg_writer_before_admission_and_fresh_clock_after_wait(pg_b2, expires_during_wait):
    async def run():
        async with owned_state(pg_b2) as state:
            queue = asyncio.Queue()
            async with state.factory() as writer:
                until = "clock_timestamp() + interval '150 milliseconds'" if expires_during_wait else ":until"
                await writer.execute(sa.text("UPDATE marketplace_accounts SET ym_review_read_not_before=" + until),
                                     {} if expires_during_wait else {"until": FUTURE})

                async def claimant():
                    async with state.factory() as db:
                        child = SimpleNamespace(**vars(state))
                        child.db = db
                        child.args = dict(state.args, db=db)
                        queue.put_nowait((await db.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one())
                        async with prepared(child) as (binding, intent, _, _):
                            if expires_during_wait:
                                permit, _, _ = await mint_permit(child, binding, intent)
                                return permit is not None
                            with pytest.raises(T.YMReviewReadDeferred):
                                async with T.claim_admission(db, binding=binding, intent=intent) as grant:
                                    await grant.allow_new_claim()
                            return False

                task = asyncio.create_task(claimant())
                try:
                    await wait_lock(state.factory, await queue.get())
                    if expires_during_wait:
                        async with asyncio.timeout(1):
                            while not (await writer.execute(sa.text(
                                    "SELECT clock_timestamp() > ym_review_read_not_before FROM marketplace_accounts"))).scalar_one():
                                await asyncio.sleep(.01)
                    await writer.commit()
                    assert await task is expires_during_wait
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            async with state.factory() as observer:
                rows = (await observer.execute(sa.select(ExecutionLog))).scalars().all()
                assert len(rows) == int(expires_during_wait)
                assert all(row.attempt_count == 0 for row in rows)
    asyncio.run(run())


def test_pg_commit_ack_loss_is_uncertain_not_deferred_or_permitted(pg_b2):
    async def run():
        async with owned_state(pg_b2) as state:
            calls = []

            async def lost_ack():
                calls.append(1)
                await state.db.commit()
                raise ConnectionError("synthetic lost commit acknowledgement")

            async with prepared(state) as (binding, intent, _, _):
                with pytest.raises(T.YMClaimCommitUncertain) as caught:
                    await mint_permit(state, binding, intent, commit=lost_ack)
            assert caught.value.transaction_unusable
            assert calls == [1]
            async with state.factory() as observer:
                rows = (await observer.execute(sa.select(ExecutionLog))).scalars().all()
                assert len(rows) == 1
                assert rows[0].attempt_count == 0 and rows[0].dispatch_started_at is None
    asyncio.run(run())


def test_pg_rotation_revalidated_and_reowned_generation_cannot_fence(pg_b2):
    async def run():
        async with owned_state(pg_b2) as state:
            async with prepared(state) as (binding, intent, _, _):
                async with state.factory() as writer:
                    await writer.execute(sa.update(ApiCredential).values(
                        meta={T.SECRET_GENERATION_KEY: uuid.uuid4().hex, "account_ref": "BIZ1"},
                        secret_enc=credential_vault.encrypt("ROTATED")))
                    await writer.commit()
                with pytest.raises(T.YMReviewReadDeferred):
                    async with T.claim_admission(state.db, binding=binding, intent=intent) as grant:
                        await grant.allow_new_claim()
            assert (await state.db.execute(sa.select(ExecutionLog))).scalars().all() == []
            await state.db.rollback()
            # Restore a coherent synthetic proof solely in this owned test DB.
            async with state.factory() as writer:
                credential = await writer.get(ApiCredential, state.credential_id)
                nonce = uuid.uuid4().hex
                credential.secret_enc = credential_vault.encrypt("SYNTHETIC")
                credential.meta = {"account_ref": "BIZ1", T.SECRET_GENERATION_KEY: nonce,
                    T.CABINET_PROOF_KEY: {"version": 1, "generation": nonce,
                        "credential_id": credential.id, "account_id": state.account_id, "business_id": "BIZ1"}}
                await writer.commit()
            async with prepared(state) as (binding, intent, _, _):
                permit, stamp, _ = await mint_permit(state, binding, intent)
                async with state.factory() as reowner:
                    await reowner.execute(sa.update(ExecutionLog).values(claim_generation=stamp.claim_generation + 1))
                    await reowner.commit()
                fenced = (await state.db.execute(executor._FENCE_CAS, {
                    "id": stamp.log_id, "now": FUTURE, "gen": stamp.claim_generation})).first()
                await state.db.commit()
                assert fenced is None
                with pytest.raises(T.YMWritePermitError):
                    with T.activate_first_write(permit, stamp=replace(stamp, claim_generation=stamp.claim_generation + 1)):
                        pytest.fail("adopted reowned generation")
                assert not permit.used
    asyncio.run(run())


def test_pg_child_task_cannot_consume_parent_permit(pg_b2):
    async def run():
        async with owned_state(pg_b2) as state:
            async with prepared(state) as (binding, intent, _, _):
                permit, stamp, _ = await mint_permit(state, binding, intent)
                started, release = asyncio.Event(), asyncio.Event()

                async def child():
                    started.set()
                    await release.wait()
                    with pytest.raises(T.YMWritePermitError):
                        with T.activate_first_write(permit, stamp=stamp):
                            pytest.fail("child inherited parent authority")

                task = asyncio.create_task(child())
                await started.wait()
                release.set()
                await task
                assert not permit.used
    asyncio.run(run())


def test_pg_stalled_cleanup_invalidates_caller_and_releases_actual_waiter(pg_b2, monkeypatch):
    async def run():
        async with owned_state(pg_b2, held=True) as state:
            monkeypatch.setattr(T, "AsyncSessionLocal", state.factory)
            await state.db.execute(sa.update(User).values(name="Must not commit"))
            rollbacks = install_rollback_fault(state, monkeypatch, "stall")
            pid = asyncio.Queue()

            async def waiter():
                async with state.factory() as db:
                    pid.put_nowait((await db.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one())
                    await db.execute(sa.update(MarketplaceAccount).values(label="Waiter completed"))
                    await db.commit()

            execution = asyncio.create_task(executor.execute(**state.args))
            blocked = None
            try:
                async with asyncio.timeout(7):
                    while not rollbacks:
                        await asyncio.sleep(.01)
                    blocked = asyncio.create_task(waiter())
                    await wait_lock(state.factory, await pid.get())
                    with pytest.raises(T.YMClaimCommitUncertain) as caught:
                        await execution
                    assert caught.value.transaction_unusable is True
                    await blocked  # Physical PG row lock must be released, not merely a flag set.
            finally:
                tasks = [task for task in (execution, blocked) if task is not None and not task.done()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            with pytest.raises((T.YMClaimCommitUncertain, sa.exc.InvalidRequestError)):
                await state.db.commit()  # No repairing rollback supplied by the test.
            with pytest.raises((T.YMClaimCommitUncertain, sa.exc.InvalidRequestError)):
                await state.db.execute(sa.select(User.name))
            async with state.factory() as observer:
                assert (await observer.execute(sa.select(User.name))).scalar_one() == "Synthetic"
                assert (await observer.execute(sa.select(ExecutionLog))).scalars().all() == []
    asyncio.run(run())


def test_pg_cleanup_failure_does_not_allow_later_caller_commit(pg_b2, monkeypatch):
    async def run():
        async with owned_state(pg_b2, held=True) as state:
            monkeypatch.setattr(T, "AsyncSessionLocal", state.factory)
            await state.db.execute(sa.update(User).values(name="Must not commit"))
            install_rollback_fault(state, monkeypatch, "failure")
            async with asyncio.timeout(7):
                with pytest.raises(T.YMClaimCommitUncertain) as caught:
                    await executor.execute(**state.args)
                assert caught.value.transaction_unusable is True
            with pytest.raises((T.YMClaimCommitUncertain, sa.exc.InvalidRequestError)):
                await state.db.commit()
            with pytest.raises((T.YMClaimCommitUncertain, sa.exc.InvalidRequestError)):
                await state.db.execute(sa.select(User.name))
            async with state.factory() as observer:
                await observer.execute(sa.text("SET LOCAL lock_timeout='300ms'"))
                await observer.execute(sa.update(MarketplaceAccount).values(label="No residual lock"))
                await observer.commit()
                assert (await observer.execute(sa.select(User.name))).scalar_one() == "Synthetic"
                assert (await observer.execute(sa.select(ExecutionLog))).scalars().all() == []
    asyncio.run(run())
