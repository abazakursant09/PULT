"""R-OZ2 — generalized review publish routing.

publish_review_response no longer hardwires Wildberries. The dispatcher routes by the review's
marketplace through the provider registry (no if/elif), and the executor capability gate uses
availability() so a marketplace PULT hasn't built (pult_supported=false) fails closed before any
write. WB behaviour is unchanged; safety (SAFE-only, negative-never-auto, AR5 ambiguous) is untouched.
"""
import asyncio
import inspect
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
import models  # noqa: F401  register tables
from services.marketplace import executor, credential_vault, action_catalog
from services.marketplace import action_catalog as ac
from services.marketplace.wb_client import wb_client
from services.marketplace.errors import ExecutionError


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── unit: credential shaping is a dict, not a branch ─────────────────────────

def test_review_credential_shaping():
    assert ac._review_credential("wildberries", "TOK", {}) == "TOK"          # identity default
    assert ac._review_credential("ozon", "APIKEY", {"ozon_client_id": "CID"}) == "CID:APIKEY"


def test_dispatcher_has_no_marketplace_branch():
    src = inspect.getsource(ac._dispatch_publish_review)
    # no if/elif on a marketplace name — routing is a registry lookup
    for literal in ('== "wildberries"', "== 'wildberries'", '== "ozon"', "== 'ozon'",
                    '== "yandex"', 'if marketplace =='):
        assert literal not in src
    assert "get_review_provider(" in src


# ── unit: dispatcher routes to the marketplace's provider ────────────────────

def test_dispatch_routes_to_wb_provider(monkeypatch):
    seen = {}
    async def fake_wb_publish(*, token, feedback_id, text):
        seen.update(token=token, feedback_id=feedback_id, text=text)
        return {"requestId": "req-wb"}
    monkeypatch.setattr(wb_client, "publish_feedback_answer", fake_wb_publish)

    out = _run(ac._dispatch_publish_review(
        "WBTOKEN", {"feedback_id": "fb1", "text": "Спасибо!"}, {"marketplace": "wildberries"}))
    assert out == {"api_request_id": "req-wb", "published": True, "feedback_id": "fb1"}
    assert seen == {"token": "WBTOKEN", "feedback_id": "fb1", "text": "Спасибо!"}   # WB token as-is


def test_dispatch_routes_to_named_provider_not_wb(monkeypatch):
    # a stub provider registered for a test marketplace receives the call; wb_client must NOT.
    class _Stub:
        def __init__(self): self.calls = []
        def supports_reviews(self): return True
        async def publish_answer(self, token, external_review_id, text):
            self.calls.append((token, external_review_id, text)); return {"requestId": "req-oz"}
    stub = _Stub()
    monkeypatch.setattr(ac, "get_review_provider", lambda mp: stub if mp == "ozon" else None)
    wb_called = {"n": 0}
    async def fake_wb(*a, **k): wb_called["n"] += 1; return {}
    monkeypatch.setattr(wb_client, "publish_feedback_answer", fake_wb)

    out = _run(ac._dispatch_publish_review(
        "APIKEY", {"feedback_id": "r1", "text": "hi"}, {"marketplace": "ozon", "ozon_client_id": "CID"}))
    assert out["api_request_id"] == "req-oz" and out["feedback_id"] == "r1"
    assert stub.calls == [("CID:APIKEY", "r1", "hi")]     # composite credential built
    assert wb_called["n"] == 0                            # WB client never touched


def test_dispatch_unsupported_marketplace_rejects(monkeypatch):
    monkeypatch.setattr(ac, "get_review_provider", lambda mp: None)
    with pytest.raises(ExecutionError) as e:
        _run(ac._dispatch_publish_review("t", {"feedback_id": "r", "text": "x"}, {"marketplace": "yandex"}))
    assert e.value.code == ExecutionError.CAPABILITY_NOT_SUPPORTED


# ── integration: executor gate fails closed for un-shipped marketplace ───────

async def _setup(marketplace="wildberries", scope="feedbacks"):
    engine = create_async_engine("sqlite+aiosqlite://",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    uid = str(uuid.uuid4())
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                 status="connected", scopes=[scope], ozon_client_id="CID")
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return db, uid


def test_ozon_publish_fails_closed_capability(monkeypatch):
    # Ozon review_reply pult_supported=false → availability().available is False → rejected before
    # any dispatch, even though a real OzonReviewProvider exists.
    async def go():
        db, uid = await _setup(marketplace="ozon")
        called = {"n": 0}
        async def fake_wb(*a, **k): called["n"] += 1; return {}
        monkeypatch.setattr(wb_client, "publish_feedback_answer", fake_wb)
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"marketplace": "ozon", "feedback_id": "fb1", "text": "hi", "rating": 5},
            idempotency_key="review:oz")
        assert res.status == "rejected"
        assert res.error["code"] == ExecutionError.CAPABILITY_NOT_SUPPORTED
        assert called["n"] == 0                      # no dispatch of any kind
    _run(go())


def test_wb_publish_still_dispatches(monkeypatch):
    async def go():
        db, uid = await _setup(marketplace="wildberries")
        seen = {}
        async def fake_wb(*, token, feedback_id, text):
            seen.update(feedback_id=feedback_id, text=text); return {"requestId": "req-1"}
        monkeypatch.setattr(wb_client, "publish_feedback_answer", fake_wb)
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"marketplace": "wildberries", "feedback_id": "fb1", "text": "Спасибо!", "rating": 5},
            idempotency_key="review:wb")
        assert res.status == "success"
        assert seen == {"feedback_id": "fb1", "text": "Спасибо!"}
    _run(go())
