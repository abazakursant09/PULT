"""
Legal A3 — Snapshot tests.

Read-only framing of legal-risk inputs for a subject. Honest maps: available /
missing inputs, requirement_candidates, not_evaluated_reasons. A missing input is
NEVER read as compliance. Snapshot writes nothing (no findings, no signals). No
score / guarantee / forecast. Works with empty sources and honest degradation.
"""
import asyncio
import uuid
from dataclasses import fields as dc_fields
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.product import Product
from models.imported_product import ImportedProductRow
from models.legal_audit import LegalAudit
from models.legal_finding import LegalFinding
from models.legal_signal import LegalSignal

from services.legal.internal_source import build_snapshot_from_internal
from services.legal.snapshot import LegalSnapshot, LegalDataUnavailable, REQUIREMENT_CANDIDATES

T0 = datetime(2026, 6, 21)
EXPECTED_CANDIDATES = (
    "product_certification", "trademark_usage", "labeling_requirements",
    "marketplace_offer_terms", "return_policy_obligations", "content_claim_risk",
)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _counts(db):
    n_a = (await db.execute(select(func.count()).select_from(LegalAudit))).scalar()
    n_f = (await db.execute(select(func.count()).select_from(LegalFinding))).scalar()
    n_s = (await db.execute(select(func.count()).select_from(LegalSignal))).scalar()
    return n_a, n_f, n_s


# ── 1. snapshot built without writing to DB; no findings/signals ─────────────

def test_snapshot_builds_without_db_writes():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(Product(user_id=uid, name="Чайник", marketplace="wildberries", sku="SKU1", category="Кухня"))
        await db.commit()
        snap = await build_snapshot_from_internal(
            db, seller_id=uid, marketplace="wildberries", subject_type="product",
            subject_ref="SKU1", now=T0)
        assert isinstance(snap, LegalSnapshot)
        assert snap.source == "internal" and snap.snapshot_created_at == T0
        assert (await _counts(db)) == (0, 0, 0)   # nothing persisted
    _run(go())


# ── 2. missing inputs are NOT treated as compliance ──────────────────────────

def test_missing_inputs_not_compliance():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(Product(user_id=uid, name="Чайник", marketplace="wildberries", sku="SKU1", category="Кухня"))
        await db.commit()
        snap = await build_snapshot_from_internal(
            db, seller_id=uid, marketplace="wildberries", subject_ref="SKU1", now=T0)
        # certificate_data is never stored → product_certification cannot be evaluated,
        # and that is reported as not_evaluated, NOT as "compliant"/"ok"/absent risk
        assert "certificate_data" in snap.missing_inputs
        assert "product_certification" in snap.not_evaluated_reasons
        assert "missing_inputs" in snap.not_evaluated_reasons["product_certification"]
        # no field anywhere claims compliance / ok / passed
        for k in snap.field_availability:
            assert "complian" not in k.lower() and k.lower() != "ok"
    _run(go())


# ── 3. not_evaluated reasons present ─────────────────────────────────────────

def test_not_evaluated_reasons_present():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # empty sources → every requirement lacks inputs
        snap = await build_snapshot_from_internal(
            db, seller_id=uid, marketplace="ozon", subject_ref="NOPE", now=T0)
        assert snap.status == "not_evaluated_ready"
        assert set(snap.not_evaluated_reasons.keys()) == set(EXPECTED_CANDIDATES)
        for req, reason in snap.not_evaluated_reasons.items():
            assert reason and "missing_inputs" in reason
    _run(go())


# ── 4. marketplace agnostic ──────────────────────────────────────────────────

def test_marketplace_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            snap = await build_snapshot_from_internal(
                db, seller_id=uid, marketplace=mp, subject_ref="SKU1", now=T0)
            assert isinstance(snap, LegalSnapshot) and snap.marketplace == mp
            assert snap.requirement_candidates == EXPECTED_CANDIDATES
    _run(go())


# ── 5. no score / guarantee / forecast fields ────────────────────────────────

def test_no_score_guarantee_forecast_fields():
    names = {f.name for f in dc_fields(LegalSnapshot)}
    for bad in ("score", "legal_score", "guarantee", "forecast", "compliance", "verdict"):
        assert bad not in names, bad


# ── 6. requirement_candidates stable ─────────────────────────────────────────

def test_requirement_candidates_stable():
    assert REQUIREMENT_CANDIDATES == EXPECTED_CANDIDATES
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        snap = await build_snapshot_from_internal(
            db, seller_id=uid, marketplace="wb", subject_ref="X", now=T0)
        assert snap.requirement_candidates == EXPECTED_CANDIDATES
    _run(go())


# ── 7. status ready when at least one requirement has all inputs ─────────────

def test_status_ready_with_partial_data():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # category + name present → labeling_requirements (needs only product_category)
        # becomes evaluable → status ready; certificate-based ones stay not_evaluated
        db.add(Product(user_id=uid, name="Чайник", marketplace="ozon", sku="SKU1", category="Кухня"))
        await db.commit()
        snap = await build_snapshot_from_internal(
            db, seller_id=uid, marketplace="ozon", subject_ref="SKU1", now=T0)
        assert snap.status == "ready"
        assert "product_category" in snap.available_inputs
        assert "labeling_requirements" not in snap.not_evaluated_reasons
        assert "product_certification" in snap.not_evaluated_reasons   # certificate_data missing
        assert (await _counts(db)) == (0, 0, 0)
    _run(go())


# ── 8. honest degradation: insufficient_data / no_db_context ─────────────────

def test_honest_degradation():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        bad = await build_snapshot_from_internal(db, seller_id=uid, marketplace="wb")
        assert isinstance(bad, LegalDataUnavailable) and bad.reason == "insufficient_data"
    _run(go())
    nodb = _run(build_snapshot_from_internal(None, seller_id="u", marketplace="wb", subject_ref="X"))
    assert isinstance(nodb, LegalDataUnavailable) and nodb.reason == "no_db_context"


# ── 9. works with completely empty sources ───────────────────────────────────

def test_works_with_empty_sources():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        snap = await build_snapshot_from_internal(
            db, seller_id=uid, marketplace="yandex", subject_type="product",
            subject_ref="GHOST", now=T0)
        assert isinstance(snap, LegalSnapshot)
        assert snap.status == "not_evaluated_ready"
        assert snap.requirement_candidates == EXPECTED_CANDIDATES
        assert "marketplace" in snap.available_inputs   # marketplace always known
        assert (await _counts(db)) == (0, 0, 0)
    _run(go())
