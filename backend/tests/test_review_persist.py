"""
Review A5 — persistence + Signal Builder tests.

persist creates audit + full 6-rule ledger; triggered → review_problem + 5-part
review_signal; not_triggered/not_evaluated → ledger only; builder deterministic;
insight_key includes review_id; safety_mode per category (RISK manual_only,
ATTENTION/SAFE manual_approval, never auto); no fake impact; already_answered is
a status; no AI; agnostic; no MP client imports.
"""
import ast
import asyncio
import inspect
import json
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.review_audit import ReviewAudit
from models.review_problem import ReviewProblem
from models.review_rule_evaluation import ReviewRuleEvaluation
from models.review_signal import ReviewSignal

from services.review.snapshot import ReviewSnapshot
from services.review.evaluation import RuleEvaluation, RuleResult
from services.review.signal_builder import build_signal
from services.review import audit_persist
from services.review.audit_persist import audit_and_persist
from services.review.safety_policy import OFF, MANUAL_APPROVAL, AUTO, MANUAL_ONLY, SAFE, ATTENTION, RISK

T0 = datetime(2026, 6, 21)
_FIELDS = ("rating", "text", "has_text", "answered", "answer_text", "answer_created_at",
           "product_name", "brand", "category", "safety_category")


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _snap(*, mp="wildberries", rating=1, text="пришёл брак", has_text=None, answered=False,
          safety_category=RISK, allowed=(OFF, MANUAL_ONLY), default=MANUAL_ONLY, review_id="rev-1"):
    if has_text is None:
        has_text = bool(text and text.strip())
    return ReviewSnapshot(
        listing_id=None, marketplace=mp, sku="SKU1", captured_at=T0, source="reviews",
        review_id=review_id, rating=rating, text=text, has_text=has_text, created_at=T0,
        answered=answered, answer_text=None, answer_created_at=None,
        product_name="P", brand=None, category="Кухня", safety_category=safety_category,
        allowed_modes=allowed, default_mode=default, field_availability={k: True for k in _FIELDS})


# ── 1. audit + 6 ledger rows ─────────────────────────────────────────────────

def test_persist_audit_and_ledger():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap())
        await db.commit()
        audit = (await db.execute(select(ReviewAudit).where(ReviewAudit.id == res.audit_id))).scalar_one()
        assert audit.status == "completed" and audit.source == "reviews" and audit.snapshot_hash
        ledger = (await db.execute(select(ReviewRuleEvaluation).where(
            ReviewRuleEvaluation.audit_id == res.audit_id))).scalars().all()
        assert len(ledger) == 6 and res.rule_evaluation_count == 6
    _run(go())


# ── 2/3/4. problem only for triggered; ledger for all ────────────────────────

def test_problem_only_for_triggered():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap())  # RISK + complaint, unanswered
        await db.commit()
        ledger = {r.problem_type: r for r in (await db.execute(select(ReviewRuleEvaluation).where(
            ReviewRuleEvaluation.audit_id == res.audit_id))).scalars().all()}
        problems = {p.problem_type for p in (await db.execute(select(ReviewProblem).where(
            ReviewProblem.audit_id == res.audit_id))).scalars().all()}
        assert "unanswered_negative_review" in problems and "complaint_detected" in problems
        assert ledger["unanswered_negative_review"].result == "triggered" and ledger["unanswered_negative_review"].evidence
        # safe/already_answered not triggered → ledger only, no problem
        assert ledger["already_answered"].result == "not_triggered"
        assert "already_answered" not in problems
        assert len(problems) < 6
    _run(go())


# ── 5. signal 5 doctrine parts + safety fields ───────────────────────────────

def test_signal_doctrine_and_safety():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap())
        await db.commit()
        sigs = (await db.execute(select(ReviewSignal).where(
            ReviewSignal.audit_id == res.audit_id))).scalars().all()
        s = next(x for x in sigs if x.problem_type == "unanswered_negative_review")
        assert s.what and s.why and s.meaning and s.what_to_do and s.expected_effect
        assert s.status == "active" and s.evidence_hash
        assert s.safety_category == "RISK" and s.safety_mode == "manual_only"
        assert s.review_id == "rev-1"
        assert s.insight_key == "rev_unanswered_negative_review:wildberries:SKU1:rev-1"
    _run(go())


# ── 6. builder deterministic ─────────────────────────────────────────────────

def test_builder_deterministic():
    ev = RuleEvaluation("unanswered_negative_review", "RISK", "critical", "reputation_risk",
                        "reviews", RuleResult.TRIGGERED,
                        evidence={"rating": 1, "safety_category": "RISK", "default_mode": "manual_only"})
    a = build_signal(ev, marketplace="ozon", sku="SKU1", review_id="r9")
    b = build_signal(ev, marketplace="ozon", sku="SKU1", review_id="r9")
    assert a == b
    assert a.insight_key == "rev_unanswered_negative_review:ozon:SKU1:r9"


# ── 7. insight_key includes review_id ────────────────────────────────────────

def test_insight_key_includes_review_id():
    ev = RuleEvaluation("complaint_detected", "RISK", "critical", "reputation_risk",
                        "requires_text", RuleResult.TRIGGERED,
                        evidence={"safety_category": "RISK", "default_mode": "manual_only"})
    d = build_signal(ev, marketplace="yandex", sku="S", review_id="rev-42")
    assert d.insight_key == "rev_complaint_detected:yandex:S:rev-42"


# ── 8/9/10. safety_mode mapping (never auto) ─────────────────────────────────

def test_safety_mode_mapping():
    def mode(pt, cat, default):
        ev = RuleEvaluation(pt, cat, "low", "reputation_risk", "reviews", RuleResult.TRIGGERED,
                            evidence={"safety_category": cat, "default_mode": default})
        return build_signal(ev, marketplace="wb", sku="S", review_id="r").safety_mode
    assert mode("unanswered_negative_review", "RISK", "manual_only") == "manual_only"
    assert mode("unanswered_attention_review", "ATTENTION", "manual_approval") == "manual_approval"
    assert mode("safe_review_can_reply", "SAFE", "manual_approval") == "manual_approval"
    # builder uses default_mode (never auto-forced)
    assert mode("safe_review_can_reply", "SAFE", "manual_approval") != "auto"


# ── 11. no fake impact language ──────────────────────────────────────────────

def test_no_fake_impact():
    for pt in ("unanswered_negative_review", "unanswered_attention_review", "safe_review_can_reply",
               "five_star_without_text", "complaint_detected", "already_answered"):
        ev = RuleEvaluation(pt, "RISK", "low", "reputation_risk", "reviews", RuleResult.TRIGGERED,
                            evidence={"rating": 1, "safety_category": "RISK", "default_mode": "manual_only",
                                      "complaint_markers_found": []})
        d = build_signal(ev, marketplace="wb", sku="S", review_id="r")
        low = (d.expected_effect + " " + d.what + " " + d.meaning).lower()
        for bad in ("рейтинг", "продаж", "гарант", "повыс"):
            assert bad not in low, f"{bad} in {pt}"


# ── 12. already_answered is a status, not a problem ──────────────────────────

def test_already_answered_is_status():
    ev = RuleEvaluation("already_answered", "STATUS", "low", "none", "reviews",
                        RuleResult.TRIGGERED,
                        evidence={"safety_category": "SAFE", "default_mode": "manual_approval"})
    d = build_signal(ev, marketplace="wb", sku="S", review_id="r")
    assert d.recommended_action_key == "no_action"
    assert "обработан" in d.what.lower() and "не требует" in d.what_to_do.lower()


# ── 13. no AI / generation ───────────────────────────────────────────────────

def test_no_ai_generation():
    core_dir = Path(inspect.getfile(audit_persist)).parent
    for path in core_dir.rglob("*.py"):
        src = path.read_text(encoding="utf-8").lower()
        for bad in ("openai", "anthropic", "llm", "generate_reply", "gpt"):
            assert bad not in src


# ── 14. marketplace agnostic ─────────────────────────────────────────────────

def test_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            res = await audit_and_persist(db, user_id=uid, snapshot=_snap(mp=mp), now=T0)
            await db.commit()
            sig = (await db.execute(select(ReviewSignal).where(
                ReviewSignal.audit_id == res.audit_id))).scalars().first()
            assert sig.insight_key.startswith("rev_") and f":{mp}:" in sig.insight_key
    _run(go())


# ── 15. core imports no marketplace clients ──────────────────────────────────

def test_core_no_marketplace_client_imports():
    core_dir = Path(inspect.getfile(audit_persist)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "action_catalog", "credential_vault")
    offenders = []
    for path in core_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                for bad in forbidden:
                    if bad in m:
                        offenders.append(f"{path.name}:{bad}")
    assert not offenders, offenders
