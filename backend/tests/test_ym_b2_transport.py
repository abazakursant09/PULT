"""First permit consumption through actual YM transport; no real HTTP or DB."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import uuid

import httpx
import pytest
from sqlalchemy import event, select, update

from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.user import User
from models.workspace import Workspace
from services.marketplace import credential_vault, executor
from services.marketplace import ym_review_read_hold as T
from services.marketplace.yandex_client import yandex_client
from test_ym_b2_claim_admission import FUTURE, mint_permit, prepared, scenario
from test_marketplace_retry_policy import no_external_io  # noqa: F401


async def fence(state, stamp):
    row = (await state.db.execute(executor._FENCE_CAS, {
        "id": stamp.log_id, "now": FUTURE, "gen": stamp.claim_generation})).first()
    await state.db.commit()
    assert row is not None


async def attempt(client, request, *, token="SYNTHETIC", number=1):
    await T.admit_yandex_attempt(client, token=token, attempt=number, **request)


def test_pre_admitted_first_send_survives_later_hold_with_zero_sql_in_fence_gap(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            async with prepared(state) as (binding, intent, _, _):
                permit, stamp, _ = await mint_permit(state, binding, intent)
                async with state.factory() as writer:
                    await writer.execute(update(MarketplaceAccount).values(ym_review_read_not_before=FUTURE))
                    await writer.commit()
                await fence(state, stamp)
                sql = []

                def record(*args):
                    sql.append(1)

                event.listen(state.engine.sync_engine, "before_cursor_execute", record)
                try:
                    with T.activate_first_write(permit, stamp=stamp):
                        await asyncio.sleep(0)  # invocation scheduling yield, not a TTL
                        await yandex_client.publish_feedback_answer(
                            token="SYNTHETIC", business_id="BIZ1", feedback_id="42", text="Synthetic reply")
                    assert sql == []
                finally:
                    event.remove(state.engine.sync_engine, "before_cursor_execute", record)
                assert len(state.sent) == 1
                assert permit.used is True
                assert permit.active is False
    asyncio.run(run())


@pytest.mark.parametrize("tamper", ["payload", "token", "client", "operation", "stamp", "task", "retry"])
def test_permit_rejects_request_identity_task_generation_and_retry_reuse(tmp_path, monkeypatch, tamper):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            async with prepared(state) as (binding, intent, request, client):
                permit, stamp, _ = await mint_permit(state, binding, intent)
                await fence(state, stamp)
                selected_stamp = replace(stamp, claim_generation=stamp.claim_generation + 1) if tamper == "stamp" else stamp

                async def consume():
                    target = object() if tamper == "client" else client
                    operation = "comments" if tamper == "operation" else "publish_feedback_answer"
                    changed = dict(request)
                    if tamper == "payload":
                        changed["json"] = {"feedbackId": 42, "comment": {"text": "SWAPPED"}}
                    with T.selected_yandex_operation(operation, client=target, business_id="BIZ1"):
                        await attempt(target, changed, token="SWAPPED" if tamper == "token" else "SYNTHETIC",
                                      number=2 if tamper == "retry" else 1)

                with pytest.raises(T.YMWritePermitError):
                    with T.activate_first_write(permit, stamp=selected_stamp):
                        if tamper == "task":
                            await asyncio.create_task(consume())
                        else:
                            await consume()
                assert state.sent == []
    asyncio.run(run())


def test_single_use_cannot_be_reactivated_or_reused_in_same_dispatch(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            async with prepared(state) as (binding, intent, request, client):
                permit, stamp, _ = await mint_permit(state, binding, intent)
                await fence(state, stamp)
                with T.activate_first_write(permit, stamp=stamp):
                    with T.selected_yandex_operation("publish_feedback_answer", client=client, business_id="BIZ1"):
                        await attempt(client, request)
                        with pytest.raises(T.YMWritePermitError):
                            await attempt(client, request)
                with pytest.raises(T.YMWritePermitError):
                    with T.activate_first_write(permit, stamp=stamp):
                        pytest.fail("used permit reactivated")
    asyncio.run(run())


def test_forged_or_new_invocation_permit_cannot_dispatch(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            async with prepared(state) as (binding, intent, _, _):
                permit, stamp, _ = await mint_permit(state, binding, intent)
                with pytest.raises(T.YMWritePermitError):
                    with T.activate_first_write(T.FirstPermit(intent, stamp), stamp=stamp):
                        pytest.fail("forged permit active")
            async with prepared(state):
                with pytest.raises(T.YMWritePermitError):
                    with T.activate_first_write(permit, stamp=stamp):
                        pytest.fail("old invocation permit active")
            assert state.sent == []
    asyncio.run(run())


@pytest.mark.parametrize("headers,reason", [
    ({"Retry-After": "3600"}, "hold"),
    ([("Retry-After", "3600"), ("Retry-After", "3601")], "indeterminate"),
])
def test_raw_write_refusal_observation_persists_then_stops_without_retry(tmp_path, monkeypatch, headers, reason):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            async with prepared(state) as (binding, intent, request, client):
                permit, stamp, _ = await mint_permit(state, binding, intent)
                await fence(state, stamp)
                with T.activate_first_write(permit, stamp=stamp):
                    with T.selected_yandex_operation("publish_feedback_answer", client=client, business_id="BIZ1"):
                        await attempt(client, request)
                        with pytest.raises(T.YMReviewReadDeferred) as caught:
                            await T.observe_yandex_response(client, httpx.Response(429, headers=headers))
                assert caught.value.reason == reason
                async with state.factory() as observer:
                    row = await observer.get(MarketplaceAccount, state.account_id)
                    assert row.ym_review_read_not_before is not None or row.ym_review_read_indeterminate
                assert state.sent == []
    asyncio.run(run())


def test_unadmitted_response_cannot_create_a_cabinet_hold(tmp_path, monkeypatch):
    async def run():
        async with scenario(tmp_path, monkeypatch, held=False) as state:
            async with prepared(state) as (binding, intent, _, client):
                permit, stamp, _ = await mint_permit(state, binding, intent)
                await fence(state, stamp)
                with T.activate_first_write(permit, stamp=stamp):
                    with T.selected_yandex_operation("publish_feedback_answer", client=client, business_id="BIZ1"):
                        with pytest.raises(T.YMReviewReadDeferred) as caught:
                            await T.observe_yandex_response(client, httpx.Response(429, headers={"Retry-After": "3600"}))
                assert caught.value.reason == "identity"
                async with state.factory() as observer:
                    row = await observer.get(MarketplaceAccount, state.account_id)
                    assert row.ym_review_read_not_before is None
                    assert row.ym_review_read_indeterminate is False
                assert state.sent == []
    asyncio.run(run())


async def rotate_key(state, *, change=None):
    async with state.factory() as writer:
        credential = await writer.get(ApiCredential, state.credential_id)
        nonce = uuid.uuid4().hex
        credential.secret_enc = credential_vault.encrypt("ROTATED")
        credential.meta = {"account_ref": "BIZ1", T.SECRET_GENERATION_KEY: nonce,
            T.CABINET_PROOF_KEY: {"version": 1, "generation": nonce,
                "credential_id": credential.id, "account_id": state.account_id, "business_id": "BIZ1"}}
        if change:
            uid, wid, aid = [str(uuid.uuid4()) for _ in range(3)]
            writer.add(User(id=uid, email=f"{uid}@example.invalid", name="Other", hashed_password="x"))
            writer.add(Workspace(id=wid, owner_user_id=uid))
            await writer.flush()
            if change == "owner":
                await writer.execute(update(MarketplaceAccount).where(
                    MarketplaceAccount.id == state.account_id).values(workspace_id=wid))
            else:
                writer.add(MarketplaceAccount(id=aid, workspace_id=wid, marketplace="yandex",
                    external_account_id="BIZ2", identity_status="verified"))
                await writer.flush()
                await writer.execute(update(MarketplaceConnection).where(
                    MarketplaceConnection.id == state.connection_id).values(
                        marketplace_account_id=aid, workspace_id=wid, user_id=uid))
        await writer.commit()


def test_admitted_key_a_real_429_after_rotation_b_persists_hold_for_next_binding(tmp_path, monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async with scenario(tmp_path, monkeypatch, held=False, http_status=429,
                            http_headers={"Retry-After": "7200"}, http_barrier=(entered, release)) as state:
            # Executor and its binding belong to this child task; parent never
            # shares its DB session while HTTP is suspended.
            task = asyncio.create_task(executor.execute(**state.args))
            try:
                async with asyncio.timeout(4):
                    await entered.wait()
                    await rotate_key(state)
                    release.set()
                    result = await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert result.status == "failed", result.error
            assert len(state.sent) == 1
            async with state.factory() as observer:
                account = await observer.get(MarketplaceAccount, state.account_id)
                until = account.ym_review_read_not_before
                assert until is not None, "received key-A refusal was discarded after key-B rotation"
                until = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until
                assert until > datetime.now(timezone.utc) + timedelta(seconds=7100)
                row = (await observer.execute(select(ExecutionLog))).scalars().one()
                assert row.attempt_count == 1 and row.dispatch_started_at is not None
            state.token = "ROTATED"
            async with prepared(state) as (binding, intent, _, _):
                with pytest.raises(T.YMReviewReadDeferred) as caught:
                    async with T.claim_admission(state.db, binding=binding, intent=intent) as grant:
                        await grant.allow_new_claim()
                assert caught.value.reason == "hold"
            assert len(state.sent) == 1
    asyncio.run(run())


@pytest.mark.parametrize("change", ["owner", "relink"])
def test_inflight_refusal_cannot_be_attributed_to_changed_owner_or_other_cabinet(tmp_path, monkeypatch, change):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async with scenario(tmp_path, monkeypatch, held=False, http_status=429,
                            http_headers={"Retry-After": "7200"}, http_barrier=(entered, release)) as state:
            task = asyncio.create_task(executor.execute(**state.args))
            try:
                async with asyncio.timeout(4):
                    await entered.wait()
                    await rotate_key(state, change=change)
                    release.set()
                    result = await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert result.status == "failed", result.error
            assert len(state.sent) == 1
            async with state.factory() as observer:
                accounts = (await observer.execute(select(MarketplaceAccount))).scalars().all()
                # Changed ownership/mapping is not permission to max a header
                # onto the guessed current cabinet or the transferred account.
                targets = accounts if change == "owner" else [
                    row for row in accounts if row.id != state.account_id]
                assert targets
                assert all(row.ym_review_read_not_before is None for row in targets)
                assert all(not row.ym_review_read_indeterminate for row in targets)
    asyncio.run(run())
