"""
Growth A5 — persistence + Signal Builder tests.

persist creates audit + full 5-rule ledger; triggered → growth_problem + 5-part
growth_signal; not_triggered / not_evaluated → ledger only; builder deterministic;
insight_key growth_<type>:<mp>:<sku>; recommended_action_key per rule; no fake
impact; no growth score; no forecast/competitor/AI fields; marketplace agnostic.
No reconciliation (A6).
"""
import asyncio
import inspect
import json
import uuid
from datetime import datetime
from pathlib import Path

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

from services.growth.snapshot import GrowthSnapshot
from services.growth.evaluation import GrowthRuleEvaluation as Eval, RuleResult
from services.growth.signal_builder import build_signal
from services.growth import audit_persist
from services.growth.audit_persist import audit_and_persist
from services.growth.rules import GrowthThresholds

T0 = datetime(2026, 6, 21)
_FA_KEYS = ("revenue", "net_profit", "margin", "units_sold", "ad_spend", "drr",
            "active_seo_signals", "critical_seo_signals", "active_review_signals",
            "risk_review_signals", "stock_units", "days_to_oos", "category", "margin_band")
FULL_TH = GrowthThresholds(low_stock_units=5, min_revenue_for_growth_signal=1000.0,
                           min_net_profit_for_growth_signal=100.0)
ALL_TYPES = ("profitable_ad_candidate", "seo_leverage_candidate", "review_leverage_candidate",
             "stock_expansion_candidate", "margin_expansion_candidate")


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _snap(*, mp="wildberries", revenue=10000.0, net_profit=2000.0, margin=20.0, units_sold=40,
          ad_spend=0.0, drr=0.0, active_seo_signals=2, critical_seo_signals=1,
          active_review_signals=1, risk_review_signals=1, stock_units=3,
          margin_band="high", avail=None):
    fa = {k: True for k in _FA_KEYS}
    if avail:
        fa.update(avail)
    return GrowthSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=T0, source="internal",
        revenue=revenue, net_profit=net_profit, margin=margin, units_sold=units_sold,
        ad_spend=ad_spend, drr=drr, active_seo_signals=active_seo_signals,
        critical_seo_signals=critical_seo_signals, active_review_signals=active_review_signals,
        risk_review_signals=risk_review_signals, stock_units=stock_units, days_to_oos=None,
        category="Кухня", margin_band=margin_band, field_availability=fa)


# ── 1. audit + 5 ledger rows ─────────────────────────────────────────────────

def test_persist_audit_and_ledger():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(), thresholds=FULL_TH)
        await db.commit()
        audit = (await db.execute(select(GrowthAudit).where(GrowthAudit.id == res.audit_id))).scalar_one()
        assert audit.status == "completed" and audit.source == "internal" and audit.snapshot_hash
        ledger = (await db.execute(select(GrowthRuleEvaluation).where(
            GrowthRuleEvaluation.audit_id == res.audit_id))).scalars().all()
        assert len(ledger) == 5 and res.rule_evaluation_count == 5
    _run(go())


# ── 2. triggered creates growth_problem ──────────────────────────────────────

def test_triggered_creates_problem():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(), thresholds=FULL_TH)
        await db.commit()
        problems = {p.problem_type for p in (await db.execute(select(GrowthProblem).where(
            GrowthProblem.audit_id == res.audit_id))).scalars().all()}
        assert problems == set(ALL_TYPES) and res.total_problems == 5
    _run(go())


# ── 3. not_triggered → ledger only, no problem ───────────────────────────────

def test_not_triggered_ledger_only():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(ad_spend=500.0),
                                      thresholds=FULL_TH)  # profitable_ad not_triggered
        await db.commit()
        ledger = {r.problem_type: r.result for r in (await db.execute(select(GrowthRuleEvaluation).where(
            GrowthRuleEvaluation.audit_id == res.audit_id))).scalars().all()}
        problems = {p.problem_type for p in (await db.execute(select(GrowthProblem).where(
            GrowthProblem.audit_id == res.audit_id))).scalars().all()}
        assert ledger["profitable_ad_candidate"] == "not_triggered"
        assert "profitable_ad_candidate" not in problems
        assert len(ledger) == 5
    _run(go())


# ── 4. not_evaluated → ledger only (with reason), no problem ─────────────────

def test_not_evaluated_ledger_only():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        th = GrowthThresholds(min_revenue_for_growth_signal=1000.0,
                              min_net_profit_for_growth_signal=100.0)  # no low_stock_units
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(), thresholds=th)
        await db.commit()
        row = (await db.execute(select(GrowthRuleEvaluation).where(
            GrowthRuleEvaluation.audit_id == res.audit_id,
            GrowthRuleEvaluation.problem_type == "stock_expansion_candidate"))).scalar_one()
        assert row.result == "not_evaluated" and "low_stock_units" in row.reason
        problems = {p.problem_type for p in (await db.execute(select(GrowthProblem).where(
            GrowthProblem.audit_id == res.audit_id))).scalars().all()}
        assert "stock_expansion_candidate" not in problems
        assert res.total_not_evaluated >= 1
    _run(go())


# ── 5. triggered creates growth_signal with 5 doctrine parts ─────────────────

def test_triggered_creates_signal_doctrine():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(), thresholds=FULL_TH)
        await db.commit()
        sigs = (await db.execute(select(GrowthSignal).where(
            GrowthSignal.audit_id == res.audit_id))).scalars().all()
        assert len(sigs) == 5
        s = next(x for x in sigs if x.problem_type == "profitable_ad_candidate")
        assert s.what and s.why and s.meaning and s.what_to_do and s.expected_effect
        assert s.status == "active" and s.evidence_hash
        assert s.category == "advertising" and s.effect_type == "revenue_gain"
        assert s.insight_key == "growth_profitable_ad_candidate:wildberries:SKU1"
    _run(go())


# ── 6. builder deterministic ─────────────────────────────────────────────────

def test_builder_deterministic():
    ev = Eval("margin_expansion_candidate", "pricing", "medium", "margin_gain", "finance",
              RuleResult.TRIGGERED, evidence={"margin_band": "high"})
    a = build_signal(ev, marketplace="ozon", sku="SKU1")
    b = build_signal(ev, marketplace="ozon", sku="SKU1")
    assert a == b
    assert a.insight_key == "growth_margin_expansion_candidate:ozon:SKU1"


# ── 7. insight_key stable / shaped ───────────────────────────────────────────

def test_insight_key_stable():
    ev = Eval("seo_leverage_candidate", "seo", "medium", "traffic_gain", "signals",
              RuleResult.TRIGGERED, evidence={})
    d = build_signal(ev, marketplace="yandex", sku="S")
    assert d.insight_key == "growth_seo_leverage_candidate:yandex:S"
    assert build_signal(ev, marketplace=None, sku=None).insight_key == \
        "growth_seo_leverage_candidate:unknown:unknown"


# ── 8. recommended_action_key correct for all 5 ──────────────────────────────

def test_recommended_action_keys():
    expected = {
        "profitable_ad_candidate": "start_advertising",
        "seo_leverage_candidate": "improve_listing",
        "review_leverage_candidate": "handle_reviews",
        "stock_expansion_candidate": "replenish_stock",
        "margin_expansion_candidate": "review_price_upside",
    }
    for pt, key in expected.items():
        ev = Eval(pt, "x", "high", "revenue_gain", "finance", RuleResult.TRIGGERED, evidence={})
        assert build_signal(ev, marketplace="wb", sku="S").recommended_action_key == key


# ── 9. no fake impact language ───────────────────────────────────────────────

def test_no_fake_impact():
    for pt in ALL_TYPES:
        ev = Eval(pt, "x", "high", "revenue_gain", "finance", RuleResult.TRIGGERED, evidence={})
        d = build_signal(ev, marketplace="wb", sku="S")
        blob = (d.expected_effect + " " + d.what + " " + d.meaning + " " + d.why).lower()
        for bad in ("гарант", "прогноз", "точную выручк", "точную прибыл", "обеща"):
            assert bad not in blob, f"{bad} in {pt}"


# ── 10. no growth score on persisted entities ────────────────────────────────

def test_no_growth_score():
    def cols(model):
        return {c.name for c in sa_inspect(model).columns}
    for model in (GrowthAudit, GrowthProblem, GrowthRuleEvaluation, GrowthSignal):
        c = cols(model)
        for bad in ("score", "growth_score", "internal_health_index"):
            assert bad not in c, f"{model.__tablename__}.{bad}"


# ── 11. no forecast / competitor / AI fields in signal evidence ──────────────

def test_no_forecast_competitor_ai_fields():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(), thresholds=FULL_TH)
        await db.commit()
        probs = (await db.execute(select(GrowthProblem).where(
            GrowthProblem.audit_id == res.audit_id))).scalars().all()
        for p in probs:
            ev = json.loads(p.evidence)
            for k in ev:
                for bad in ("forecast", "expected", "predict", "competitor", "market", "trend", "ai_"):
                    assert bad not in k.lower(), f"{bad} in {p.problem_type}"
        # no AI / external-client import tokens in the growth service source
        # (doctrine docstrings legitimately say "no forecast / no competitors",
        # so we scan for real code tokens, not those policy words)
        core_dir = Path(inspect.getfile(audit_persist)).parent
        for path in core_dir.rglob("*.py"):
            src = path.read_text(encoding="utf-8").lower()
            for bad in ("openai", "anthropic", "import requests", "import httpx", "gpt-"):
                assert bad not in src, f"{bad} in {path.name}"
    _run(go())


# ── 12. marketplace agnostic ─────────────────────────────────────────────────

def test_marketplace_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            res = await audit_and_persist(db, user_id=uid, snapshot=_snap(mp=mp),
                                          thresholds=FULL_TH, now=T0)
            await db.commit()
            sig = (await db.execute(select(GrowthSignal).where(
                GrowthSignal.audit_id == res.audit_id))).scalars().first()
            assert sig.insight_key.startswith("growth_") and f":{mp}:" in sig.insight_key
    _run(go())
