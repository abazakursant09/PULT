"""
Review A2 — data foundation schema tests.

Four append-only Review tables: round-trip; coverage ledger distinguishes
"problem not found" (not_triggered) from "problem not evaluated" (not_evaluated);
marketplace-agnostic; detection tables append-only (signal is the lifecycle
entity); insight_key shaped for the Decision Spine; safety_mode/category carry
the Human-Control + Negative-Review doctrine. No engine, no rule logic.
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
from models.review_audit import ReviewAudit
from models.review_problem import ReviewProblem
from models.review_rule_evaluation import ReviewRuleEvaluation
from models.review_signal import ReviewSignal


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── round-trip: audit → problem → ledger → signal ────────────────────────────

def test_full_review_record_roundtrip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); lid = str(uuid.uuid4()); rid = str(uuid.uuid4())
        audit = ReviewAudit(user_id=uid, listing_id=lid, marketplace="ozon", sku="SKU1",
                            source="reviews", status="completed", rule_catalog_version="v1",
                            total_problems=1, total_not_evaluated=1, top_severity="critical",
                            triggered_by="manual")
        db.add(audit); await db.flush()

        problem = ReviewProblem(audit_id=audit.id, user_id=uid, listing_id=lid, review_id=rid,
                                marketplace="ozon", sku="SKU1", problem_type="unanswered_negative",
                                category="RISK", severity="critical",
                                estimated_effect_type="reputation_risk", detectability="reviews",
                                evidence=json.dumps({"rating": 1, "has_text": True}))
        db.add(problem); await db.flush()

        signal = ReviewSignal(audit_id=audit.id, problem_id=problem.id, review_id=rid, user_id=uid,
                              listing_id=lid, marketplace="ozon", sku="SKU1",
                              signal_key="rev_unanswered_negative",
                              insight_key="rev_unanswered_negative:ozon:SKU1",
                              problem_type="unanswered_negative", safety_category="RISK",
                              safety_mode="manual_only", recommended_action_key="reply_manually",
                              alternative_action_keys=json.dumps(["escalate"]),
                              what="...", why="...", meaning="...", what_to_do="...",
                              expected_effect="может снизить репутационный риск",
                              priority_level="critical", expected_effect_type="reputation_risk",
                              effect_band="high", confidence=0.9, status="active")
        db.add(signal); await db.commit()

        a = (await db.execute(select(ReviewAudit).where(ReviewAudit.id == audit.id))).scalar_one()
        p = (await db.execute(select(ReviewProblem).where(ReviewProblem.audit_id == audit.id))).scalar_one()
        s = (await db.execute(select(ReviewSignal).where(ReviewSignal.audit_id == audit.id))).scalar_one()
        assert a.source == "reviews" and a.total_not_evaluated == 1
        assert p.category == "RISK" and p.review_id == rid
        assert s.insight_key == "rev_unanswered_negative:ozon:SKU1"
        # Negative-Review doctrine: RISK → manual_only, never auto
        assert s.safety_category == "RISK" and s.safety_mode == "manual_only"
        assert "повыс" not in (s.expected_effect or "")   # No Fake Impact: no rating promise
    _run(go())


# ── safety_mode supports the human-control doctrine values ───────────────────

def test_safety_modes_storable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for mode, cat in (("off", "SAFE"), ("manual_approval", "ATTENTION"),
                          ("auto", "SAFE"), ("manual_only", "RISK")):
            aid = str(uuid.uuid4())
            db.add(ReviewSignal(audit_id=aid, user_id=uid, signal_key=f"rev_{mode}",
                                problem_type="x", safety_category=cat, safety_mode=mode,
                                status="active"))
        await db.commit()
        modes = {s.safety_mode for s in (await db.execute(select(ReviewSignal))).scalars().all()}
        assert modes == {"off", "manual_approval", "auto", "manual_only"}
    _run(go())


# ── coverage ledger: not_found vs not_evaluated ──────────────────────────────

def test_ledger_distinguishes_not_found_from_not_evaluated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        for pt, res, reason in [
            ("unanswered_negative", "triggered", None),
            ("unanswered_neutral", "not_triggered", None),
            ("complaint_detected", "not_evaluated", "missing_fields: review_text"),
        ]:
            db.add(ReviewRuleEvaluation(audit_id=aid, user_id=uid, problem_type=pt,
                                        result=res, reason=reason))
        await db.commit()
        got = {r.problem_type: (r.result, r.reason) for r in
               (await db.execute(select(ReviewRuleEvaluation).where(
                   ReviewRuleEvaluation.audit_id == aid))).scalars().all()}
        assert got["unanswered_neutral"][0] == "not_triggered"
        assert got["complaint_detected"][0] == "not_evaluated"
        assert "missing_fields" in got["complaint_detected"][1]
        assert got["unanswered_negative"][0] == "triggered"
    _run(go())


def test_ledger_unique_per_rule_per_audit():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(ReviewRuleEvaluation(audit_id=aid, user_id=uid, problem_type="unanswered_negative",
                                    result="triggered"))
        await db.commit()
        db.add(ReviewRuleEvaluation(audit_id=aid, user_id=uid, problem_type="unanswered_negative",
                                    result="not_triggered"))
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
            db.add(ReviewAudit(user_id=uid, marketplace=mp, status="completed"))
        await db.commit()
        mps = {a.marketplace for a in (await db.execute(select(ReviewAudit))).scalars().all()}
        assert mps == {"wildberries", "ozon", "yandex"}
    _run(go())


# ── append-only contract: detection tables have no updated_at ────────────────

def test_detection_tables_append_only():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    for model in (ReviewAudit, ReviewProblem, ReviewRuleEvaluation):
        assert "updated_at" not in cols(model), f"{model.__tablename__} must be append-only"
    assert "updated_at" in cols(ReviewSignal)   # lifecycle entity
    assert "internal_health_index" not in cols(ReviewAudit)   # no public score
