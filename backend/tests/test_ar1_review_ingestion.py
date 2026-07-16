"""AR1 — real review ingestion foundation.

Proves the marketplace-neutral provider layer + hardened /sync: WB normalization lives in the
provider, unsupported marketplaces answer 422 with no DB write and no external call, dedup is
enforced by the AR0 partial-unique index (savepoint per row), and ownership/tenant isolation is
unchanged. No response generation, no publish, no automation.
"""
import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
import models  # register tables
from models.product import Product
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.review_response import ReviewResponse
from services.marketplace import credential_vault
from services.marketplace.reviews import get_review_provider, REVIEW_PROVIDERS
from services.marketplace.reviews.base import NormalizedReview
from services.marketplace.reviews.wildberries import WildberriesReviewProvider
from routers import reviews as reviews_router

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, marketplace="wildberries", with_conn=True):
    prod = Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="NM-1", marketplace=marketplace)
    db.add(prod)
    if with_conn:
        conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                     status="connected", scopes=["feedbacks"])
        db.add(conn)
        await db.flush()
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                             secret_enc=credential_vault.encrypt("tok"), meta={}))
    await db.commit()
    return prod


def _client(db, uid):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(reviews_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


# WB payload as list_unanswered_feedbacks returns it.
_WB_FB = [
    {"id": "WB-1", "text": "супер", "userName": "Иван", "productValuation": 5,
     "createdDate": "2026-07-01T10:00:00Z"},
    {"id": "WB-2", "text": "плохо", "userName": "", "productValuation": 2},   # no date
    {"id": "", "text": "no id — dropped"},
]


# ── 1. WB provider normalizes payload ────────────────────────────────────────

def test_wb_provider_normalizes(monkeypatch):
    async def _fake(*, token, nm_id=None, take=50, skip=0):
        return _WB_FB
    monkeypatch.setattr("services.marketplace.reviews.wildberries.wb_client.list_unanswered_feedbacks", _fake)

    out = _run(WildberriesReviewProvider().fetch_reviews("tok", "NM-1"))
    assert len(out) == 2  # empty-id row dropped
    a, b = out
    assert a.external_review_id == "WB-1" and a.rating == 5 and a.text == "супер" and a.author == "Иван"
    assert a.marketplace == "wildberries" and a.product_ref == "NM-1"
    assert isinstance(a.review_created_at, datetime)          # date parsed
    assert b.external_review_id == "WB-2" and b.author is None and b.review_created_at is None  # no date → None


# ── 2. Unsupported marketplace: 422, no DB rows, no external call ─────────────

def test_unsupported_marketplace_is_honest(monkeypatch):
    called = {"n": 0}
    async def _boom(*a, **k):
        called["n"] += 1
        return []
    monkeypatch.setattr("services.marketplace.reviews.wildberries.wb_client.list_unanswered_feedbacks", _boom)

    db = _run(_new_db())
    uid = str(uuid.uuid4())
    prod = _run(_seed(db, uid, marketplace="ozon"))
    r = _client(db, uid).post(f"/api/reviews/{prod.id}/sync")

    assert r.status_code == 422
    assert "не поддерживается" in r.json()["detail"]
    assert called["n"] == 0                                    # no external call
    n = _run(db.execute(select(func.count(ReviewResponse.id)))).scalar_one()
    assert n == 0                                              # no DB write


def test_registry_has_no_fake_providers():
    # WB is live. Ozon has a REAL fetch/publish provider (R-OZ1) but it is honestly gated —
    # supports_reviews() stays False until R-OZ3 wires the publish dispatcher + flips
    # capability_registry pult_supported, so the /sync router still answers unsupported for Ozon.
    # No FAKE/stub provider is registered for anyone.
    assert set(REVIEW_PROVIDERS) == {"wildberries", "ozon"}
    assert get_review_provider("wildberries").supports_reviews() is True
    assert get_review_provider("ozon").supports_reviews() is False
    for mp in ("yandex", "megamarket"):
        assert get_review_provider(mp) is None


# ── 3. Duplicate ingestion: one row, second sync skipped ─────────────────────

def test_duplicate_ingestion_is_skipped(monkeypatch):
    async def _fake(*, token, nm_id=None, take=50, skip=0):
        return [{"id": "WB-1", "text": "x", "userName": "A", "productValuation": 5}]
    monkeypatch.setattr("services.marketplace.reviews.wildberries.wb_client.list_unanswered_feedbacks", _fake)

    db = _run(_new_db())
    uid = str(uuid.uuid4())
    prod = _run(_seed(db, uid))
    c = _client(db, uid)

    r1 = c.post(f"/api/reviews/{prod.id}/sync")
    assert r1.json()["imported"] == 1 and r1.json()["skipped"] == 0
    r2 = c.post(f"/api/reviews/{prod.id}/sync")
    assert r2.json()["imported"] == 0 and r2.json()["skipped"] == 1   # dedup via unique index
    n = _run(db.execute(select(func.count(ReviewResponse.id))
                        .where(ReviewResponse.product_id == prod.id))).scalar_one()
    assert n == 1


# ── 4. Concurrent duplicate protection (batch of same id → one row) ──────────

def test_batch_with_repeated_external_id_yields_one_row(monkeypatch):
    async def _fake(*, token, nm_id=None, take=50, skip=0):
        # same external id twice in one batch — the DB, not app code, keeps it to one
        return [{"id": "WB-9", "text": "a", "productValuation": 5},
                {"id": "WB-9", "text": "b", "productValuation": 5}]
    monkeypatch.setattr("services.marketplace.reviews.wildberries.wb_client.list_unanswered_feedbacks", _fake)

    db = _run(_new_db())
    uid = str(uuid.uuid4())
    prod = _run(_seed(db, uid))
    r = _client(db, uid).post(f"/api/reviews/{prod.id}/sync")
    assert r.json()["imported"] == 1 and r.json()["skipped"] == 1
    n = _run(db.execute(select(func.count(ReviewResponse.id)))).scalar_one()
    assert n == 1


# ── 5-6. Tenant isolation + owned-product requirement unchanged ──────────────

def test_sync_requires_owned_product(monkeypatch):
    async def _fake(*a, **k):
        return []
    monkeypatch.setattr("services.marketplace.reviews.wildberries.wb_client.list_unanswered_feedbacks", _fake)

    db = _run(_new_db())
    owner = str(uuid.uuid4())
    prod = _run(_seed(db, owner))
    attacker = str(uuid.uuid4())
    r = _client(db, attacker).post(f"/api/reviews/{prod.id}/sync")   # different user
    assert r.status_code == 404
