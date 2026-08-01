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
from services.marketplace import executor, credential_vault, action_catalog, operation_key
from services.marketplace import action_catalog as ac
from services.marketplace.wb_client import wb_client
from services.marketplace.errors import ExecutionError


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── unit: credential shaping is a dict, not a branch ─────────────────────────

def test_review_credential_shaping():
    from services.marketplace.reviews import review_credential
    assert review_credential("wildberries", "TOK", {}) == "TOK"          # identity default
    assert review_credential("ozon", "APIKEY", {"ozon_client_id": "CID"}) == "CID:APIKEY"


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
    # comment_status/comment_id are None for WB: it does not moderate seller replies, so there is
    # no moderation verdict to report and the caller treats the reply as live.
    assert out == {"api_request_id": "req-wb", "published": True, "feedback_id": "fb1",
                   "comment_status": None, "comment_id": None}
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


def test_ozon_publish_routes_and_succeeds(monkeypatch):
    # R-OZ3: Ozon review reply is PULT-supported. The executor gate passes (marketplace_api +
    # pult_supported), the dispatcher routes to the Ozon provider, and the composite credential
    # "<client_id>:<api_key>" reaches ozon_client. WB client is never touched.
    from services.marketplace import ozon_client as ozon_mod
    async def go():
        db, uid = await _setup(marketplace="ozon")   # connection carries ozon_client_id="CID"
        seen = {}
        async def fake_ozon(*, token, client_id, review_id, text):
            seen.update(token=token, client_id=client_id, review_id=review_id, text=text)
            return {"requestId": "req-oz"}
        monkeypatch.setattr(ozon_mod.ozon_client, "publish_feedback_answer", fake_ozon)
        wb_called = {"n": 0}
        async def fake_wb(*a, **k): wb_called["n"] += 1; return {}
        monkeypatch.setattr(wb_client, "publish_feedback_answer", fake_wb)

        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"marketplace": "ozon", "feedback_id": "fb1", "text": "Спасибо!", "rating": 5},
            idempotency_key=operation_key.review_key("oz"))
        assert res.status == "success"
        # composite credential split correctly: client_id from the connection, api_key from the vault
        assert seen == {"token": "tok", "client_id": "CID", "review_id": "fb1", "text": "Спасибо!"}
        assert wb_called["n"] == 0                    # WB client never touched
    _run(go())


def test_yandex_publish_without_a_resolved_cabinet_fails_closed(monkeypatch):
    """Yandex now has a provider, but every review call is scoped to a cabinet. When none has been
    resolved yet — a publish before any sync ran — the credential is incomplete and the attempt must
    stop right there. The failure mode this guards against is interpolating the missing value into
    the literal cabinet "None" and sending a seller's reply at it."""
    async def go():
        db, uid = await _setup(marketplace="yandex")     # credential carries no account_ref
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"marketplace": "yandex", "feedback_id": "fb1", "text": "hi", "rating": 5},
            idempotency_key=operation_key.review_key("ym"))
        assert res.ok is False
        assert res.error["code"] == ExecutionError.AUTH
    _run(go())


def test_megamarket_publish_still_fails_closed(monkeypatch):
    # No provider at all → CAPABILITY_NOT_SUPPORTED before anything is dispatched.
    async def go():
        db, uid = await _setup(marketplace="megamarket")
        res = await executor.execute(
            db=db, user_id=uid, action_type="publish_review_response",
            payload={"marketplace": "megamarket", "feedback_id": "fb1", "text": "hi", "rating": 5},
            idempotency_key=operation_key.review_key("mm"))
        assert res.status == "rejected"
        assert res.error["code"] == ExecutionError.CAPABILITY_NOT_SUPPORTED
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
            idempotency_key=operation_key.review_key("wb"))
        assert res.status == "success"
        assert seen == {"feedback_id": "fb1", "text": "Спасибо!"}
    _run(go())
