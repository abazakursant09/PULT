"""
Growth A4 — Rule Engine tests.

5 deterministic rules over GrowthSnapshot + external GrowthThresholds. Three
outcomes never collapsed; thresholds have NO defaults (missing → not_evaluated);
evidence is snapshot-derived only (no forecast / competitor / market / AI).
"""
import ast
import inspect
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from services.growth import rules as rules_mod
from services.growth.snapshot import GrowthSnapshot
from services.growth.evaluation import RuleResult
from services.growth.engine import evaluate_snapshot
from services.growth.rules import GrowthThresholds, RULE_REGISTRY

T0 = datetime(2026, 6, 21)

_FA_KEYS = ("revenue", "net_profit", "margin", "units_sold", "ad_spend", "drr",
            "active_seo_signals", "critical_seo_signals", "active_review_signals",
            "risk_review_signals", "stock_units", "days_to_oos", "category", "margin_band")

FULL_TH = GrowthThresholds(low_stock_units=5, min_revenue_for_growth_signal=1000.0,
                           min_net_profit_for_growth_signal=100.0)

ALL_PROBLEM_TYPES = ("profitable_ad_candidate", "seo_leverage_candidate",
                     "review_leverage_candidate", "stock_expansion_candidate",
                     "margin_expansion_candidate")


def _snap(*, mp="wildberries", revenue=10000.0, net_profit=2000.0, margin=20.0,
          units_sold=40, ad_spend=0.0, drr=0.0, active_seo_signals=2, critical_seo_signals=1,
          active_review_signals=1, risk_review_signals=1, stock_units=3,
          category="Кухня", margin_band="high", avail=None):
    fa = {k: True for k in _FA_KEYS}
    if avail:
        fa.update(avail)
    return GrowthSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=T0, source="internal",
        revenue=revenue, net_profit=net_profit, margin=margin, units_sold=units_sold,
        ad_spend=ad_spend, drr=drr,
        active_seo_signals=active_seo_signals, critical_seo_signals=critical_seo_signals,
        active_review_signals=active_review_signals, risk_review_signals=risk_review_signals,
        stock_units=stock_units, days_to_oos=None,
        category=category, margin_band=margin_band, field_availability=fa)


def _by_type(snap, th=FULL_TH):
    return {e.problem_type: e for e in evaluate_snapshot(snap, th)}


# ── 1. all 5 rules can be triggered ──────────────────────────────────────────

def test_all_rules_can_trigger():
    ev = _by_type(_snap())   # base snapshot satisfies all 5
    for pt in ALL_PROBLEM_TYPES:
        assert ev[pt].result == RuleResult.TRIGGERED, pt
        assert ev[pt].evidence and "thresholds_used" in ev[pt].evidence


# ── 2. all 5 rules can be not_triggered ──────────────────────────────────────

def test_all_rules_can_not_trigger():
    cases = {
        "profitable_ad_candidate": _snap(ad_spend=500.0),          # already advertised
        "seo_leverage_candidate": _snap(active_seo_signals=0),     # no seo headroom
        "review_leverage_candidate": _snap(active_review_signals=0, risk_review_signals=0),
        "stock_expansion_candidate": _snap(stock_units=100),       # plenty of stock
        "margin_expansion_candidate": _snap(margin_band="medium"), # not high margin
    }
    for pt, snap in cases.items():
        assert _by_type(snap)[pt].result == RuleResult.NOT_TRIGGERED, pt


# ── 3. not_evaluated never mixed with not_triggered ──────────────────────────

def test_not_evaluated_distinct_from_not_triggered():
    snap = _snap(avail={"ad_spend": False})    # profitable_ad loses a required field
    e = _by_type(snap)["profitable_ad_candidate"]
    assert e.result == RuleResult.NOT_EVALUATED
    assert e.reason and "missing_fields" in e.reason and "ad_spend" in e.reason
    assert e.evidence is None
    # a different rule whose fields are present still evaluates normally
    assert _by_type(snap)["seo_leverage_candidate"].result == RuleResult.TRIGGERED


# ── 4. no thresholds → threshold rules not_evaluated ─────────────────────────

def test_missing_thresholds_not_evaluated():
    ev = _by_type(_snap(), GrowthThresholds())   # all thresholds None
    for pt in ALL_PROBLEM_TYPES:
        assert ev[pt].result == RuleResult.NOT_EVALUATED, pt
        assert "missing_threshold" in (ev[pt].reason or "")
    # partial: only stock threshold missing → only stock rule blocked on it
    th = GrowthThresholds(min_revenue_for_growth_signal=1000.0,
                          min_net_profit_for_growth_signal=100.0)
    ev2 = _by_type(_snap(), th)
    assert ev2["stock_expansion_candidate"].result == RuleResult.NOT_EVALUATED
    assert "low_stock_units" in ev2["stock_expansion_candidate"].reason
    assert ev2["seo_leverage_candidate"].result == RuleResult.TRIGGERED


# ── 5. evidence deterministic ────────────────────────────────────────────────

def test_evidence_deterministic():
    a = _by_type(_snap())["margin_expansion_candidate"].evidence
    b = _by_type(_snap())["margin_expansion_candidate"].evidence
    assert a == b
    assert a["margin_band"] == "high" and a["revenue"] == 10000.0
    assert a["thresholds_used"]["min_revenue_for_growth_signal"] == 1000.0


# ── 6. registry stable ───────────────────────────────────────────────────────

def test_registry_stable():
    assert tuple(r.problem_type for r in RULE_REGISTRY) == ALL_PROBLEM_TYPES
    cats = {r.category for r in RULE_REGISTRY}
    assert cats == {"advertising", "seo", "reputation", "inventory", "pricing"}


# ── 7. same snapshot + thresholds → same result ──────────────────────────────

def test_deterministic_engine():
    snap = _snap()
    assert evaluate_snapshot(snap, FULL_TH) == evaluate_snapshot(snap, FULL_TH)


# ── 8. marketplace agnostic ──────────────────────────────────────────────────

def test_marketplace_agnostic():
    base = {pt: e.result for pt, e in _by_type(_snap(mp="wildberries")).items()}
    for mp in ("ozon", "yandex"):
        got = {pt: e.result for pt, e in _by_type(_snap(mp=mp)).items()}
        assert got == base


# ── 9. no external API imports ───────────────────────────────────────────────

def test_no_external_api_imports():
    core_dir = Path(inspect.getfile(rules_mod)).parent
    forbidden = ("wb_client", "ozon_client", "yandex_client", "requests", "httpx",
                 "aiohttp", "credential_vault", "openai", "anthropic")
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


# ── 10. no forecast / competitor / market / AI fields in evidence ────────────

def test_evidence_has_no_forecast_or_competitor_fields():
    allowed = set(rules_mod._base_evidence(_snap()).keys()) | {"thresholds_used"}
    forbidden_substr = ("forecast", "expected", "predict", "competitor", "market", "trend", "ai_")
    for e in evaluate_snapshot(_snap(), FULL_TH):
        if e.result != RuleResult.TRIGGERED:
            continue
        keys = set(e.evidence.keys())
        assert keys <= allowed, keys - allowed
        for k in keys:
            for bad in forbidden_substr:
                assert bad not in k.lower(), k
