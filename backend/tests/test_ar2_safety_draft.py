"""AR2 — safety classification persisted on real reviews + safe deterministic drafts.

Reviews are classified at ingestion (reusing the existing classify_safety), the category and a
human reason land on the ReviewResponse row, SAFE/ATTENTION reviews get a promise-free draft, RISK
reviews never get a sendable draft, approval is a status move (not a publish), and there is no LLM
anywhere. Ownership and marketplace neutrality are preserved.
"""
import asyncio
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
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
from services.review.draft import classify_review, build_draft, FORBIDDEN_MARKERS
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


async def _seed(db, uid, marketplace="wildberries"):
    prod = Product(id=str(uuid.uuid4()), user_id=uid, name="Кружка", sku="NM-1", marketplace=marketplace)
    db.add(prod)
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


def _sync_with(monkeypatch, db, uid, feedbacks, marketplace="wildberries"):
    async def _fake(*, token, nm_id=None, take=50, skip=0):
        return feedbacks
    monkeypatch.setattr("services.marketplace.reviews.wildberries.wb_client.list_unanswered_feedbacks", _fake)
    prod = _run(_seed(db, uid, marketplace))
    r = _client(db, uid).post(f"/api/reviews/{prod.id}/sync")
    assert r.status_code == 200, r.text
    return prod


def _rows(db, product_id):
    return _run(db.execute(select(ReviewResponse).where(ReviewResponse.product_id == product_id)
                           .order_by(ReviewResponse.external_review_id))).scalars().all()


# ── 1. Safety persisted at ingestion ─────────────────────────────────────────

def test_safety_category_persisted_on_sync(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    prod = _sync_with(monkeypatch, db, uid, [
        {"id": "A", "text": "отлично", "productValuation": 5},          # SAFE
        {"id": "B", "text": "нормально но есть нюансы", "productValuation": 4},  # 4★+text → ATTENTION
        {"id": "C", "text": "так себе", "productValuation": 3},          # ATTENTION
        {"id": "D", "text": "ужасно", "productValuation": 1},            # RISK (≤2)
        {"id": "E", "text": "пришёл брак", "productValuation": 5},       # RISK (complaint marker)
    ])
    by = {r.external_review_id: r for r in _rows(db, prod.id)}
    assert by["A"].safety_category == "SAFE"
    assert by["B"].safety_category == "ATTENTION"
    assert by["C"].safety_category == "ATTENTION"
    assert by["D"].safety_category == "RISK"
    assert by["E"].safety_category == "RISK"


def test_manual_reason_only_for_non_safe(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    prod = _sync_with(monkeypatch, db, uid, [
        {"id": "A", "text": "отлично", "productValuation": 5},
        {"id": "D", "text": "ужасно", "productValuation": 1},
        {"id": "C", "text": "средне", "productValuation": 3},
    ])
    by = {r.external_review_id: r for r in _rows(db, prod.id)}
    assert by["A"].manual_required_reason is None          # SAFE → no reason
    assert by["D"].manual_required_reason                  # RISK → reason
    assert by["C"].manual_required_reason                  # ATTENTION → reason


# ── 2. Draft: SAFE gets a safe draft, RISK does not ──────────────────────────

def test_safe_review_gets_contextual_draft(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    prod = _sync_with(monkeypatch, db, uid, [{"id": "A", "text": "супер", "productValuation": 5}])
    rid = _rows(db, prod.id)[0].id
    r = _client(db, uid).post(f"/api/reviews/{prod.id}/{rid}/draft")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "drafted"
    assert "Кружка" in body["response_text"]               # contextual: product name present
    low = body["response_text"].lower()
    assert not any(m in low for m in FORBIDDEN_MARKERS)     # no refund/defect/legal promises


def test_risk_review_gets_no_sendable_draft(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    prod = _sync_with(monkeypatch, db, uid, [{"id": "D", "text": "брак", "productValuation": 1}])
    rid = _rows(db, prod.id)[0].id
    r = _client(db, uid).post(f"/api/reviews/{prod.id}/{rid}/draft")
    assert r.status_code == 422                             # RISK: refused, human writes it
    row = _run(db.get(ReviewResponse, rid))
    assert (row.response_text or "") == ""                  # response_text stays empty
    assert row.status != "drafted"


def test_draft_never_sets_published(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    prod = _sync_with(monkeypatch, db, uid, [{"id": "A", "text": "ок", "productValuation": 5}])
    rid = _rows(db, prod.id)[0].id
    _client(db, uid).post(f"/api/reviews/{prod.id}/{rid}/draft")
    assert _run(db.get(ReviewResponse, rid)).status == "drafted"   # never "published"


# ── 3. Approval is a status move, not a publish ──────────────────────────────

def test_approve_moves_to_approved(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    prod = _sync_with(monkeypatch, db, uid, [{"id": "A", "text": "ок", "productValuation": 5}])
    rid = _rows(db, prod.id)[0].id
    c = _client(db, uid)
    c.post(f"/api/reviews/{prod.id}/{rid}/draft")
    r = c.post(f"/api/reviews/{prod.id}/{rid}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert _run(db.get(ReviewResponse, rid)).published_at is None    # approve ≠ publish


def test_cannot_approve_empty(monkeypatch):
    db = _run(_new_db()); uid = str(uuid.uuid4())
    prod = _sync_with(monkeypatch, db, uid, [{"id": "D", "text": "брак", "productValuation": 1}])
    rid = _rows(db, prod.id)[0].id     # RISK, no draft → empty
    r = _client(db, uid).post(f"/api/reviews/{prod.id}/{rid}/approve")
    assert r.status_code == 422


# ── 4. Security: tenant isolation on draft/approve ───────────────────────────

def test_draft_and_approve_are_owner_scoped(monkeypatch):
    db = _run(_new_db()); owner = str(uuid.uuid4())
    prod = _sync_with(monkeypatch, db, owner, [{"id": "A", "text": "ок", "productValuation": 5}])
    rid = _rows(db, prod.id)[0].id
    attacker = _client(db, str(uuid.uuid4()))
    assert attacker.post(f"/api/reviews/{prod.id}/{rid}/draft").status_code == 404
    assert attacker.post(f"/api/reviews/{prod.id}/{rid}/approve").status_code == 404


# ── 5. Marketplace neutrality + no LLM ───────────────────────────────────────

def test_classification_is_marketplace_agnostic():
    # classify_review takes only rating + text; nothing marketplace-specific.
    for mp in ("wildberries", "ozon", "yandex", "megamarket"):
        assert classify_review(5, "ок")[0] == "SAFE"
        assert classify_review(1, "плохо")[0] == "RISK"
    # and the draft builder is marketplace-agnostic too
    assert build_draft(5, "ок", "Товар") is not None
    assert build_draft(1, "брак", "Товар") is None


def test_no_llm_dependency():
    # Check real dependencies, not words in the docstring (which explains what the module is NOT).
    # Strip the module docstring and comments, then assert no LLM import and no randomness.
    import ast
    from pathlib import Path
    src = Path(reviews_router.__file__).resolve().parent.parent / "services" / "review" / "draft.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for bad in ("openai", "anthropic", "random", "httpx", "requests"):
        assert bad not in imported, f"draft.py must not import {bad}"

    # No attribute call like x.choice(...) / openai.* — pure deterministic code only.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "choice", "draft.py must not use random .choice"
