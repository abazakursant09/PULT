"""
Advertising A5 — persistence + Signal Builder tests.

persist creates audit + full 6-rule ledger; triggered → advertising_problem +
5-part advertising_signal; not_triggered/not_evaluated → ledger only; builder
deterministic; insight_key stable; recommended_action_key matches catalog; no
score; no campaign/CTR/CPC leak; agnostic; core has no MP client imports.
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
from models.advertising_audit import AdvertisingAudit
from models.advertising_problem import AdvertisingProblem
from models.advertising_rule_evaluation import AdvertisingRuleEvaluation
from models.advertising_signal import AdvertisingSignal

from services.advertising.snapshot import AdvertisingSnapshot, AdvertisingThresholds
from services.advertising.evaluation import RuleEvaluation, RuleResult
from services.advertising.signal_builder import build_signal
from services.advertising import audit_persist
from services.advertising.audit_persist import audit_and_persist

T = AdvertisingThresholds(max_drr=20.0, min_revenue_for_signal=1000.0,
                          min_ad_spend_for_signal=100.0, low_margin_threshold=10.0,
                          low_stock_units=5, oos_risk_days=7.0)
_FIELDS = ("revenue", "net_profit", "ad_spend", "units_sold", "margin", "drr",
           "orders", "stock_units", "days_to_oos", "active_seo_problems",
           "critical_seo_problems", "category", "price_band", "margin_band", "thresholds")


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


def _snap(*, mp="wildberries", net_profit=2000.0, margin=20.0, stock_units=50,
          days_to_oos=30.0, active_seo=0, availability=None):
    return AdvertisingSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=datetime(2026, 6, 21),
        source="finance", revenue=10000.0, net_profit=net_profit, ad_spend=1000.0,
        orders=None, units_sold=20, margin=margin, drr=10.0,
        stock_units=stock_units, days_to_oos=days_to_oos,
        active_seo_problems=active_seo, critical_seo_problems=0,
        category=None, price_band=None, margin_band=None, thresholds=T,
        field_availability=availability if availability is not None else {k: True for k in _FIELDS})


# ── 1. persist → audit + 6 ledger rows ───────────────────────────────────────

def test_persist_audit_and_full_ledger():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(net_profit=-500.0, stock_units=1))
        await db.commit()
        audit = (await db.execute(select(AdvertisingAudit).where(AdvertisingAudit.id == res.audit_id))).scalar_one()
        assert audit.status == "completed" and audit.source == "finance"
        assert audit.rule_catalog_version == "1" and audit.snapshot_hash
        ledger = (await db.execute(select(AdvertisingRuleEvaluation).where(
            AdvertisingRuleEvaluation.audit_id == res.audit_id))).scalars().all()
        assert len(ledger) == 6 and res.rule_evaluation_count == 6
    _run(go())


# ── 2/3/4. problem only for triggered; ledger for all (with reason/evidence) ─

def test_problem_only_for_triggered():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        # net<0 → ad_destroying_profit triggered; ops/seo unavailable → not_evaluated
        avail = {k: True for k in _FIELDS}
        avail.update({"stock_units": False, "days_to_oos": False, "active_seo_problems": False})
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(net_profit=-500.0, availability=avail))
        await db.commit()
        ledger = {r.problem_type: r for r in (await db.execute(select(AdvertisingRuleEvaluation).where(
            AdvertisingRuleEvaluation.audit_id == res.audit_id))).scalars().all()}
        problems = {p.problem_type for p in (await db.execute(select(AdvertisingProblem).where(
            AdvertisingProblem.audit_id == res.audit_id))).scalars().all()}
        assert "ad_destroying_profit" in problems
        assert ledger["ad_destroying_profit"].result == "triggered" and ledger["ad_destroying_profit"].evidence
        assert ledger["ad_on_low_stock"].result == "not_evaluated"
        assert "missing_fields" in ledger["ad_on_low_stock"].reason
        assert "ad_on_low_stock" not in problems
        assert len(problems) < 6
    _run(go())


# ── 5. signal with 5 doctrine parts + status active ──────────────────────────

def test_signal_five_doctrine_parts():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(net_profit=-500.0))
        await db.commit()
        sigs = (await db.execute(select(AdvertisingSignal).where(
            AdvertisingSignal.audit_id == res.audit_id))).scalars().all()
        assert len(sigs) == res.total_problems and sigs
        s = next(x for x in sigs if x.problem_type == "ad_destroying_profit")
        assert s.what and s.why and s.meaning and s.what_to_do and s.expected_effect
        assert s.status == "active" and s.evidence_hash
        assert s.signal_key == "adv_ad_destroying_profit"
    _run(go())


# ── 6. builder deterministic ─────────────────────────────────────────────────

def test_builder_deterministic():
    ev = RuleEvaluation("ad_destroying_profit", "Profitability", "critical", "margin_loss",
                        "finance", RuleResult.TRIGGERED, evidence={"drr": 40.0, "net_profit": -500.0})
    a = build_signal(ev, marketplace="ozon", sku="SKU1")
    b = build_signal(ev, marketplace="ozon", sku="SKU1")
    assert a == b
    assert "40.0" in a.what and "-500.0" in a.what


# ── 7. insight_key stable ────────────────────────────────────────────────────

def test_insight_key_stable():
    ev = RuleEvaluation("ad_destroying_profit", "Profitability", "critical", "margin_loss",
                        "finance", RuleResult.TRIGGERED, evidence={})
    d = build_signal(ev, marketplace="wildberries", sku="SKU123")
    assert d.insight_key == "adv_ad_destroying_profit:wildberries:SKU123"


# ── 8. recommended_action_key matches catalog (dedup-aware) ──────────────────

def test_recommended_action_catalog():
    expected = {
        "ad_destroying_profit": "stop_auto_promotion",
        "ad_spend_without_sales": "stop_ad_on_product",
        "ad_on_unprofitable_product": "stop_ad_on_product",
        "ad_on_low_stock": "pause_campaign",
        "ad_on_bad_listing": "improve_listing",
        "ad_on_oos_risk": "pause_campaign",
    }
    for pt, action in expected.items():
        ev = RuleEvaluation(pt, "c", "high", "margin_loss", "finance", RuleResult.TRIGGERED, evidence={})
        assert build_signal(ev, marketplace="wb", sku="S").recommended_action_key == action


# ── 9/10. no score / no campaign-CTR-CPC leak ────────────────────────────────

def test_no_score_no_cabinet_leak():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        res = await audit_and_persist(db, user_id=uid, snapshot=_snap(net_profit=-500.0))
        await db.commit()
        audit = (await db.execute(select(AdvertisingAudit).where(AdvertisingAudit.id == res.audit_id))).scalar_one()
        from sqlalchemy import inspect as sa_inspect
        cols = {c.name for c in sa_inspect(AdvertisingAudit).columns}
        assert "internal_health_index" not in cols and "score" not in cols
        sig = (await db.execute(select(AdvertisingSignal).where(
            AdvertisingSignal.audit_id == res.audit_id))).scalars().first()
        blob = " ".join(str(x) for x in (sig.what, sig.why, sig.meaning, sig.alternative_action_keys)).lower()
        for bad in ("ctr", "cpc", "clicks", "impressions", "bid", "keyword"):
            assert bad not in blob
    _run(go())


# ── 11. marketplace agnostic ─────────────────────────────────────────────────

def test_agnostic():
    async def go():
        for mp in ("wildberries", "ozon", "yandex"):
            db = await _engine(); uid = str(uuid.uuid4())
            res = await audit_and_persist(db, user_id=uid, snapshot=_snap(mp=mp, net_profit=-1.0),
                                          now=datetime(2026, 6, 21))
            await db.commit()
            sig = (await db.execute(select(AdvertisingSignal).where(
                AdvertisingSignal.audit_id == res.audit_id))).scalars().first()
            assert sig.insight_key.endswith(f":{mp}:SKU1")
    _run(go())


# ── 12. core imports no marketplace clients ──────────────────────────────────

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
