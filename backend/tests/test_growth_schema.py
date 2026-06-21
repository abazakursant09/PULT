"""
Growth A2 — data foundation schema tests.

Four append-only Growth tables: round-trip audit → problem → ledger → signal;
coverage ledger distinguishes "opportunity not present" (not_triggered) from
"not evaluated" (not_evaluated); marketplace-agnostic; detection tables
append-only (signal is the lifecycle entity); insight_key shaped for the Decision
Spine (growth_<problem_type>:<marketplace>:<sku>); growth categories storable;
NO score / NO internal_health_index. No engine, no rule logic.
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
from models.growth_audit import GrowthAudit
from models.growth_problem import GrowthProblem
from models.growth_rule_evaluation import GrowthRuleEvaluation
from models.growth_signal import GrowthSignal

GROWTH_CATEGORIES = ("pricing", "advertising", "seo", "inventory", "reputation")


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── round-trip: audit → problem → ledger → signal ────────────────────────────

def test_full_growth_record_roundtrip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); lid = str(uuid.uuid4())
        audit = GrowthAudit(user_id=uid, listing_id=lid, marketplace="ozon", sku="SKU1",
                            source="finance", status="completed", rule_catalog_version="v1",
                            total_problems=1, total_not_evaluated=1, top_severity="high",
                            triggered_by="manual")
        db.add(audit); await db.flush()

        problem = GrowthProblem(audit_id=audit.id, user_id=uid, listing_id=lid,
                                marketplace="ozon", sku="SKU1", problem_type="price_below_market_floor",
                                category="pricing", severity="high",
                                estimated_effect_type="margin_gain", detectability="finance",
                                evidence=json.dumps({"current_price": 900, "floor": 1100}))
        db.add(problem); await db.flush()

        ledger = GrowthRuleEvaluation(audit_id=audit.id, user_id=uid, listing_id=lid,
                                      problem_type="price_below_market_floor", result="triggered",
                                      evidence=json.dumps({"gap": 200}))
        db.add(ledger)

        signal = GrowthSignal(audit_id=audit.id, problem_id=problem.id, user_id=uid,
                              listing_id=lid, marketplace="ozon", sku="SKU1",
                              signal_key="growth_price_below_market_floor",
                              insight_key="growth_price_below_market_floor:ozon:SKU1",
                              problem_type="price_below_market_floor", category="pricing",
                              recommended_action_key="raise_price",
                              alternative_action_keys=json.dumps(["test_price"]),
                              what="...", why="...", meaning="...", what_to_do="...",
                              expected_effect="можно вернуть недополученную маржу",
                              priority_level="high", effect_type="margin_gain",
                              effect_band="high", confidence=0.8, status="active")
        db.add(signal); await db.commit()

        a = (await db.execute(select(GrowthAudit).where(GrowthAudit.id == audit.id))).scalar_one()
        p = (await db.execute(select(GrowthProblem).where(GrowthProblem.audit_id == audit.id))).scalar_one()
        s = (await db.execute(select(GrowthSignal).where(GrowthSignal.audit_id == audit.id))).scalar_one()
        assert a.source == "finance" and a.total_not_evaluated == 1
        assert p.category == "pricing" and p.estimated_effect_type == "margin_gain"
        assert s.insight_key == "growth_price_below_market_floor:ozon:SKU1"
        assert s.effect_type == "margin_gain" and s.status == "active"
        assert s.signal_key.startswith("growth_")
    _run(go())


# ── growth categories are storable ───────────────────────────────────────────

def test_growth_categories_storable():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        for cat in GROWTH_CATEGORIES:
            db.add(GrowthProblem(audit_id=aid, user_id=uid, problem_type=f"opp_{cat}",
                                 category=cat, severity="medium"))
        await db.commit()
        cats = {p.category for p in (await db.execute(select(GrowthProblem))).scalars().all()}
        assert cats == set(GROWTH_CATEGORIES)
    _run(go())


# ── coverage ledger: not_present vs not_evaluated ────────────────────────────

def test_ledger_distinguishes_not_present_from_not_evaluated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        for pt, res, reason in [
            ("price_below_market_floor", "triggered", None),
            ("ad_budget_underspent", "not_triggered", None),
            ("seo_title_gap", "not_evaluated", "missing_fields: title"),
        ]:
            db.add(GrowthRuleEvaluation(audit_id=aid, user_id=uid, problem_type=pt,
                                        result=res, reason=reason))
        await db.commit()
        got = {r.problem_type: (r.result, r.reason) for r in
               (await db.execute(select(GrowthRuleEvaluation).where(
                   GrowthRuleEvaluation.audit_id == aid))).scalars().all()}
        assert got["ad_budget_underspent"][0] == "not_triggered"
        assert got["seo_title_gap"][0] == "not_evaluated"
        assert "missing_fields" in got["seo_title_gap"][1]
        assert got["price_below_market_floor"][0] == "triggered"
    _run(go())


def test_ledger_unique_per_rule_per_audit():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(GrowthRuleEvaluation(audit_id=aid, user_id=uid, problem_type="price_below_market_floor",
                                    result="triggered"))
        await db.commit()
        db.add(GrowthRuleEvaluation(audit_id=aid, user_id=uid, problem_type="price_below_market_floor",
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
            db.add(GrowthAudit(user_id=uid, marketplace=mp, status="completed"))
        await db.commit()
        mps = {a.marketplace for a in (await db.execute(select(GrowthAudit))).scalars().all()}
        assert mps == {"wildberries", "ozon", "yandex"}
    _run(go())


# ── signal lifecycle: status mutates, updated_at present ─────────────────────

def test_signal_lifecycle_mutates():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        sig = GrowthSignal(audit_id=aid, user_id=uid, signal_key="growth_x",
                           problem_type="x", category="pricing", status="active")
        db.add(sig); await db.commit()
        sig.status = "promoted_to_decision"; sig.decision_id = str(uuid.uuid4())
        await db.commit()
        s = (await db.execute(select(GrowthSignal).where(GrowthSignal.id == sig.id))).scalar_one()
        assert s.status == "promoted_to_decision" and s.decision_id
    _run(go())


# ── append-only contract + no score ──────────────────────────────────────────

def test_detection_append_only_and_no_score():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    for model in (GrowthAudit, GrowthProblem, GrowthRuleEvaluation):
        assert "updated_at" not in cols(model), f"{model.__tablename__} must be append-only"
    assert "updated_at" in cols(GrowthSignal)   # lifecycle entity
    # no public score / health index anywhere
    for model in (GrowthAudit, GrowthProblem, GrowthRuleEvaluation, GrowthSignal):
        c = cols(model)
        assert "score" not in c
        assert "internal_health_index" not in c
        assert "growth_score" not in c
