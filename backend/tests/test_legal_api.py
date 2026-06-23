"""
Legal A7 — API layer tests.

Handlers called directly with a real in-memory db (DI bypassed). POST audit runs
audit_and_persist over an internal subject; missing subject data → legal_unavailable
(not error). Lifecycle endpoints mutate only status/lifecycle_reason/updated_at.
No compliance / score / forecast / guarantee / money in responses. Envelope
{items,total}. not_evaluated is never shown as compliant.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_product import ImportedProductRow

from routers import legal_engine as le
from routers.legal_engine import (
    run_legal_audit, legal_signals, legal_overview, legal_audits, legal_findings,
    acknowledge_signal, dismiss_signal, reopen_signal,
    LegalAuditRequest, LegalAuditResponse, LegalSignalsResponse,
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


async def _seed(db, uid, *, title="Крем лечит 100%", mp="wildberries", sku="SKU1"):
    db.add(ImportedProductRow(import_id="imp1", user_id=uid, marketplace=mp, sku=sku, title=title))
    await db.flush()


async def _audit(db, uid, *, mp="wildberries", sku="SKU1"):
    return await run_legal_audit(
        LegalAuditRequest(marketplace=mp, subject_type="product", subject_ref=sku, sku=sku),
        current_user=_User(uid), db=db)


# ── 1. POST audit creates audit/evaluations/signals ──────────────────────────

def test_post_audit_creates_records():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid); await db.commit()
        resp = await _audit(db, uid)
        assert isinstance(resp, LegalAuditResponse)
        assert resp.ok and resp.status == "completed"
        assert resp.total_findings >= 1 and resp.reconciliation.created >= 1
    _run(go())


# ── 2. GET signals returns legal signals (5 doctrine parts) ──────────────────

def test_get_signals_doctrine():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid); await db.commit()
        await _audit(db, uid)
        sg = await legal_signals(current_user=_User(uid), db=db)
        assert isinstance(sg, LegalSignalsResponse) and sg.total >= 1
        s = next(x for x in sg.items if x.requirement_type == "content_claim_risk")
        assert s.what_happened and s.why_it_matters and s.meaning
        assert s.recommended_action and s.expected_effect
        assert s.recommended_action_key == "review_content_claim"
        assert s.effect_type == "takedown_risk_reduction"   # qualitative
        assert "может снизить риск" in s.expected_effect.lower()
    _run(go())


# ── 3. audit with insufficient data → honest degradation ─────────────────────

def test_audit_insufficient_data():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        resp = await run_legal_audit(
            LegalAuditRequest(marketplace="ozon"),   # no subject_ref / sku
            current_user=_User(uid), db=db)
        assert resp.ok is False and resp.status == "legal_unavailable"
        assert resp.reason == "insufficient_data" and resp.audit_id is None
    _run(go())


# ── 4/5/6. lifecycle endpoints ───────────────────────────────────────────────

async def _first_signal_id(db, uid):
    sg = await legal_signals(current_user=_User(uid), db=db)
    return sg.items[0].signal_id


def test_acknowledge():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid); await db.commit(); await _audit(db, uid)
        sid = await _first_signal_id(db, uid)
        r = await acknowledge_signal(sid, current_user=_User(uid), db=db)
        assert r.ok and r.signal.status == "acknowledged"
        assert r.signal.lifecycle_reason == "acknowledged_by_user"
    _run(go())


def test_dismiss():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid); await db.commit(); await _audit(db, uid)
        sid = await _first_signal_id(db, uid)
        r = await dismiss_signal(sid, current_user=_User(uid), db=db)
        assert r.ok and r.signal.status == "dismissed"
        assert r.signal.lifecycle_reason == "dismissed_by_user"
    _run(go())


def test_reopen():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid); await db.commit(); await _audit(db, uid)
        sid = await _first_signal_id(db, uid)
        await dismiss_signal(sid, current_user=_User(uid), db=db)
        r = await reopen_signal(sid, current_user=_User(uid), db=db)
        assert r.ok and r.signal.status == "reopened"
        assert r.signal.lifecycle_reason == "reopened_by_user"
    _run(go())


# ── 7. no compliance/score/forecast/guarantee/money fields in API ────────────

def test_no_forbidden_fields_in_api():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid); await db.commit(); await _audit(db, uid)
        sg = await legal_signals(current_user=_User(uid), db=db)
        d = sg.items[0].model_dump()
        for bad in ("score", "confidence", "forecast", "guarantee", "compliance",
                    "compliant", "expected_revenue", "money", "rub"):
            assert bad not in d, bad
        blob = str(d).lower()
        for bad in ("compliant", "гаранти", "₽", "прогноз"):
            assert bad not in blob, bad
    _run(go())


# ── 8. envelope shape + not_evaluated not shown as compliant ─────────────────

def test_envelope_and_no_compliant_overview():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # clean title → content_claim not_detected, others not_evaluated → no signals
        await _seed(db, uid, title="Чайник синий"); await db.commit()
        await _audit(db, uid)
        sg = await legal_signals(current_user=_User(uid), db=db)
        d = sg.model_dump()
        assert set(d.keys()) == {"items", "total"} and d["total"] == 0
        ov = await legal_overview(current_user=_User(uid), db=db)
        od = ov.model_dump()
        # honest: no "compliant"/score field; not_evaluated surfaces as a count, not a pass
        for bad in ("compliant", "compliance", "score"):
            assert bad not in od
        assert od["total_not_evaluated"] >= 1
    _run(go())


# ── 9. routes mounted in main.py ─────────────────────────────────────────────

def test_routes_mounted():
    paths = {getattr(r, "path", None) for r in le.router.routes}
    assert {"/legal/signals", "/legal/audit", "/legal/signals/{signal_id}/acknowledge",
            "/legal/signals/{signal_id}/dismiss", "/legal/signals/{signal_id}/reopen"} <= paths
    import main
    app_paths = set(main.app.openapi()["paths"])  # OpenAPI paths: robust on FastAPI 0.136 (flat) and 0.137+ (nested mounts)
    assert "/api/legal/audit" in app_paths and "/api/legal/signals" in app_paths


# ── 10. audits + findings read endpoints ─────────────────────────────────────

def test_audits_and_findings():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid); await db.commit(); await _audit(db, uid)
        au = await legal_audits(current_user=_User(uid), db=db)
        assert au.total == 1 and au.items[0].status == "completed"
        fi = await legal_findings(current_user=_User(uid), db=db)
        assert fi.total >= 1
        f = next(x for x in fi.items if x.requirement_type == "content_claim_risk")
        assert f.estimated_effect_type == "takedown_risk" and "matched_keywords" in (f.evidence or {})
    _run(go())
