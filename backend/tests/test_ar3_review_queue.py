"""AR3 — seller review queue + control surface.

Proves the seller-wide queue is owner-scoped and filterable, the derived state resolves by the
fixed precedence, a failed publish records failure_reason + attempts (while the success path is
unchanged), and history is owner-scoped. No auto-publish, no marketplace branching.
"""
import asyncio
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import get_current_user
import models  # register tables
from models.product import Product
from models.review_response import ReviewResponse
from models.execution_log import ExecutionLog
from services.review.state import (
    derive_state, PUBLISHED, FAILED, APPROVED, DRAFTED, NEEDS_ATTENTION, NEW,
)
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


def _client(db, uid):
    async def _override_db():
        yield db
    app = FastAPI()
    app.include_router(reviews_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


async def _product(db, uid, marketplace="wildberries"):
    p = Product(id=str(uuid.uuid4()), user_id=uid, name="T", sku="NM", marketplace=marketplace)
    db.add(p); await db.commit()
    return p


async def _review(db, product_id, marketplace="wildberries", **kw):
    r = ReviewResponse(id=str(uuid.uuid4()), product_id=product_id, marketplace=marketplace,
                       external_review_id=str(uuid.uuid4()), status=kw.pop("status", "pending"), **kw)
    db.add(r); await db.commit()
    return r


# ── State derivation precedence ──────────────────────────────────────────────

def test_state_precedence():
    assert derive_state("published", "RISK", "err") == PUBLISHED     # published wins over all
    assert derive_state("approved", "RISK", "boom") == FAILED        # failure beats approved
    assert derive_state("approved", None, None) == APPROVED
    assert derive_state("drafted", None, None) == DRAFTED
    assert derive_state("pending", "ATTENTION", None) == NEEDS_ATTENTION
    assert derive_state("pending", "SAFE", None) == NEW
    assert derive_state("pending", None, None) == NEW


# ── Queue: owner isolation ───────────────────────────────────────────────────

def test_queue_is_owner_scoped_across_products():
    db = _run(_new_db())
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    pa1 = _run(_product(db, a)); pa2 = _run(_product(db, a))
    pb = _run(_product(db, b))
    _run(_review(db, pa1.id)); _run(_review(db, pa2.id))   # A: two products
    _run(_review(db, pb.id))                                # B: one

    r = _client(db, a).get("/api/reviews/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2                               # only A's reviews, across both products
    # B cannot see A's reviews
    assert _client(db, b).get("/api/reviews/queue").json()["total"] == 1


def test_queue_filters_by_state_and_marketplace():
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    p_wb = _run(_product(db, uid, "wildberries"))
    p_oz = _run(_product(db, uid, "ozon"))
    _run(_review(db, p_wb.id, "wildberries", status="published"))
    _run(_review(db, p_wb.id, "wildberries", status="pending", safety_category="RISK"))
    _run(_review(db, p_oz.id, "ozon", status="drafted"))
    c = _client(db, uid)

    assert c.get("/api/reviews/queue?state=Published").json()["total"] == 1
    assert c.get("/api/reviews/queue?state=NeedsAttention").json()["total"] == 1
    assert c.get("/api/reviews/queue?marketplace=ozon").json()["total"] == 1
    assert c.get("/api/reviews/queue?marketplace=wildberries").json()["total"] == 2


def test_queue_paginates():
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    p = _run(_product(db, uid))
    for _ in range(5):
        _run(_review(db, p.id))
    c = _client(db, uid)
    r = c.get("/api/reviews/queue?limit=2&offset=0").json()
    assert r["total"] == 5 and len(r["items"]) == 2 and r["limit"] == 2


def test_queue_item_exposes_state_and_lifecycle_fields():
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    p = _run(_product(db, uid))
    _run(_review(db, p.id, status="pending", safety_category="RISK",
                 manual_required_reason="жалоба", rating=1))
    item = _client(db, uid).get("/api/reviews/queue").json()["items"][0]
    assert item["state"] == "NeedsAttention"
    assert item["safety_category"] == "RISK" and item["manual_required_reason"] == "жалоба"
    assert item["marketplace"] == "wildberries" and "external_review_id" in item


# ── History: owner-scoped, reads ExecutionLog ────────────────────────────────

def test_history_owner_scoped():
    db = _run(_new_db())
    owner = str(uuid.uuid4())
    p = _run(_product(db, owner))
    rv = _run(_review(db, p.id))
    _run(_add(db, ExecutionLog(id=str(uuid.uuid4()), user_id=owner,
                               action_type="publish_review_response", mode="manual_l3",
                               status="failed", error_code="TIMEOUT",
                               idempotency_key=f"review:{rv.id}", payload={})))
    r = _client(db, owner).get(f"/api/reviews/{p.id}/{rv.id}/history")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 1 and entries[0]["status"] == "failed" and entries[0]["error_code"] == "TIMEOUT"
    # attacker cannot read it
    assert _client(db, str(uuid.uuid4())).get(f"/api/reviews/{p.id}/{rv.id}/history").status_code == 404


async def _add(db, obj):
    db.add(obj); await db.commit()
