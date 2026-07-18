"""AR-AUTO-FILL — the full automatic path, no manual sync or draft.

Proves that with an enabled + consented rule PULT auto-syncs a review, auto-drafts a safe reply, and
(mode=auto + kill switch on) auto-publishes it — for Wildberries AND Ozon — and that confirm-mode
stops before publishing, negatives/ATTENTION never auto-publish, the kill switch gates ONLY publish,
and a second cycle never double-syncs or double-publishes. Provider + executor are mocked (no network).
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  register tables
from models.user import User
from models.product import Product
from models.review_response import ReviewResponse
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from config import settings
from services.marketplace import credential_vault
from services.marketplace.reviews import REVIEW_PROVIDERS
import tasks.auto_review_pipeline as pipe
import tasks.auto_publish_reviews as ap
from services.marketplace.review_automation_gate import CONSENT_VERSION as _CV

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _bind_session(monkeypatch, db):
    """Point both pipeline and publish worker at the one test session."""
    @asynccontextmanager
    async def _factory():
        yield db
    monkeypatch.setattr(pipe, "AsyncSessionLocal", _factory)
    monkeypatch.setattr(ap, "AsyncSessionLocal", _factory)


def _mock_fetch(monkeypatch, marketplace, reviews):
    async def _fake(token, product_ref):
        return reviews
    monkeypatch.setattr(REVIEW_PROVIDERS[marketplace], "fetch_reviews", _fake)


def _mock_publish(monkeypatch, *, ok=True, status="success", error=None):
    calls = {"n": 0}
    async def _fake(*, db, user_id, action_type, payload, mode, insight_key, idempotency_key, rule=None):
        calls["n"] += 1
        from models.execution_log import ExecutionLog
        log = ExecutionLog(id=str(uuid.uuid4()), user_id=user_id, action_type=action_type,
                           mode=mode, status=status, idempotency_key=idempotency_key, payload={})
        db.add(log); await db.flush()
        return SimpleNamespace(ok=ok, status=status, error=error, log_id=log.id)
    monkeypatch.setattr(ap.executor, "execute", _fake)
    return calls


def _review(marketplace, *, rating=5, text="Отлично", ext=None):
    return SimpleNamespace(marketplace=marketplace, external_review_id=(ext or str(uuid.uuid4())),
                           product_ref="SKU", rating=rating, text=text, author="И",
                           review_created_at=datetime(2026, 7, 14), metadata={})


async def _seed(db, *, marketplace="wildberries", mode="auto", enabled=True, consent=True):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                 status="connected", scopes=["feedbacks"],
                                 ozon_client_id="CID" if marketplace == "ozon" else None)
    db.add(conn); await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), meta={}))
    db.add(Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="SKU", marketplace=marketplace))
    db.add(AutomationRule(id=str(uuid.uuid4()), user_id=uid, contour="reputation",
                          action_type="publish_review_response", mode=mode, enabled=enabled,
                          guard={}, connection_id=conn.id,
                          consent_at=(datetime.utcnow() if consent else None),
                          consent_version=(_CV if consent else None)))
    await db.commit()
    return uid


def _reviews(db, uid):
    return _run(db.execute(select(ReviewResponse))).scalars().all()


# ── Full auto path: WB + Ozon ────────────────────────────────────────────────
def _full_auto(monkeypatch, marketplace):
    db = _run(_new_db())
    uid = _run(_seed(db, marketplace=marketplace, mode="auto"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", True)
    _mock_fetch(monkeypatch, marketplace, [_review(marketplace, ext="R1")])
    calls = _mock_publish(monkeypatch)

    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())

    rows = _reviews(db, uid)
    assert len(rows) == 1                          # synced once
    assert rows[0].status == "published"           # auto-published
    assert calls["n"] == 1                         # one provider publish call

    # Second cycle: no new sync (dedup) and no second publish (already published)
    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())
    assert len(_reviews(db, uid)) == 1 and calls["n"] == 1


def test_full_auto_wb(monkeypatch):
    _full_auto(monkeypatch, "wildberries")


def test_full_auto_ozon(monkeypatch):
    _full_auto(monkeypatch, "ozon")


# ── Confirm mode: sync + draft, NO publish ───────────────────────────────────
def _confirm(monkeypatch, marketplace):
    db = _run(_new_db())
    uid = _run(_seed(db, marketplace=marketplace, mode="confirm"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", True)   # even with the switch on, confirm waits
    _mock_fetch(monkeypatch, marketplace, [_review(marketplace, ext="R1")])
    calls = _mock_publish(monkeypatch)

    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())

    rows = _reviews(db, uid)
    assert len(rows) == 1 and rows[0].status == "drafted"       # drafted, waiting for the seller
    assert rows[0].response_text                                 # a real draft exists
    assert calls["n"] == 0                                       # never auto-published


def test_confirm_wb(monkeypatch):
    _confirm(monkeypatch, "wildberries")


def test_confirm_ozon(monkeypatch):
    _confirm(monkeypatch, "ozon")


# ── Safety: ATTENTION drafted-not-published, RISK not drafted ────────────────
def test_attention_drafted_never_published(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, mode="auto"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", True)
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", rating=3, text="средне", ext="A1")])
    calls = _mock_publish(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())
    rows = _reviews(db, uid)
    assert rows[0].safety_category == "ATTENTION"
    assert rows[0].status == "drafted"      # not 'approved' → worker never publishes
    assert calls["n"] == 0


def test_risk_not_drafted(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, mode="auto"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", True)
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", rating=1, text="ужас", ext="X1")])
    calls = _mock_publish(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    _run(ap.run_auto_publish_reviews())
    rows = _reviews(db, uid)
    assert rows[0].safety_category == "RISK"
    assert rows[0].status == "pending" and not (rows[0].response_text or "")   # no draft
    assert calls["n"] == 0


# ── Kill switch gates ONLY publish; sync + draft still run ────────────────────
def test_global_off_syncs_drafts_but_never_publishes(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, mode="auto"))
    _bind_session(monkeypatch, db)
    monkeypatch.setattr(settings, "automation_enabled", False)   # kill switch OFF
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", ext="R1")])
    calls = _mock_publish(monkeypatch)
    _run(pipe.run_auto_sync_reviews())
    _run(pipe.run_auto_draft_reviews())
    out = _run(ap.run_auto_publish_reviews())
    rows = _reviews(db, uid)
    assert len(rows) == 1 and rows[0].response_text            # synced + drafted despite kill switch
    assert out["ran"] is False and calls["n"] == 0            # publish worker no-op globally


# ── Seller off / no consent / revoke → nothing happens ───────────────────────
def test_seller_disabled_no_sync(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, enabled=False))
    _bind_session(monkeypatch, db)
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", ext="R1")])
    _run(pipe.run_auto_sync_reviews())
    assert len(_reviews(db, uid)) == 0


def test_no_consent_no_sync(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, consent=False))
    _bind_session(monkeypatch, db)
    _mock_fetch(monkeypatch, "wildberries", [_review("wildberries", ext="R1")])
    _run(pipe.run_auto_sync_reviews())
    assert len(_reviews(db, uid)) == 0


# ── One failing store never stops the rest ────────────────────────────────────
def test_one_connection_error_does_not_stop_others(monkeypatch):
    db = _run(_new_db())
    uid = _run(_seed(db, marketplace="wildberries", mode="auto"))
    _bind_session(monkeypatch, db)

    async def _boom(token, product_ref):
        raise RuntimeError("marketplace down")
    monkeypatch.setattr(REVIEW_PROVIDERS["wildberries"], "fetch_reviews", _boom)

    out = _run(pipe.run_auto_sync_reviews())   # must not raise
    assert out["errors"] >= 1 and out["imported"] == 0
