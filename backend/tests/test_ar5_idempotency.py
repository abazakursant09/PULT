"""AR5 — execution idempotency hardening.

A marketplace write that may have committed but returned TIMEOUT/5XX is recorded as `ambiguous`
and NEVER auto-repeated: a second call with the same idempotency key returns needs_reconcile
without re-dispatching. Clean failures (AUTH/VALIDATION/RATE_LIMIT) stay `failed`/`rejected` and
remain retryable. Success idempotency is unchanged. The change is additive to the shared executor,
so pricing/ads paths are unaffected.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog  # noqa: F401
import models  # register tables
from services.marketplace import executor, credential_vault
from services.marketplace.wb_client import wb_client
from services.marketplace.errors import ExecutionError


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _setup(scope="feedbacks"):
    engine = create_async_engine("sqlite+aiosqlite://",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    uid = str(uuid.uuid4())
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace="wildberries",
                                 status="connected", scopes=[scope] if scope else [])
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return db, uid


def _raiser(code, counter):
    async def _fake(*, token, feedback_id, text):
        counter["calls"] += 1
        raise ExecutionError(code, "boom")
    return _fake


def _ok(counter):
    async def _fake(*, token, feedback_id, text):
        counter["calls"] += 1
        return {"api_request_id": "req-1"}
    return _fake


def _publish(db, uid, key="review:x"):
    return executor.execute(
        db=db, user_id=uid, action_type="publish_review_response",
        payload={"marketplace": "wildberries", "feedback_id": "fb1", "text": "Спасибо!", "rating": 5},
        idempotency_key=key,
    )


# ── 1-2. TIMEOUT / 5XX → ambiguous ───────────────────────────────────────────

def test_timeout_is_ambiguous():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _raiser(ExecutionError.TIMEOUT, c)
        res = await _publish(db, uid)
        assert res.status == "ambiguous" and res.error["code"] == "TIMEOUT"
    _run(go())


def test_5xx_is_ambiguous():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _raiser(ExecutionError.MARKETPLACE_5XX, c)
        res = await _publish(db, uid)
        assert res.status == "ambiguous"
    _run(go())


# ── 3. No second dispatch after ambiguous → needs_reconcile ──────────────────

def test_ambiguous_then_retry_does_not_redispatch():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _raiser(ExecutionError.TIMEOUT, c)
        r1 = await _publish(db, uid, key="review:1")
        assert r1.status == "ambiguous" and c["calls"] == 1
        # retry with the SAME key — even if the marketplace would now answer, we must not call it
        wb_client.publish_feedback_answer = _ok(c)
        r2 = await _publish(db, uid, key="review:1")
        assert r2.status == "needs_reconcile"
        assert r2.ok is False
        assert c["calls"] == 1          # NO second marketplace call
    _run(go())


# ── 4. Success idempotency unchanged ─────────────────────────────────────────

def test_success_idempotency_unchanged():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _ok(c)
        r1 = await _publish(db, uid, key="review:s")
        r2 = await _publish(db, uid, key="review:s")
        assert r1.status == "success" and r2.status == "success"
        assert c["calls"] == 1          # second returns prior success, no re-call
    _run(go())


# ── 5-6. Clean failures remain failed / retryable ────────────────────────────

def test_auth_remains_failed_and_retryable():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _raiser(ExecutionError.AUTH, c)
        r1 = await _publish(db, uid, key="review:a")
        assert r1.status == "failed" and r1.error["code"] == "AUTH"
        # same key retried — AUTH is clean, so it IS re-dispatched (not suppressed)
        r2 = await _publish(db, uid, key="review:a")
        assert r2.status == "failed" and c["calls"] == 2
    _run(go())


def test_rate_limit_is_not_ambiguous():
    async def go():
        db, uid = await _setup()
        c = {"calls": 0}
        wb_client.publish_feedback_answer = _raiser(ExecutionError.RATE_LIMIT, c)
        res = await _publish(db, uid, key="review:r")
        assert res.status == "failed"                    # 429 is clean, not ambiguous
        assert not ExecutionError.is_ambiguous_error("RATE_LIMIT")
    _run(go())


def test_classifier():
    assert ExecutionError.is_ambiguous_error("TIMEOUT")
    assert ExecutionError.is_ambiguous_error("MARKETPLACE_5XX")
    for clean in ("RATE_LIMIT", "AUTH", "VALIDATION", "MARKETPLACE_4XX",
                  "NO_CONNECTION", "CAPABILITY_NOT_SUPPORTED"):
        assert not ExecutionError.is_ambiguous_error(clean)


# ── 7. auto_publish never retries an ambiguous result ────────────────────────

def test_auto_publish_marks_ambiguous_terminal(monkeypatch):
    import tasks.auto_publish_reviews as ap
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from datetime import datetime
    from models.product import Product
    from models.user import User
    from models.review_response import ReviewResponse
    from models.automation_rule import AutomationRule
    from config import settings

    async def go():
        db, uid = await _setup()
        db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
        p = Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="NM", marketplace="wildberries")
        db.add(p)
        r = ReviewResponse(id=str(uuid.uuid4()), product_id=p.id, marketplace="wildberries",
                           external_review_id="WB-1", rating=5, safety_category="SAFE",
                           response_text="Спасибо!", status="approved")
        db.add(r)
        db.add(AutomationRule(id=str(uuid.uuid4()), user_id=uid, contour="reputation",
                              action_type="publish_review_response", mode="auto", enabled=True, guard={}))
        await db.commit()

        monkeypatch.setattr(settings, "automation_enabled", True)

        @asynccontextmanager
        async def _factory():
            yield db
        monkeypatch.setattr(ap, "AsyncSessionLocal", _factory)

        calls = {"n": 0}
        async def _fake_exec(**kw):
            calls["n"] += 1
            return SimpleNamespace(ok=False, status="ambiguous",
                                   error={"code": "TIMEOUT"}, log_id="lg")
        monkeypatch.setattr(ap.executor, "execute", _fake_exec)

        out = await ap.run_auto_publish_reviews()
        assert out["terminal"] == 1 and calls["n"] == 1
        fresh = await db.get(ReviewResponse, r.id)
        assert fresh.status == "failed"                      # terminal, not re-selected
        assert fresh.retry_next_at is None
        assert "не подтверждена" in fresh.failure_reason

        # a second tick must NOT re-attempt (row is terminal / not publishable)
        out2 = await ap.run_auto_publish_reviews()
        assert calls["n"] == 1

    _run(go())
