"""
Review A7 — API layer tests.

Handlers called directly with a real in-memory db (DI bypassed). POST audit
builds a ReviewSnapshot from a seeded internal ReviewResponse; missing review →
status=review_unavailable (NOT error). Verifies all 5 endpoints, doctrine
fields, safety_category + safety_mode visible, AUTO never returned for
RISK/ATTENTION, no score, no AI/generation, marketplace independence, routes
mounted in main.py.
"""
import ast
import asyncio
import inspect
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.product import Product
from models.review_response import ReviewResponse

from routers import review_engine as rv
from routers.review_engine import (
    run_review_audit, review_overview, review_signals, review_problems, review_audits,
    ReviewAuditRequest, ReviewAuditResponse, ReviewOverviewResponse,
)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _User:
    def __init__(self, uid):
        self.id = uid


async def _seed_review(db, uid, *, review_id, rating, marketplace="wildberries",
                       sku="SKU1", text=None, response_text=None, status="pending"):
    prod = Product(user_id=uid, name="P", marketplace=marketplace, category="Кухня", sku=sku)
    db.add(prod)
    await db.flush()
    db.add(ReviewResponse(id=review_id, product_id=prod.id, rating=rating, review_text=text,
                          response_text=response_text, status=status, marketplace=marketplace))
    await db.flush()
    return prod.id


async def _seed_review_no_product(db, *, review_id, rating, marketplace="wildberries"):
    # review whose product_id points to a non-existent product → ownership unprovable
    db.add(ReviewResponse(id=review_id, product_id="ghost-product", rating=rating,
                          marketplace=marketplace, status="pending"))
    await db.flush()


async def _audit(db, uid, review_id, *, marketplace="wildberries"):
    return await run_review_audit(
        ReviewAuditRequest(review_id=review_id, marketplace=marketplace),
        current_user=_User(uid), db=db)


# ── 1. POST audit creates an audit from an internal review ───────────────────

def test_post_audit_creates_audit():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review(db, uid, review_id="rev-1", rating=1); await db.commit()
        resp = await _audit(db, uid, "rev-1")
        assert isinstance(resp, ReviewAuditResponse)
        assert resp.ok and resp.status == "completed" and resp.audit_id
        assert resp.total_problems >= 1            # RISK + unanswered → negative review signal
        assert resp.reconciliation.created >= 1
    _run(go())


# ── 2. missing review → review_unavailable (NOT error) ───────────────────────

def test_missing_review_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await _audit(db, uid, "ghost")
        assert resp.ok is False and resp.status == "review_unavailable"
        assert resp.reason == "review_missing" and resp.audit_id is None  # no fabrication
    _run(go())


# ── 3. overview without fake numbers / no score ──────────────────────────────

def test_overview_no_fake_no_score():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review(db, uid, review_id="rev-1", rating=1); await db.commit()
        await _audit(db, uid, "rev-1")
        ov = await review_overview(marketplace="wildberries", current_user=_User(uid), db=db)
        assert isinstance(ov, ReviewOverviewResponse)
        assert ov.active_signals >= 1 and ov.risk_signals >= 1
        assert ov.unresolved_problems >= 1 and ov.last_audit_at is not None
        d = ov.model_dump()
        assert "score" not in d and "rating_forecast" not in d
        # empty user → honest zeros, no fabrication
        ov2 = await review_overview(current_user=_User(str(uuid.uuid4())), db=db)
        assert ov2.active_signals == 0 and ov2.last_audit_at is None
    _run(go())


# ── 4. signals carry the 5 doctrine parts ────────────────────────────────────

def test_signals_doctrine_fields():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review(db, uid, review_id="rev-1", rating=1); await db.commit()
        await _audit(db, uid, "rev-1")
        sg = await review_signals(status="active", current_user=_User(uid), db=db)
        assert sg.total >= 1
        s = next(x for x in sg.items if x.problem_type == "unanswered_negative_review")
        assert s.what and s.why and s.meaning and s.recommended_action and s.expected_effect
        assert s.priority_level and s.effect_band and s.confidence is not None
        assert s.review_id == "rev-1"
    _run(go())


# ── 5. signals expose safety_category AND safety_mode ────────────────────────

def test_signals_include_safety_category_and_mode():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review(db, uid, review_id="rev-1", rating=1); await db.commit()
        await _audit(db, uid, "rev-1")
        sg = await review_signals(current_user=_User(uid), db=db)
        s = next(x for x in sg.items if x.problem_type == "unanswered_negative_review")
        assert s.safety_category == "RISK"
        assert s.safety_mode == "manual_only"      # mandatory, never auto for RISK
    _run(go())


# ── 6. AUTO is never returned for RISK / ATTENTION signals ───────────────────

def test_risk_attention_never_auto():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review(db, uid, review_id="rev-risk", rating=1); await db.commit()
        await _audit(db, uid, "rev-risk")
        await _seed_review(db, uid, review_id="rev-att", rating=3, sku="SKU2"); await db.commit()
        await _audit(db, uid, "rev-att")
        sg = await review_signals(current_user=_User(uid), db=db)
        risky = [x for x in sg.items if x.safety_category in ("RISK", "ATTENTION")]
        assert risky
        for x in risky:
            assert x.safety_mode != "auto"
    _run(go())


# ── 7. problems = latest audit only ──────────────────────────────────────────

def test_problems_latest_audit():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review(db, uid, review_id="rev-1", rating=1); await db.commit()
        await _audit(db, uid, "rev-1")
        pr = await review_problems(current_user=_User(uid), db=db)
        assert pr.total >= 1
        p = next(x for x in pr.items if x.problem_type == "unanswered_negative_review")
        assert p.severity and p.category == "RISK" and p.estimated_effect_type
        assert p.review_id == "rev-1" and p.detected_at
        assert "safety_category" in (p.evidence or {})
    _run(go())


# ── 8. audits history ────────────────────────────────────────────────────────

def test_audits_history():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review(db, uid, review_id="rev-1", rating=1); await db.commit()
        await _audit(db, uid, "rev-1")
        au = await review_audits(current_user=_User(uid), db=db)
        assert au.total == 1
        a = au.items[0].model_dump()
        assert a["status"] == "completed" and a["triggered_by"] == "manual"
        for bad in ("score", "snapshot_hash", "rating_forecast"):
            assert bad not in a
    _run(go())


# ── 9. marketplace agnostic ──────────────────────────────────────────────────

def test_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            await _seed_review(db, uid, review_id="rev-1", rating=1, marketplace=mp)
            await db.commit()
            resp = await _audit(db, uid, "rev-1", marketplace=mp)
            assert resp.ok and resp.total_problems >= 1
            sg = await review_signals(current_user=_User(uid), db=db)
            assert any(f":{mp}:SKU1:rev-1" in (x.insight_key or "") for x in sg.items)
    _run(go())


# ── 10. no public score anywhere in responses ────────────────────────────────

def test_no_score_in_responses():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review(db, uid, review_id="rev-1", rating=1); await db.commit()
        resp = await _audit(db, uid, "rev-1")
        ov = await review_overview(current_user=_User(uid), db=db)
        sg = await review_signals(current_user=_User(uid), db=db)
        blobs = [str(resp.model_dump()), str(ov.model_dump()),
                 str([x.model_dump() for x in sg.items])]
        for blob in blobs:
            low = blob.lower()
            for bad in ("'score'", "internal_health_index", "rating_forecast"):
                assert bad not in low
    _run(go())


# ── 11. router has no AI / reply-generation modules ──────────────────────────

def test_router_no_ai_generation():
    src = Path(inspect.getfile(rv)).read_text(encoding="utf-8").lower()
    for bad in ("openai", "anthropic", "llm", "generate_reply", "autoresponder", "gpt"):
        assert bad not in src
    # static-import check: no marketplace client / external API in the router
    forbidden = ("wb_client", "ozon_client", "yandex_client", "credential_vault")
    offenders = []
    for node in ast.walk(ast.parse(Path(inspect.getfile(rv)).read_text(encoding="utf-8"))):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            for f in forbidden:
                if f in m:
                    offenders.append(m)
    assert not offenders, offenders


# ── 12. routes mounted in main.py ────────────────────────────────────────────

def test_routes_mounted():
    paths = {getattr(r, "path", None) for r in rv.router.routes}
    assert {"/reviews/audit", "/reviews/overview", "/reviews/signals",
            "/reviews/problems", "/reviews/audits"} <= paths
    import main
    app_paths = set(main.app.openapi()["paths"])  # OpenAPI paths: robust on FastAPI 0.136 (flat) and 0.137+ (nested mounts)
    assert "/api/reviews/audit" in app_paths
    # review_engine must be registered BEFORE reviews (else /reviews/{product_id}
    # shadows the explicit GET paths). Verify ordering in the app route table.
    ordered = list(main.app.openapi()["paths"])
    assert ordered.index("/api/reviews/overview") < ordered.index("/api/reviews/{product_id}")


# ── 13. owner can audit own review (A7 hardening) ────────────────────────────

def test_owner_can_audit_own_review():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review(db, uid, review_id="rev-1", rating=1); await db.commit()
        resp = await _audit(db, uid, "rev-1")
        assert resp.ok and resp.status == "completed" and resp.audit_id
    _run(go())


# ── 14. non-owner → review_unavailable (NOT 403, no leak) ────────────────────

def test_non_owner_gets_review_unavailable():
    async def go():
        db = await _engine()
        owner = str(uuid.uuid4()); attacker = str(uuid.uuid4())
        await _seed_review(db, owner, review_id="rev-1", rating=1); await db.commit()
        resp = await _audit(db, attacker, "rev-1")          # attacker audits owner's review
        assert resp.ok is False and resp.status == "review_unavailable"
        assert resp.reason == "review_missing"              # same reason as truly-missing
        assert resp.audit_id is None
    _run(go())


# ── 15. missing ownership proof (no product) → review_unavailable ────────────

def test_missing_ownership_proof_unavailable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_review_no_product(db, review_id="rev-orphan", rating=1); await db.commit()
        resp = await _audit(db, uid, "rev-orphan")
        assert resp.ok is False and resp.status == "review_unavailable"
        assert resp.reason == "review_missing" and resp.audit_id is None
    _run(go())


# ── 16. no data leak: foreign review response == truly-missing response ──────

def test_no_data_leak_for_foreign_review():
    async def go():
        db = await _engine()
        owner = str(uuid.uuid4()); attacker = str(uuid.uuid4())
        await _seed_review(db, owner, review_id="secret-rev", rating=2, sku="SECRET",
                           text="конфиденциально"); await db.commit()
        foreign = (await _audit(db, attacker, "secret-rev")).model_dump()
        ghost = (await _audit(db, attacker, "does-not-exist")).model_dump()
        # identical externally-visible shape (sans the caller's own echoed review_id)
        # → existence of secret-rev not revealed
        foreign.pop("review_id"); ghost.pop("review_id")
        assert foreign == ghost
        blob = str(foreign).lower()
        for leaked in ("secret", "конфиденциаль", "owner"):
            assert leaked not in blob
    _run(go())
