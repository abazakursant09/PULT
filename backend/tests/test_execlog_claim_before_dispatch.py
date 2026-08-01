"""SECURITY-2D-1B-B — claim-before-dispatch on SQLite (single-process conflict + resolve paths).

True cross-connection concurrency is proven on real PostgreSQL (test_execlog_claim_pg.py). Here we
prove the deterministic claim/resolve logic, operation-key enforcement, fingerprint mismatch, in_flight
+ dispatch_started_at, and the legacy-alias guard. The dispatch stub counts REAL provider calls.
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  register tables
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
from services.marketplace import executor, credential_vault, operation_key
from services.marketplace.wb_client import wb_client
from services.marketplace.errors import ExecutionError


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite://",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    uid = str(uuid.uuid4())
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
                                 status="connected", scopes=["feedbacks"])
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return db, uid


def _ok(counter):
    async def _fake(*, token, feedback_id, text):
        counter["calls"] += 1
        return {"api_request_id": "req"}
    return _fake


def _raiser(code, counter):
    async def _fake(*, token, feedback_id, text):
        counter["calls"] += 1
        raise ExecutionError(code, "boom")
    return _fake


def _publish(db, uid, key, text="Спасибо!"):
    return executor.execute(
        db=db, user_id=uid, action_type="publish_review_response",
        payload={"marketplace": "wildberries", "feedback_id": "fb1", "text": text, "rating": 5},
        idempotency_key=key,
    )


def _vkey():
    return operation_key.review_key(str(uuid.uuid4()))


def test_valid_v1_key_dispatches_once_and_sets_in_flight_fields():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _ok(c)
        key = _vkey()
        res = await _publish(db, uid, key)
        assert res.status == "success" and c["calls"] == 1
        row = (await db.execute(select(ExecutionLog).where(
            ExecutionLog.idempotency_key == key))).scalars().first()
        assert row.status == "success"
        assert row.request_fingerprint and row.request_fingerprint.startswith("fp1:")
        assert row.dispatch_started_at is not None      # stamped before dispatch
    _run(go())


def test_missing_key_rejected_no_dispatch():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _ok(c)
        res = await _publish(db, uid, None)
        assert res.status == "rejected" and c["calls"] == 0
        assert res.error["code"] == "GUARD_OPERATION_KEY_REQUIRED"
    _run(go())


def test_legacy_format_key_rejected_no_dispatch():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _ok(c)
        res = await _publish(db, uid, "review:legacy")   # not a v1 key
        assert res.status == "rejected" and c["calls"] == 0
    _run(go())


def test_same_key_same_payload_cached_no_second_dispatch():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _ok(c)
        key = _vkey()
        r1 = await _publish(db, uid, key)
        r2 = await _publish(db, uid, key)               # conflict → resolve cached
        assert r1.status == "success" and r2.status == "success"
        assert c["calls"] == 1
    _run(go())


def test_same_key_different_payload_is_mismatch_no_dispatch():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _ok(c)
        key = _vkey()
        r1 = await _publish(db, uid, key, text="one")
        r2 = await _publish(db, uid, key, text="TWO")   # same key, different contents
        assert r1.status == "success"
        assert r2.status == "needs_reconcile"
        assert r2.error["code"] == "IDEMPOTENCY_MISMATCH"
        assert c["calls"] == 1                          # 0 second dispatch
    _run(go())


def test_prior_failed_same_key_blocked_no_redispatch():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _raiser(ExecutionError.AUTH, c)
        key = _vkey()
        r1 = await _publish(db, uid, key)
        assert r1.status == "failed" and c["calls"] == 1
        # 1B-B never auto-retries a failed claim (controlled re-own is 1C)
        wb_client.publish_feedback_answer = _ok(c)
        r2 = await _publish(db, uid, key)
        assert r2.status == "needs_reconcile"
        assert r2.error["code"] == "PRIOR_FAILED"
        assert c["calls"] == 1                          # NO second dispatch
    _run(go())


def test_ambiguous_prior_same_key_needs_reconcile():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _raiser(ExecutionError.TIMEOUT, c)
        key = _vkey()
        r1 = await _publish(db, uid, key)
        assert r1.status == "ambiguous" and c["calls"] == 1
        r2 = await _publish(db, uid, key)
        assert r2.status == "needs_reconcile" and c["calls"] == 1
    _run(go())


def test_legacy_review_alias_blocks_regardless_of_status():
    async def go():
        for legacy_status in ("pending", "success", "failed", "ambiguous", "rejected", "reverted"):
            db, uid = await _setup()
            c = {"calls": 0}
            wb_client.publish_feedback_answer = _ok(c)
            rid = str(uuid.uuid4())
            db.add(ExecutionLog(id=str(uuid.uuid4()), user_id=uid,
                                action_type="publish_review_response", mode="manual_l3",
                                payload={}, status=legacy_status,
                                idempotency_key="review:" + rid))
            await db.commit()
            res = await _publish(db, uid, operation_key.review_key(rid))
            assert res.status == "needs_reconcile", legacy_status
            assert res.error["code"] == "LEGACY_OPERATION_NEEDS_RECONCILE"
            assert c["calls"] == 0, legacy_status       # never dispatched
    _run(go())


def test_legacy_decision_alias_blocks_by_decision_id():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _ok(c)
        did = str(uuid.uuid4())
        # a legacy decision-apply row: decision_id set, opaque (non-v1) key
        db.add(ExecutionLog(id=str(uuid.uuid4()), user_id=uid,
                            action_type="publish_review_response", mode="manual_l3",
                            payload={}, status="success", decision_id=did,
                            idempotency_key=str(uuid.uuid4())))
        await db.commit()
        res = await _publish(db, uid, operation_key.decision_key(did))
        assert res.status == "needs_reconcile"
        assert res.error["code"] == "LEGACY_OPERATION_NEEDS_RECONCILE"
        assert c["calls"] == 0
    _run(go())


def test_new_v1_op_is_not_blocked_by_content_legacy_row():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _ok(c)
        # a legacy content-derived key (price:) must NOT block a fresh, unrelated v1 client op
        db.add(ExecutionLog(id=str(uuid.uuid4()), user_id=uid, action_type="set_price",
                            mode="manual_l3", payload={}, status="success",
                            idempotency_key="price:p:100"))
        await db.commit()
        res = await _publish(db, uid, operation_key.client_key(str(uuid.uuid4())))
        assert res.status == "success" and c["calls"] == 1
    _run(go())
