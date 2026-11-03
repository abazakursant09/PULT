"""B2 claim admission through the existing executor; synthetic HTTP/owned WAL DB."""
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import models  # noqa: F401
from database import Base
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.user import User
from models.workspace import Workspace
from services.marketplace import credential_vault, executor, operation_key
from services.marketplace import ym_review_read_hold
from services.marketplace.yandex_client import build_publish_feedback_request, yandex_client

from test_marketplace_retry_policy import no_external_io  # noqa: F401


FUTURE = datetime(2099, 11, 3, tzinfo=timezone.utc)


async def seed_claim_rows(db, *, marketplace="yandex", managed=True, held=True, proven=True):
    uid, wid, aid, cid, kid = [str(uuid.uuid4()) for _ in range(5)]
    # Existing credential/verification columns are timestamp WITHOUT time zone;
    # keep UTC wall time there, while the hold column uses aware UTC FUTURE.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    meta = {"account_ref": "BIZ1"}
    if marketplace == "yandex" and managed and proven:
        nonce = uuid.uuid4().hex
        meta.update({ym_review_read_hold.SECRET_GENERATION_KEY: nonce,
                     ym_review_read_hold.CABINET_PROOF_KEY: {
                         "version": 1, "generation": nonce, "credential_id": kid,
                         "account_id": aid, "business_id": "BIZ1"}})
    db.add(User(id=uid, email=f"{uid}@example.invalid", name="Synthetic", hashed_password="x"))
    await db.flush()  # Explicit FK order also applies on PostgreSQL, not only SQLite.
    db.add(Workspace(id=wid, owner_user_id=uid))
    await db.flush()
    db.add(MarketplaceAccount(
        id=aid, workspace_id=wid, marketplace=marketplace,
        external_account_id="BIZ1" if managed else None,
        identity_status="verified" if managed else "unverified_legacy",
        ym_review_read_not_before=FUTURE if held else None,
    ))
    await db.flush()
    db.add(MarketplaceConnection(
        id=cid, user_id=uid, workspace_id=wid, marketplace_account_id=aid,
        marketplace=marketplace, status="connected", scopes=["feedbacks"],
        verification_status="verified" if managed else "unverified", verified_at=now,
    ))
    await db.flush()
    db.add(ApiCredential(
        id=kid, connection_id=cid, scope="feedbacks",
        secret_enc=credential_vault.encrypt("SYNTHETIC"), meta=meta,
        verification_status="verified" if managed else "unverified",
        verified_at=now, updated_at=now,
    ))
    await db.commit()
    args = dict(db=db, user_id=uid, connection_id=cid, action_type="publish_review_response",
                payload={"marketplace": marketplace, "feedback_id": "42", "text": "Synthetic reply", "rating": 5},
                idempotency_key=operation_key.review_key(str(uuid.uuid4())))
    return SimpleNamespace(db=db, account_id=aid, connection_id=cid, credential_id=kid, args=args)


@asynccontextmanager
async def scenario(tmp_path, monkeypatch, *, marketplace="yandex", managed=True, held=True, proven=True,
                   http_status=200, http_headers=None, http_barrier=None):
    # Native executor fixture shape, but owned file/WAL supports independent authority sessions.
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + (tmp_path / "claim-admission.sqlite").as_posix(),
        connect_args={"timeout": .2},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sent = []

    class SyntheticHTTP:
        def __init__(self, *, timeout):
            assert timeout is not None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, *, headers, json, params):
            assert method == "POST"
            if marketplace == "yandex":
                assert url == "https://api.partner.market.yandex.ru/v2/businesses/BIZ1/goods-feedback/comments/update"
                assert headers["Api-Key"] == "SYNTHETIC"
                assert json == {"feedbackId": 42, "comment": {"text": "Synthetic reply"}}
            else:
                assert url.endswith("/api/v1/feedbacks/answer")
                assert headers["Authorization"] == "SYNTHETIC"
                assert json == {"id": "42", "text": "Synthetic reply"}
            assert params is None
            sent.append((method, url))  # no tokens or headers retained
            if http_barrier is not None:
                http_barrier[0].set()
                await http_barrier[1].wait()
            if http_status == "timeout":
                raise httpx.ReadTimeout("synthetic delivery uncertainty")
            return httpx.Response(http_status, headers=http_headers, json={
                "requestId": "synthetic-request", "result": {"id": 7, "status": "PUBLISHED"},
            })

    if marketplace == "wildberries":
        # Older suite members leave a successful instance stub installed. Exercise
        # the real transport in this scenario and restore prior state on teardown.
        from services.marketplace.wb_client import WBClient, wb_client
        monkeypatch.setattr(wb_client, "publish_feedback_answer",
                            WBClient.publish_feedback_answer.__get__(wb_client, WBClient))
    monkeypatch.setattr(httpx, "AsyncClient", SyntheticHTTP)
    monkeypatch.setattr(ym_review_read_hold, "AsyncSessionLocal", factory)
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(text("PRAGMA journal_mode=WAL"))).scalar_one() == "wal"
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            state = await seed_claim_rows(db, marketplace=marketplace, managed=managed,
                                          held=held, proven=proven)
            state.factory, state.sent, state.engine, state.token = factory, sent, engine, "SYNTHETIC"
            yield state
    finally:
        await engine.dispose()


@pytest.mark.parametrize("proof", ["result", "provider", "claim_and_attempt"])
def test_future_managed_hold_defers_before_new_claim(tmp_path, monkeypatch, proof):
    async def run():
        async with scenario(tmp_path, monkeypatch) as state:
            result = await executor.execute(**state.args)
            rows = (await state.db.execute(select(ExecutionLog))).scalars().all()
            observed = (result.status, result.log_id is not None, len(state.sent),
                        len(rows), sum(row.attempt_count for row in rows))
            if proof == "result":
                assert (result.status, result.log_id, result.ok) == ("deferred", None, False), observed
            elif proof == "provider":
                assert state.sent == [], observed
            else:
                assert (len(rows), sum(row.attempt_count for row in rows)) == (0, 0), observed
    asyncio.run(run())


def test_cached_success_still_returns_while_held_without_new_dispatch(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            first = await executor.execute(**state.args)
            assert first.status == "success", first.error
            await state.db.execute(update(MarketplaceAccount).where(
                MarketplaceAccount.id == state.account_id).values(ym_review_read_not_before=FUTURE))
            await state.db.commit()
            cached = await executor.execute(**state.args)
            assert cached.status == "success", cached.error
            assert cached.log_id == first.log_id
            assert len(state.sent) == 1
            rows = (await state.db.execute(select(ExecutionLog))).scalars().all()
            assert len(rows) == 1
            assert rows[0].attempt_count == 1
    asyncio.run(run())


def test_verified_account_ref_without_current_key_witness_is_unmanaged(tmp_path, monkeypatch):
    """Status/account_ref are not a provenance proof; legacy use stays available.

    The historical RED above intentionally predates the current-key witness
    contract. Its retained XML must not be relabelled as nonce-proven behavior.
    """
    async def run():
        async with scenario(tmp_path, monkeypatch, managed=True, proven=False) as state:
            credential = (await state.db.execute(select(ApiCredential))).scalars().one()
            assert credential.verification_status == "verified"
            assert credential.meta == {"account_ref": "BIZ1"}
            result = await executor.execute(**state.args)
            assert result.status == "success", result.error
            assert len(state.sent) == 1
            rows = (await state.db.execute(select(ExecutionLog))).scalars().all()
            assert len(rows) == 1
            assert rows[0].attempt_count == 1
    asyncio.run(run())


@pytest.mark.parametrize("loss", ["unverified", "unverified_legacy", "missing_link"])
def test_proven_credential_losing_account_authority_never_becomes_unmanaged(tmp_path, monkeypatch, loss):
    async def run():
        async with scenario(tmp_path, monkeypatch) as state:
            if loss == "missing_link":
                await state.db.execute(update(MarketplaceConnection).where(
                    MarketplaceConnection.id == state.connection_id).values(marketplace_account_id=None))
            else:
                await state.db.execute(update(MarketplaceAccount).where(
                    MarketplaceAccount.id == state.account_id).values(identity_status=loss))
            await state.db.commit()
            result = await executor.execute(**state.args)
            assert result.status == "deferred", result.error
            assert result.log_id is None and result.ok is False
            assert result.error["reason"] == "identity"
            assert state.sent == []
            assert (await state.db.execute(select(ExecutionLog))).scalars().all() == []
            async with state.factory() as observer:
                account = await observer.get(MarketplaceAccount, state.account_id)
                assert account.ym_review_read_not_before is not None
    asyncio.run(run())


@pytest.mark.parametrize("marketplace,managed", [("wildberries", True), ("yandex", False)])
def test_non_yandex_and_unmanaged_publication_remain_unchanged(tmp_path, monkeypatch, marketplace, managed):
    async def run():
        async with scenario(tmp_path, monkeypatch, marketplace=marketplace, managed=managed) as state:
            result = await executor.execute(**state.args)
            assert result.status == "success", result.error
            assert len(state.sent) == 1
            rows = (await state.db.execute(select(ExecutionLog))).scalars().all()
            assert len(rows) == 1
            assert rows[0].attempt_count == 1
    asyncio.run(run())


@asynccontextmanager
async def prepared(state, *, client=None):
    client = yandex_client._api if client is None else client
    connection = await state.db.get(MarketplaceConnection, state.connection_id)
    snapshot = await ym_review_read_hold.load_credential_snapshot(
        state.db, connection_id=connection.id, scope="feedbacks")
    async with ym_review_read_hold.bind_cabinet_context(
            state.db, connection, snapshot, session_factory=state.factory) as binding:
        assert binding is not None
        request = build_publish_feedback_request(business_id="BIZ1", feedback_id="42", text="Synthetic reply")
        intent = ym_review_read_hold.make_write_intent(
            binding, client=client, request=request, token=getattr(state, "token", "SYNTHETIC"))
        yield binding, intent, request, client


def own_log(state):
    return ExecutionLog(
        id=str(uuid.uuid4()), user_id=state.args["user_id"], marketplace="yandex",
        action_type="publish_review_response", mode="manual_l3", status="pending",
        payload=state.args["payload"], idempotency_key=state.args["idempotency_key"],
        connection_id=state.connection_id, request_fingerprint="fp1:" + "a" * 64,
    )


async def mint_permit(state, binding, intent, *, commit=None):
    async with ym_review_read_hold.claim_admission(state.db, binding=binding, intent=intent) as grant:
        await grant.allow_new_claim()
        row = own_log(state)
        state.db.add(row)
        await state.db.flush([row])
        stamp = ym_review_read_hold.ClaimStamp(
            row.id, row.idempotency_key, row.request_fingerprint, row.claim_generation)
        permit = await grant.commit_claim(commit or state.db.commit, stamp=stamp)
        return permit, stamp, row


def test_denial_preserves_unrelated_caller_work_without_commit_or_rollback(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch) as state:
            async with prepared(state) as (binding, intent, _, _):
                await state.db.execute(update(User).where(User.id == state.args["user_id"]).values(name="Pending"))
                with pytest.raises(ym_review_read_hold.YMReviewReadDeferred):
                    async with ym_review_read_hold.claim_admission(state.db, binding=binding, intent=intent) as grant:
                        await grant.allow_new_claim()
                assert (await state.db.execute(select(User.name))).scalar_one() == "Pending"
                async with state.factory() as observer:
                    assert (await observer.execute(select(User.name))).scalar_one() == "Synthetic"
                    assert (await observer.execute(select(ExecutionLog))).scalars().all() == []
                await state.db.rollback()
                assert state.sent == []
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["before_commit", "lost_ack", "cancel", "timeout"])
def test_commit_uncertainty_or_cancel_never_mints_permit(tmp_path, monkeypatch, failure):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            calls = []

            async def commit():
                calls.append(1)
                if failure == "lost_ack":
                    await state.db.commit()
                if failure == "cancel":
                    raise asyncio.CancelledError()
                if failure == "timeout":
                    await asyncio.Event().wait()  # bounded by the real core transaction timeout
                raise ConnectionError("synthetic commit uncertainty")

            expected = asyncio.CancelledError if failure == "cancel" else ym_review_read_hold.YMClaimCommitUncertain
            async with prepared(state) as (binding, intent, _, _):
                with pytest.raises(expected):
                    await mint_permit(state, binding, intent, commit=commit)
            assert calls == [1]
            assert state.sent == []
            await state.db.rollback()
            async with state.factory() as observer:
                rows = (await observer.execute(select(ExecutionLog))).scalars().all()
                assert len(rows) == (1 if failure == "lost_ack" else 0)
                assert all(row.attempt_count == 0 and row.dispatch_started_at is None for row in rows)
    asyncio.run(run())


def test_rotation_after_binding_denies_before_claim(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            async with prepared(state) as (binding, intent, _, _):
                async with state.factory() as writer:
                    await writer.execute(update(ApiCredential).where(ApiCredential.id == state.credential_id).values(
                        secret_enc=credential_vault.encrypt("ROTATED"),
                        meta={"account_ref": "BIZ1", ym_review_read_hold.SECRET_GENERATION_KEY: uuid.uuid4().hex}))
                    await writer.commit()
                with pytest.raises(ym_review_read_hold.YMReviewReadDeferred):
                    async with ym_review_read_hold.claim_admission(state.db, binding=binding, intent=intent) as grant:
                        await grant.allow_new_claim()
                assert (await state.db.execute(select(ExecutionLog))).scalars().all() == []
                assert state.sent == []
    asyncio.run(run())


def test_fence_generation_loss_never_dispatches_or_adopts_new_generation(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            original = state.db.execute

            async def execute(statement, *args, **kwargs):
                if statement is executor._FENCE_CAS:
                    await original(update(ExecutionLog).values(claim_generation=1))
                return await original(statement, *args, **kwargs)

            monkeypatch.setattr(state.db, "execute", execute)
            result = await executor.execute(**state.args)
            assert result.status == "needs_reconcile"
            assert state.sent == []
            rows = (await original(select(ExecutionLog))).scalars().all()
            assert len(rows) == 1
            assert rows[0].claim_generation == 1
            assert rows[0].attempt_count == 0
            assert rows[0].dispatch_started_at is None
    asyncio.run(run())


@pytest.mark.parametrize("status,expected", [(429, "failed"), (503, "ambiguous"), ("timeout", "ambiguous")])
def test_actual_dispatched_refusal_or_ambiguity_never_reopens_key(tmp_path, monkeypatch, status, expected):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False, http_status=status,
                            http_headers={"Retry-After": "3600"} if status == 429 else None) as state:
            result = await executor.execute(**state.args)
            assert result.status == expected, result.error
            assert len(state.sent) == 1
            await state.db.execute(update(MarketplaceAccount).values(
                ym_review_read_not_before=datetime(2000, 1, 1, tzinfo=timezone.utc)))
            await state.db.commit()
            again = await executor.execute(**state.args)
            assert again.status != "success"
            assert len(state.sent) == 1
            rows = (await state.db.execute(select(ExecutionLog))).scalars().all()
            assert len(rows) == 1 and rows[0].attempt_count == 1
            assert rows[0].dispatch_started_at is not None
    asyncio.run(run())


def test_unique_insert_loser_never_mints_permit_or_dispatches(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            existing = own_log(state)
            state.db.add(existing)
            await state.db.commit()
            existing_id = existing.id
            async with prepared(state) as (binding, intent, _, _):
                with pytest.raises(IntegrityError):
                    await mint_permit(state, binding, intent)
            await state.db.rollback()
            rows = (await state.db.execute(select(ExecutionLog))).scalars().all()
            assert [row.id for row in rows] == [existing_id]
            assert rows[0].attempt_count == 0
            assert state.sent == []
    asyncio.run(run())


def test_unrelated_new_execution_log_rejected_without_implicit_flush(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            async with prepared(state) as (binding, intent, _, _):
                pending = own_log(state)
                state.db.add(pending)
                with pytest.raises(ym_review_read_hold.YMReviewReadDeferred):
                    async with ym_review_read_hold.claim_admission(state.db, binding=binding, intent=intent):
                        pytest.fail("unrelated claim state was admitted")
                assert pending in state.db.new
                async with state.factory() as observer:
                    assert (await observer.execute(select(ExecutionLog))).scalars().all() == []
                assert state.sent == []
                await state.db.rollback()
    asyncio.run(run())


def install_rollback_fault(state, monkeypatch, mode):
    """Fault only the actual caller connection's savepoint, not the public API."""
    original = state.db.connection
    entered = []

    class Savepoint:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        async def commit(self):
            return await self.inner.commit()

        async def rollback(self):
            entered.append(mode)
            if mode == "stall":
                await asyncio.Event().wait()
            if mode == "cancel":
                raise asyncio.CancelledError()
            raise ConnectionError("synthetic savepoint rollback failed")

    class Connection:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        async def begin_nested(self):
            return Savepoint(await self.inner.begin_nested())

    async def connection(*args, **kwargs):
        return Connection(await original(*args, **kwargs))

    monkeypatch.setattr(state.db, "connection", connection)
    return entered


@pytest.mark.parametrize("phase", ["hold_denial", "authority_timeout"])
@pytest.mark.parametrize("fault", ["stall", "failure", "cancel"])
def test_uncertain_cleanup_is_bounded_and_caller_cannot_later_commit_work(tmp_path, monkeypatch, phase, fault):
    async def run():
        async with scenario(tmp_path, monkeypatch) as state:
            await state.db.execute(update(User).values(name="Must not leak after uncertainty"))
            rollbacks = install_rollback_fault(state, monkeypatch, fault)
            if phase == "authority_timeout":
                async def locked_forever(*args, **kwargs):
                    await asyncio.Event().wait()
                monkeypatch.setattr(ym_review_read_hold, "_locked_authority", locked_forever)
            # Core's authority and cleanup budgets must both finish before this outer
            # safety bound. TimeoutError here is a failure, never an accepted outcome.
            async with asyncio.timeout(7):
                try:
                    await executor.execute(**state.args)
                except asyncio.CancelledError:
                    assert fault == "cancel"
                except ym_review_read_hold.YMClaimCommitUncertain as exc:
                    assert exc.transaction_unusable is True
                else:
                    pytest.fail("uncertain caller transaction must propagate, not return a reusable result")
            assert rollbacks
            assert state.sent == []
            # Deliberately do NOT repair the caller session with rollback. A normal
            # API consumer might attempt its next commit; uncertain prior work must
            # already have been discarded/invalidated by the production boundary.
            with pytest.raises((ym_review_read_hold.YMClaimCommitUncertain, InvalidRequestError)):
                await state.db.commit()
            with pytest.raises((ym_review_read_hold.YMClaimCommitUncertain, InvalidRequestError)):
                await state.db.execute(select(User.name))
            async with state.factory() as observer:
                assert (await observer.execute(select(User.name))).scalar_one() == "Synthetic"
                assert (await observer.execute(select(ExecutionLog))).scalars().all() == []
                # Also prove no residual writer lock can strand a later worker.
                await observer.execute(update(MarketplaceAccount).values(label="Available after cleanup"))
                await observer.commit()
    asyncio.run(run())
