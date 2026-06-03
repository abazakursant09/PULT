"""
Functional tests for the Marketplace Execution Layer executor (ME-1/ME-2).

Real async path against an in-memory SQLite; the WB HTTP call is mocked at the
client boundary, so we exercise resolve → scope → validate → guard →
log-pending → dispatch → persist without touching the network.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog          # noqa: F401 (register table)
from models.automation_rule import AutomationRule      # noqa: F401
from services.marketplace import executor, credential_vault
from services.marketplace.wb_client import wb_client


def _run(coro):
    return asyncio.run(coro)


async def _setup(*, scope="feedbacks"):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db = Session()
    uid = str(uuid.uuid4())
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
        status="connected", scopes=[scope] if scope else [],
    )
    db.add(conn)
    await db.flush()
    db.add(ApiCredential(
        id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
        secret_enc=credential_vault.encrypt("fake-wb-token"), meta={},
    ))
    await db.commit()
    return db, uid


def _mock_publish(monkeypatch_counter):
    async def _fake(*, token, feedback_id, text):
        monkeypatch_counter["calls"] += 1
        monkeypatch_counter["last_token"] = token
        return {"requestId": "req-123"}
    return _fake


# ── L3 happy path ───────────────────────────────────────────────────────────
def test_l3_publish_success():
    async def go():
        db, uid = await _setup()
        counter = {"calls": 0}
        wb_client.publish_feedback_answer = _mock_publish(counter)
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"feedback_id": "fb1", "text": "Спасибо за отзыв!", "rating": 5},
        )
        assert res.status == "success", res.error
        assert res.api_request_id == "req-123"
        assert counter["calls"] == 1
        assert counter["last_token"] == "fake-wb-token"   # vault decrypt worked
    _run(go())


# ── guard: never auto-publish a non-positive review (L4) ──────────────────────
def test_guard_blocks_negative_auto_publish():
    async def go():
        db, uid = await _setup()
        wb_client.publish_feedback_answer = _mock_publish({"calls": 0})
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"feedback_id": "fb1", "text": "ответ", "rating": 2},
            mode="automated_l4",
            rule={"enabled": True, "guard": {}},
        )
        assert res.status == "rejected"
        assert res.error["code"] == "GUARD_NEGATIVE_NEVER_AUTO"
    _run(go())


# ── L4 requires an enabled rule ───────────────────────────────────────────────
def test_l4_requires_rule():
    async def go():
        db, uid = await _setup()
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"feedback_id": "fb1", "text": "ответ", "rating": 5},
            mode="automated_l4", rule=None,
        )
        assert res.status == "rejected"
        assert res.error["code"] == "GUARD_NO_ACTIVE_RULE"
    _run(go())


# ── scope enforcement ─────────────────────────────────────────────────────────
def test_missing_scope_rejected():
    async def go():
        db, uid = await _setup(scope="prices")  # no feedbacks scope
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"feedback_id": "fb1", "text": "ответ", "rating": 5},
        )
        assert res.status == "rejected"
        assert res.error["code"] == "MISSING_SCOPE"
    _run(go())


# ── validation ────────────────────────────────────────────────────────────────
def test_validation_missing_text():
    async def go():
        db, uid = await _setup()
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"feedback_id": "fb1", "rating": 5},
        )
        assert res.status == "rejected"
        assert res.error["code"] == "VALIDATION"
    _run(go())


# ── idempotency: same key → single API call ──────────────────────────────────
def test_idempotency_dedupes():
    async def go():
        db, uid = await _setup()
        counter = {"calls": 0}
        wb_client.publish_feedback_answer = _mock_publish(counter)
        kw = dict(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"feedback_id": "fb1", "text": "ответ", "rating": 5},
            idempotency_key="review:abc",
        )
        r1 = await executor.execute(**kw)
        r2 = await executor.execute(**kw)
        assert r1.status == "success" and r2.status == "success"
        assert counter["calls"] == 1   # second call served from prior log
    _run(go())


# ── dry_run: no dispatch, no log ──────────────────────────────────────────────
def test_dry_run_no_side_effects():
    async def go():
        db, uid = await _setup()
        counter = {"calls": 0}
        wb_client.publish_feedback_answer = _mock_publish(counter)
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"feedback_id": "fb1", "text": "ответ", "rating": 5},
            dry_run=True,
        )
        assert res.status == "dry_run_ok"
        assert res.log_id is None
        assert counter["calls"] == 0
    _run(go())


# ── unknown action ────────────────────────────────────────────────────────────
def test_unknown_action():
    async def go():
        db, uid = await _setup()
        try:
            await executor.execute(
                db=db, user_id=uid, action_type="nope", payload={},
            )
            assert False, "should have raised"
        except Exception as e:
            assert "UNKNOWN_ACTION" in str(e) or "no such action" in str(e)
    _run(go())
