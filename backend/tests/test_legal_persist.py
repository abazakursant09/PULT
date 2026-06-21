"""
Legal A5 — persistence + Signal Builder tests.

persist creates an append-only audit + full 6-row ledger; DETECTED → immutable
legal_finding + advisory legal_signal (5-part doctrine, qualitative effect); a
repeated run does not spawn duplicate ACTIVE signals; not_detected / not_evaluated
persist to the ledger only (no finding, no signal). No score / forecast /
guarantee / compliance / money. Advisory language only.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select, func, inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.imported_product import ImportedProductRow
from models.legal_audit import LegalAudit
from models.legal_finding import LegalFinding
from models.legal_rule_evaluation import LegalRuleEvaluation
from models.legal_signal import LegalSignal

from services.legal.persist import audit_and_persist, LegalPersistResult

T0 = datetime(2026, 6, 21)


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed_imported(db, uid, *, mp="wildberries", sku="SKU1", title):
    db.add(ImportedProductRow(import_id="imp1", user_id=uid, marketplace=mp, sku=sku, title=title))
    await db.flush()


async def _audit(db, uid, *, mp="wildberries", sku="SKU1"):
    return await audit_and_persist(db, seller_id=uid, marketplace=mp, subject_type="product",
                                   subject_ref=sku, sku=sku, now=T0)


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


# ── 1/2. audit created + all 6 evaluations saved ─────────────────────────────

def test_audit_and_full_ledger():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_imported(db, uid, title="Крем лечит 100%"); await db.commit()
        res = await _audit(db, uid); await db.commit()
        assert isinstance(res, LegalPersistResult)
        audit = (await db.execute(select(LegalAudit).where(LegalAudit.id == res.audit_id))).scalar_one()
        assert audit.status == "completed" and audit.snapshot_hash and audit.source == "internal"
        ledger = (await db.execute(select(LegalRuleEvaluation).where(
            LegalRuleEvaluation.audit_id == res.audit_id))).scalars().all()
        assert len(ledger) == 6 and res.rule_evaluation_count == 6
        results = {r.requirement_type: r.result for r in ledger}
        assert results["content_claim_risk"] == "detected"
        assert results["product_certification"] == "not_evaluated"
    _run(go())


# ── 3/4. detected → finding + signal ─────────────────────────────────────────

def test_detected_creates_finding_and_signal():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_imported(db, uid, title="Крем лечит 100% оригинал"); await db.commit()
        res = await _audit(db, uid); await db.commit()
        assert res.total_findings == 1 and res.signals_created == 1
        f = (await db.execute(select(LegalFinding))).scalars().one()
        assert f.requirement_type == "content_claim_risk" and f.category == "content"
        assert f.estimated_effect_type == "takedown_risk"
        s = (await db.execute(select(LegalSignal))).scalars().one()
        assert s.status == "active" and s.requirement_type == "content_claim_risk"
        assert s.insight_key == "legal_content_claim_risk:wildberries:SKU1"
    _run(go())


# ── 5. not_evaluated → ledger only, no finding/signal ────────────────────────

def test_not_evaluated_ledger_only():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # no imported row → content_claim_risk not_evaluated (no text); all 6 not_evaluated
        res = await audit_and_persist(db, seller_id=uid, marketplace="ozon",
                                      subject_type="product", subject_ref="SKU9", sku="SKU9", now=T0)
        await db.commit()
        assert res.total_findings == 0 and res.signals_created == 0
        assert res.total_not_evaluated == 6
        assert await _count(db, LegalFinding) == 0 and await _count(db, LegalSignal) == 0
        assert await _count(db, LegalRuleEvaluation) == 6
    _run(go())


# ── 6. not_detected → ledger only, no finding/signal ─────────────────────────

def test_not_detected_ledger_only():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_imported(db, uid, title="Чайник электрический синий"); await db.commit()
        res = await _audit(db, uid); await db.commit()
        ledger = {r.requirement_type: r.result for r in (await db.execute(
            select(LegalRuleEvaluation))).scalars().all()}
        assert ledger["content_claim_risk"] == "not_detected"
        assert res.total_findings == 0 and res.signals_created == 0
        assert await _count(db, LegalFinding) == 0 and await _count(db, LegalSignal) == 0
    _run(go())


# ── 7. repeated run does not spawn duplicate ACTIVE signal ───────────────────

def test_repeated_run_no_duplicate_active_signal():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_imported(db, uid, title="Крем лечит 100%"); await db.commit()
        r1 = await _audit(db, uid); await db.commit()
        r2 = await _audit(db, uid); await db.commit()
        assert r1.signals_created == 1
        assert r2.signals_created == 0 and r2.signals_unchanged == 1
        # exactly one live signal for the insight_key; audits/ledger appended
        live = (await db.execute(select(LegalSignal).where(
            LegalSignal.insight_key == "legal_content_claim_risk:wildberries:SKU1"))).scalars().all()
        assert len(live) == 1
        assert await _count(db, LegalAudit) == 2            # audit append-only
        assert await _count(db, LegalRuleEvaluation) == 12  # ledger append-only
        assert await _count(db, LegalFinding) == 2          # finding append-only/immutable
    _run(go())


# ── 8. detection tables append-only ──────────────────────────────────────────

def test_detection_tables_append_only():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    for model in (LegalAudit, LegalFinding, LegalRuleEvaluation):
        assert "updated_at" not in cols(model), f"{model.__tablename__} must be append-only"
    assert "updated_at" in cols(LegalSignal)   # lifecycle entity


# ── 9. signal carries the 5 doctrine parts ───────────────────────────────────

def test_signal_five_doctrine_parts():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_imported(db, uid, title="Крем лечит 100%"); await db.commit()
        await _audit(db, uid); await db.commit()
        s = (await db.execute(select(LegalSignal))).scalars().one()
        # what_happened / why_it_matters / meaning / recommended_action / expected_effect
        assert s.what and s.why and s.meaning and s.what_to_do and s.expected_effect
        assert s.recommended_action_key == "review_content_claim"
        assert s.effect_type == "takedown_risk_reduction"   # qualitative
        assert s.confidence is None                          # no numeric score
    _run(go())


# ── 10. no score / forecast / guarantee / compliance / money fields ──────────

def test_no_score_forecast_guarantee_money():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    for model in (LegalAudit, LegalFinding, LegalRuleEvaluation, LegalSignal):
        c = cols(model)
        for bad in ("score", "legal_score", "forecast", "guarantee", "compliance",
                    "expected_revenue", "effect_money", "rub"):
            assert bad not in c, f"{model.__tablename__}.{bad}"
    from dataclasses import fields as dc_fields
    rnames = {f.name for f in dc_fields(LegalPersistResult)}
    for bad in ("score", "forecast", "guarantee", "compliance", "money"):
        assert bad not in rnames


# ── 11. advisory language only (no guarantee / money / compliance claim) ─────

def test_advisory_language_only():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed_imported(db, uid, title="Крем лечит 100%"); await db.commit()
        await _audit(db, uid); await db.commit()
        s = (await db.execute(select(LegalSignal))).scalars().one()
        blob = " ".join([s.what, s.why, s.meaning, s.what_to_do, s.expected_effect]).lower()
        for bad in ("гаранти", "₽", "руб", "compliant", "соответствует требованиям закона",
                    "точно", "нарушение закона"):
            assert bad not in blob, bad
        assert "может снизить риск" in s.expected_effect.lower()
    _run(go())
