"""
Legal A2 — data foundation schema tests.

Four append-only Legal tables: round-trip audit → finding → ledger → signal;
coverage ledger distinguishes "no risk observed" (not_detected) from "not
evaluated" (not_evaluated); marketplace-agnostic; detection tables append-only
(signal is the lifecycle entity); insight_key shaped for the Decision Spine
(legal_<requirement_type>:<marketplace>:<sku>); legal categories storable; signal
lifecycle incl. acknowledged; NO score / NO internal_health_index / NO legal
conclusion fields. No engine, no rule logic.
"""
import asyncio
import json
import uuid

from sqlalchemy import select, inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.legal_audit import LegalAudit
from models.legal_finding import LegalFinding
from models.legal_rule_evaluation import LegalRuleEvaluation
from models.legal_signal import LegalSignal

LEGAL_CATEGORIES = ("marking", "certification", "labeling", "ip", "tax", "content", "prohibited")


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── round-trip: audit → finding → ledger → signal ────────────────────────────

def test_full_legal_record_roundtrip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); lid = str(uuid.uuid4())
        audit = LegalAudit(user_id=uid, listing_id=lid, marketplace="ozon", sku="SKU1",
                           subject_type="product", subject_ref="SKU1", source="listing",
                           status="completed", rule_catalog_version="v1",
                           total_findings=1, total_not_evaluated=1, top_severity="high",
                           triggered_by="manual")
        db.add(audit); await db.flush()

        finding = LegalFinding(audit_id=audit.id, user_id=uid, listing_id=lid,
                               marketplace="ozon", sku="SKU1", subject_type="product",
                               subject_ref="SKU1", requirement_type="mark_required",
                               category="marking", severity="high", risk_level="high",
                               estimated_effect_type="block_risk", detectability="listing",
                               evidence=json.dumps({"category_requires_mark": True, "mark_present": False}))
        db.add(finding); await db.flush()

        ledger = LegalRuleEvaluation(audit_id=audit.id, user_id=uid, listing_id=lid,
                                     requirement_type="mark_required", result="detected",
                                     evidence=json.dumps({"mark_present": False}))
        db.add(ledger)

        signal = LegalSignal(audit_id=audit.id, finding_id=finding.id, user_id=uid,
                             listing_id=lid, marketplace="ozon", sku="SKU1",
                             subject_type="product", subject_ref="SKU1",
                             signal_key="legal_mark_required",
                             insight_key="legal_mark_required:ozon:SKU1",
                             requirement_type="mark_required", category="marking",
                             recommended_action_key="check_requirement",
                             alternative_action_keys=json.dumps(["consult_lawyer"]),
                             what="...", why="...", meaning="...",
                             what_to_do="Проверить, нужна ли маркировка для этой категории.",
                             expected_effect="может снизить риск блокировки карточки",
                             priority_level="high", risk_level="high", effect_type="block_risk",
                             effect_band="high", confidence=0.7, status="active")
        db.add(signal); await db.commit()

        a = (await db.execute(select(LegalAudit).where(LegalAudit.id == audit.id))).scalar_one()
        f = (await db.execute(select(LegalFinding).where(LegalFinding.audit_id == audit.id))).scalar_one()
        s = (await db.execute(select(LegalSignal).where(LegalSignal.audit_id == audit.id))).scalar_one()
        assert a.source == "listing" and a.total_not_evaluated == 1 and a.subject_type == "product"
        assert f.category == "marking" and f.requirement_type == "mark_required" and f.risk_level == "high"
        assert s.insight_key == "legal_mark_required:ozon:SKU1"
        assert s.effect_type == "block_risk" and s.status == "active"
        assert s.signal_key.startswith("legal_")
        # No Fake Impact / no legal conclusion language baked into the schema
        assert "гарант" not in (s.expected_effect or "")
    _run(go())


# ── legal categories storable ────────────────────────────────────────────────

def test_legal_categories_storable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        for cat in LEGAL_CATEGORIES:
            db.add(LegalFinding(audit_id=aid, user_id=uid, requirement_type=f"req_{cat}",
                                category=cat, severity="medium"))
        await db.commit()
        cats = {f.category for f in (await db.execute(select(LegalFinding))).scalars().all()}
        assert cats == set(LEGAL_CATEGORIES)
    _run(go())


# ── coverage ledger: not_detected vs not_evaluated ───────────────────────────

def test_ledger_distinguishes_not_detected_from_not_evaluated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        for rt, res, reason in [
            ("mark_required", "detected", None),
            ("certificate_required", "not_detected", None),
            ("ip_conflict", "not_evaluated", "missing_fields: brand"),
        ]:
            db.add(LegalRuleEvaluation(audit_id=aid, user_id=uid, requirement_type=rt,
                                       result=res, reason=reason))
        await db.commit()
        got = {r.requirement_type: (r.result, r.reason) for r in
               (await db.execute(select(LegalRuleEvaluation).where(
                   LegalRuleEvaluation.audit_id == aid))).scalars().all()}
        assert got["certificate_required"][0] == "not_detected"
        assert got["ip_conflict"][0] == "not_evaluated"
        assert "missing_fields" in got["ip_conflict"][1]
        assert got["mark_required"][0] == "detected"
    _run(go())


def test_ledger_unique_per_rule_per_audit():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(LegalRuleEvaluation(audit_id=aid, user_id=uid, requirement_type="mark_required",
                                   result="detected"))
        await db.commit()
        db.add(LegalRuleEvaluation(audit_id=aid, user_id=uid, requirement_type="mark_required",
                                   result="not_detected"))
        raised = False
        try:
            await db.commit()
        except Exception:
            raised = True; await db.rollback()
        assert raised
    _run(go())


# ── marketplace agnostic ─────────────────────────────────────────────────────

def test_marketplace_agnostic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for mp in ("wildberries", "ozon", "yandex"):
            db.add(LegalAudit(user_id=uid, marketplace=mp, status="completed"))
        await db.commit()
        mps = {a.marketplace for a in (await db.execute(select(LegalAudit))).scalars().all()}
        assert mps == {"wildberries", "ozon", "yandex"}
    _run(go())


# ── signal lifecycle: status mutates incl. acknowledged; updated_at present ──

def test_signal_lifecycle_mutates():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        sig = LegalSignal(audit_id=aid, user_id=uid, signal_key="legal_x",
                          requirement_type="x", category="marking", status="active")
        db.add(sig); await db.commit()
        sig.status = "acknowledged"; await db.commit()
        sig.status = "promoted_to_decision"; sig.decision_id = str(uuid.uuid4())
        await db.commit()
        s = (await db.execute(select(LegalSignal).where(LegalSignal.id == sig.id))).scalar_one()
        assert s.status == "promoted_to_decision" and s.decision_id
    _run(go())


# ── append-only detection + Decision/Learning/Effect compat + no score ───────

def test_detection_append_only_compat_and_no_score():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    for model in (LegalAudit, LegalFinding, LegalRuleEvaluation):
        assert "updated_at" not in cols(model), f"{model.__tablename__} must be append-only"
    sig = cols(LegalSignal)
    assert "updated_at" in sig                 # lifecycle entity
    assert "decision_id" in sig                # Decision Spine compat
    assert "insight_key" in sig                # Learning OS compat
    assert "effect_type" in sig and "effect_band" in sig   # Effect PULT compat
    # no public score / health index, no fabricated guarantee fields anywhere
    for model in (LegalAudit, LegalFinding, LegalRuleEvaluation, LegalSignal):
        c = cols(model)
        for bad in ("score", "legal_score", "internal_health_index", "guarantee", "forecast"):
            assert bad not in c, f"{model.__tablename__}.{bad}"
