"""
Advertising A4 — deterministic Rule Engine tests.

All 6 rules can trigger / not_trigger; not_evaluated stays distinct (with reason);
evidence deterministic + snapshot-only (no CTR/CPC/campaign fields); registry
order stable; determinism; marketplace-agnostic; core imports no MP clients.
"""
import ast
import inspect
from datetime import datetime
from pathlib import Path

from services.advertising.snapshot import AdvertisingSnapshot, AdvertisingThresholds
from services.advertising.evaluation import RuleResult
from services.advertising.engine import evaluate_snapshot
from services.advertising import rules as rules_mod
from services.advertising.rules import RULE_REGISTRY

T = AdvertisingThresholds(max_drr=20.0, min_revenue_for_signal=1000.0,
                          min_ad_spend_for_signal=100.0, low_margin_threshold=10.0,
                          low_stock_units=5, oos_risk_days=7.0)

_FIELDS = ("revenue", "net_profit", "ad_spend", "units_sold", "margin", "drr",
           "orders", "stock_units", "days_to_oos", "active_seo_problems",
           "critical_seo_problems", "category", "price_band", "margin_band", "thresholds")


def _snap(*, mp="wildberries", revenue=10000.0, net_profit=2000.0, ad_spend=1000.0,
          units_sold=20, margin=20.0, drr=10.0, stock_units=50, days_to_oos=30.0,
          active_seo=0, critical_seo=0, thresholds=T, availability=None):
    return AdvertisingSnapshot(
        listing_id="L1", marketplace=mp, sku="SKU1", captured_at=datetime(2026, 6, 21),
        source="finance", revenue=revenue, net_profit=net_profit, ad_spend=ad_spend,
        orders=None, units_sold=units_sold, margin=margin, drr=drr,
        stock_units=stock_units, days_to_oos=days_to_oos,
        active_seo_problems=active_seo, critical_seo_problems=critical_seo,
        category=None, price_band=None, margin_band=None, thresholds=thresholds,
        field_availability=availability if availability is not None else {k: True for k in _FIELDS},
    )


def _by(results):
    return {r.problem_type: r for r in results}


# ── healthy snapshot: nothing triggers ───────────────────────────────────────

def test_healthy_triggers_nothing():
    res = _by(evaluate_snapshot(_snap()))
    assert all(r.result == RuleResult.NOT_TRIGGERED for r in res.values()), \
        {k: v.result for k, v in res.items() if v.result != RuleResult.NOT_TRIGGERED}


# ── 1. all 6 can TRIGGER ─────────────────────────────────────────────────────

def test_all_rules_can_trigger():
    cases = {
        "ad_destroying_profit": _snap(net_profit=-500.0),                 # ad>0 & net<0
        "ad_spend_without_sales": _snap(ad_spend=300.0, revenue=0.0, units_sold=0, drr=None, margin=None),
        "ad_on_unprofitable_product": _snap(margin=3.0),                  # margin < 10
        "ad_on_low_stock": _snap(stock_units=2),                         # <= 5
        "ad_on_bad_listing": _snap(active_seo=2, critical_seo=1),
        "ad_on_oos_risk": _snap(days_to_oos=3.0),                        # <= 7
    }
    for pt, snap in cases.items():
        r = _by(evaluate_snapshot(snap))[pt]
        assert r.result == RuleResult.TRIGGERED, f"{pt}: {r.result}/{r.reason}"
        assert r.evidence and "ad_spend" in r.evidence
    assert set(cases) == {r.problem_type for r in RULE_REGISTRY}   # all 6 covered


# ── 2. all 6 can NOT_TRIGGER (healthy) ───────────────────────────────────────

def test_all_rules_can_not_trigger():
    res = _by(evaluate_snapshot(_snap()))
    for rule in RULE_REGISTRY:
        assert res[rule.problem_type].result == RuleResult.NOT_TRIGGERED


# ── 3. not_evaluated distinct, with reason ───────────────────────────────────

def test_not_evaluated_distinct():
    # no thresholds → threshold rules not_evaluated; missing ops/seo fields likewise
    avail = {k: True for k in _FIELDS}
    avail.update({"thresholds": False, "stock_units": False, "days_to_oos": False,
                  "active_seo_problems": False})
    res = _by(evaluate_snapshot(_snap(thresholds=None, availability=avail)))
    for pt in ("ad_destroying_profit", "ad_spend_without_sales", "ad_on_unprofitable_product",
               "ad_on_low_stock", "ad_on_oos_risk", "ad_on_bad_listing"):
        assert res[pt].result == RuleResult.NOT_EVALUATED
        assert res[pt].reason and "missing_fields" in res[pt].reason
        assert res[pt].evidence is None
    # finance-only snapshot: money rules evaluate, ops/seo rules not_evaluated
    fin = {k: True for k in _FIELDS}
    fin.update({"stock_units": False, "days_to_oos": False, "active_seo_problems": False})
    r2 = _by(evaluate_snapshot(_snap(net_profit=-1.0, availability=fin)))
    assert r2["ad_destroying_profit"].result == RuleResult.TRIGGERED
    assert r2["ad_on_low_stock"].result == RuleResult.NOT_EVALUATED
    assert r2["ad_on_bad_listing"].result == RuleResult.NOT_EVALUATED


# ── 4. evidence deterministic + no CTR/CPC/campaign fields ───────────────────

_ALLOWED_EVIDENCE = {
    "ad_spend", "revenue", "net_profit", "drr", "margin", "units_sold", "stock_units",
    "days_to_oos", "active_seo_problems", "critical_seo_problems", "thresholds_used",
}


def test_evidence_whitelisted_no_cabinet_fields():
    for snap in (_snap(net_profit=-1.0), _snap(margin=1.0), _snap(stock_units=1)):
        for r in evaluate_snapshot(snap):
            if r.evidence:
                assert set(r.evidence) <= _ALLOWED_EVIDENCE, set(r.evidence) - _ALLOWED_EVIDENCE
                for bad in ("ctr", "cpc", "clicks", "impressions", "campaign", "bid", "keyword"):
                    assert bad not in r.evidence


# ── 5. registry order stable ─────────────────────────────────────────────────

def test_registry_order_stable():
    order = [r.problem_type for r in RULE_REGISTRY]
    assert order == [r.problem_type for r in evaluate_snapshot(_snap())]
    assert len(order) == 6 and len(set(order)) == 6


# ── 6. determinism ───────────────────────────────────────────────────────────

def test_deterministic():
    snap = _snap(net_profit=-300.0, stock_units=1)
    assert evaluate_snapshot(snap) == evaluate_snapshot(snap)


# ── 7. marketplace agnostic ──────────────────────────────────────────────────

def test_marketplace_agnostic():
    out = {}
    for mp in ("wildberries", "ozon", "yandex"):
        out[mp] = {p: r.result for p, r in _by(evaluate_snapshot(_snap(mp=mp, net_profit=-1.0))).items()}
    assert out["wildberries"] == out["ozon"] == out["yandex"]


# ── 8. core imports no marketplace clients ───────────────────────────────────

def test_core_no_marketplace_client_imports():
    core_dir = Path(inspect.getfile(rules_mod)).parent
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


# ── 9. no CTR/CPC/campaign fields in the contract ────────────────────────────

def test_snapshot_has_no_cabinet_fields():
    from services.advertising import snapshot as snap_mod
    src = inspect.getsource(snap_mod).lower()
    for bad in ("ctr", "cpc", "clicks", "impressions", "campaign", "bid", "keyword"):
        assert bad not in src
