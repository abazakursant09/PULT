"""
Advertising A2 — data foundation schema tests.

Four append-only Advertising tables: round-trip; coverage ledger distinguishes
"problem not found" (not_triggered) from "problem not evaluated" (not_evaluated);
marketplace-agnostic; detection tables append-only (signal is the lifecycle
entity); insight_key shaped for the Decision Spine. No engine, no rule logic.
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
from models.advertising_audit import AdvertisingAudit
from models.advertising_problem import AdvertisingProblem
from models.advertising_rule_evaluation import AdvertisingRuleEvaluation
from models.advertising_signal import AdvertisingSignal


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


# ── round-trip: audit → problem → ledger → signal ────────────────────────────

def test_full_advertising_record_roundtrip():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); lid = str(uuid.uuid4())
        audit = AdvertisingAudit(user_id=uid, listing_id=lid, marketplace="ozon", sku="SKU1",
                                 source="finance", status="completed", rule_catalog_version="v1",
                                 total_problems=1, total_not_evaluated=1, top_severity="critical",
                                 triggered_by="manual")
        db.add(audit); await db.flush()

        problem = AdvertisingProblem(audit_id=audit.id, user_id=uid, listing_id=lid, marketplace="ozon",
                                     sku="SKU1", problem_type="ad_destroying_profit", category="Profitability",
                                     severity="critical", estimated_effect_type="margin_loss",
                                     detectability="finance",
                                     evidence=json.dumps({"drr": 38.0, "net_profit": -1200.0}))
        db.add(problem); await db.flush()

        signal = AdvertisingSignal(audit_id=audit.id, problem_id=problem.id, user_id=uid, listing_id=lid,
                                   marketplace="ozon", sku="SKU1", signal_key="adv_ad_destroying_profit",
                                   insight_key="adv_ad_destroying_profit:ozon:SKU1",
                                   problem_type="ad_destroying_profit",
                                   recommended_action_key="stop_auto_promotion",
                                   alternative_action_keys=json.dumps(["reduce_budget", "stop_ad_on_product"]),
                                   what="...", why="...", meaning="...", what_to_do="...",
                                   expected_effect="margin_loss", priority_level="critical",
                                   expected_effect_type="margin_loss", effect_band="high",
                                   confidence=0.9, status="active")
        db.add(signal); await db.commit()

        a = (await db.execute(select(AdvertisingAudit).where(AdvertisingAudit.id == audit.id))).scalar_one()
        p = (await db.execute(select(AdvertisingProblem).where(AdvertisingProblem.audit_id == audit.id))).scalar_one()
        s = (await db.execute(select(AdvertisingSignal).where(AdvertisingSignal.audit_id == audit.id))).scalar_one()
        assert a.source == "finance" and a.total_not_evaluated == 1
        assert json.loads(p.evidence)["net_profit"] == -1200.0
        assert s.insight_key == "adv_ad_destroying_profit:ozon:SKU1"   # Decision-bridge shape
        assert json.loads(s.alternative_action_keys) == ["reduce_budget", "stop_ad_on_product"]
        assert s.status == "active"
    _run(go())


# ── coverage ledger: not_found vs not_evaluated distinguishable ──────────────

def test_ledger_distinguishes_not_found_from_not_evaluated():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        for pt, res, reason in [
            ("ad_destroying_profit", "triggered", None),
            ("ad_spend_without_sales", "not_triggered", None),
            ("ad_on_low_stock", "not_evaluated", "missing_fields: stock"),
        ]:
            db.add(AdvertisingRuleEvaluation(audit_id=aid, user_id=uid, problem_type=pt,
                                             result=res, reason=reason))
        await db.commit()
        got = {r.problem_type: (r.result, r.reason) for r in
               (await db.execute(select(AdvertisingRuleEvaluation).where(
                   AdvertisingRuleEvaluation.audit_id == aid))).scalars().all()}
        assert got["ad_spend_without_sales"][0] == "not_triggered"
        assert got["ad_on_low_stock"][0] == "not_evaluated"
        assert "missing_fields" in got["ad_on_low_stock"][1]
        assert got["ad_destroying_profit"][0] == "triggered"
    _run(go())


def test_ledger_unique_per_rule_per_audit():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4()); aid = str(uuid.uuid4())
        db.add(AdvertisingRuleEvaluation(audit_id=aid, user_id=uid, problem_type="ad_destroying_profit",
                                         result="triggered"))
        await db.commit()
        db.add(AdvertisingRuleEvaluation(audit_id=aid, user_id=uid, problem_type="ad_destroying_profit",
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
            db.add(AdvertisingAudit(user_id=uid, marketplace=mp, status="completed"))
        await db.commit()
        mps = {a.marketplace for a in (await db.execute(select(AdvertisingAudit))).scalars().all()}
        assert mps == {"wildberries", "ozon", "yandex"}
    _run(go())


# ── append-only contract: detection tables have no updated_at ────────────────

def test_detection_tables_append_only():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    for model in (AdvertisingAudit, AdvertisingProblem, AdvertisingRuleEvaluation):
        assert "updated_at" not in cols(model), f"{model.__tablename__} must be append-only"
    assert "updated_at" in cols(AdvertisingSignal)   # lifecycle entity
    # money-first: no public health index
    assert "internal_health_index" not in cols(AdvertisingAudit)
