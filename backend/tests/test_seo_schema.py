"""
SEO A2 — data foundation schema tests.

Verifies the four append-only SEO tables exist and round-trip, that the
coverage ledger distinguishes "problem not found" (not_triggered) from "problem
not evaluated" (not_evaluated), that the layer is marketplace-agnostic (plain
string column, all three MPs accepted), and that detection tables are append-only
(no `updated_at`). No engine, no rule logic — schema only.
"""
import asyncio
import uuid
import json

from sqlalchemy import select, inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.seo_audit import SeoAudit
from models.seo_problem import SeoProblem
from models.seo_rule_evaluation import SeoRuleEvaluation
from models.seo_signal import SeoSignal


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── round-trip: full audit → problem → ledger → signal ───────────────────────

def test_full_seo_record_roundtrip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); lid = str(uuid.uuid4())
        audit = SeoAudit(user_id=uid, listing_id=lid, marketplace="ozon", sku="SKU1",
                         status="completed", rule_catalog_version="v1",
                         total_problems=1, total_not_evaluated=1, top_severity="critical",
                         triggered_by="manual", score=72.0)
        db.add(audit); await db.flush()

        problem = SeoProblem(audit_id=audit.id, user_id=uid, listing_id=lid, marketplace="ozon",
                             sku="SKU1", problem_type="required_attributes_missing",
                             category="Attributes", severity="critical",
                             estimated_effect_type="filter_exclusion", detectability="static_card",
                             evidence=json.dumps({"missing": ["colour", "material"]}))
        db.add(problem); await db.flush()

        signal = SeoSignal(audit_id=audit.id, problem_id=problem.id, user_id=uid, listing_id=lid,
                           marketplace="ozon", sku="SKU1",
                           signal_key="seo_required_attributes_missing",
                           insight_key="seo_required_attributes_missing:ozon:SKU1",
                           problem_type="required_attributes_missing",
                           recommended_action_key="complete_required_fields",
                           what="...", why="...", meaning="...", what_to_do="...",
                           expected_effect="filter_inclusion",
                           priority_level="critical", expected_effect_type="filter_inclusion",
                           effect_band="high", status="active")
        db.add(signal); await db.commit()

        a = (await db.execute(select(SeoAudit).where(SeoAudit.id == audit.id))).scalar_one()
        p = (await db.execute(select(SeoProblem).where(SeoProblem.audit_id == audit.id))).scalar_one()
        s = (await db.execute(select(SeoSignal).where(SeoSignal.audit_id == audit.id))).scalar_one()
        assert a.score == 72.0 and a.total_not_evaluated == 1
        assert json.loads(p.evidence)["missing"] == ["colour", "material"]
        assert s.insight_key == "seo_required_attributes_missing:ozon:SKU1"
        assert s.status == "active"
    _run(go())


# ── the core requirement: not_found vs not_evaluated are distinguishable ──────

def test_coverage_ledger_distinguishes_not_found_from_not_evaluated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        rows = [
            SeoRuleEvaluation(audit_id=aid, user_id=uid, problem_type="title_too_short",
                              result="triggered"),
            SeoRuleEvaluation(audit_id=aid, user_id=uid, problem_type="title_too_long",
                              result="not_triggered"),              # ran, problem NOT found
            SeoRuleEvaluation(audit_id=aid, user_id=uid, problem_type="wrong_category_placement",
                              result="not_evaluated", reason="missing_fields: expected_category_path"),
        ]
        for r in rows:
            db.add(r)
        await db.commit()

        got = {r.problem_type: (r.result, r.reason) for r in
               (await db.execute(select(SeoRuleEvaluation).where(
                   SeoRuleEvaluation.audit_id == aid))).scalars().all()}
        # "not found" is explicit, not inferred from absence
        assert got["title_too_long"][0] == "not_triggered"
        # "not evaluated" is explicit and carries a reason
        assert got["wrong_category_placement"][0] == "not_evaluated"
        assert "missing_fields" in got["wrong_category_placement"][1]
        assert got["title_too_short"][0] == "triggered"
    _run(go())


def test_ledger_unique_per_rule_per_audit():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(SeoRuleEvaluation(audit_id=aid, user_id=uid, problem_type="title_too_short",
                                 result="triggered"))
        await db.commit()
        db.add(SeoRuleEvaluation(audit_id=aid, user_id=uid, problem_type="title_too_short",
                                 result="not_triggered"))
        raised = False
        try:
            await db.commit()
        except Exception:
            raised = True
            await db.rollback()
        assert raised  # UniqueConstraint(audit_id, problem_type)
    _run(go())


# ── marketplace-agnostic: plain string, all three MPs accepted equally ────────

def test_marketplace_agnostic():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for mp in ("wildberries", "ozon", "yandex"):
            db.add(SeoAudit(user_id=uid, marketplace=mp, status="completed"))
        await db.commit()
        mps = {a.marketplace for a in
               (await db.execute(select(SeoAudit))).scalars().all()}
        assert mps == {"wildberries", "ozon", "yandex"}  # no enum/whitelist
    _run(go())


# ── append-only contract: detection tables have no updated_at ─────────────────

def test_append_only_no_updated_at():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    for model in (SeoAudit, SeoProblem, SeoRuleEvaluation, SeoSignal):
        assert "updated_at" not in cols(model), f"{model.__tablename__} must be append-only"
