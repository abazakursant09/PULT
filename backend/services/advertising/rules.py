"""
Advertising Rule Catalog — engine implementation (A4).

6 deterministic rules over an AdvertisingSnapshot. Pure: no DB, no API, no signal
building, no marketplace-specific logic, no external data. Advertising is judged
ONLY through impact on profit / margin / stock / listing — never campaign/CTR/CPC.

Every rule:
  1. declares `required_fields` (field_availability keys it needs);
  2. is NOT_EVALUATED (with reason) when any required field/threshold is
     unavailable — absence of data is never treated as "ok";
  3. when evaluable, is TRIGGERED (snapshot-derived evidence) or NOT_TRIGGERED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Tuple, Union

from .snapshot import AdvertisingSnapshot
from .evaluation import RuleEvaluation, RuleResult

RULE_CATALOG_VERSION = "1"

_Pred = Tuple[str, Union[dict, str, None]]


@dataclass(frozen=True)
class Rule:
    problem_type: str
    category: str
    severity: str
    estimated_effect_type: str
    detectability: str
    required_fields: Tuple[str, ...]
    predicate: Callable[[AdvertisingSnapshot], _Pred]

    def evaluate(self, snap: AdvertisingSnapshot) -> RuleEvaluation:
        missing = [f for f in self.required_fields if not snap.field_availability.get(f)]
        if missing:
            return self._mk(RuleResult.NOT_EVALUATED, reason=f"missing_fields: {','.join(missing)}")
        kind, payload = self.predicate(snap)
        if kind == "triggered":
            return self._mk(RuleResult.TRIGGERED, evidence=dict(payload))   # type: ignore[arg-type]
        if kind == "not_evaluated":
            return self._mk(RuleResult.NOT_EVALUATED, reason=str(payload))
        return self._mk(RuleResult.NOT_TRIGGERED)

    def _mk(self, result, *, evidence=None, reason=None) -> RuleEvaluation:
        return RuleEvaluation(
            problem_type=self.problem_type, category=self.category, severity=self.severity,
            estimated_effect_type=self.estimated_effect_type, detectability=self.detectability,
            result=result, evidence=evidence, reason=reason,
        )


# ── predicates (snapshot-only; thresholds guaranteed present when required) ───

def _p_ad_destroying_profit(s: AdvertisingSnapshot) -> _Pred:
    t = s.thresholds
    if s.ad_spend and s.ad_spend > 0 and s.net_profit is not None and s.net_profit < 0:
        return "triggered", {"ad_spend": s.ad_spend, "net_profit": s.net_profit, "drr": s.drr,
                             "revenue": s.revenue, "thresholds_used": {"max_drr": t.max_drr}}
    if (s.drr is not None and s.drr > t.max_drr
            and s.revenue is not None and s.revenue >= t.min_revenue_for_signal):
        return "triggered", {"ad_spend": s.ad_spend, "net_profit": s.net_profit, "drr": s.drr,
                             "revenue": s.revenue,
                             "thresholds_used": {"max_drr": t.max_drr,
                                                 "min_revenue_for_signal": t.min_revenue_for_signal}}
    return "not_triggered", None


def _p_ad_spend_without_sales(s: AdvertisingSnapshot) -> _Pred:
    t = s.thresholds
    if (s.ad_spend is not None and s.ad_spend >= t.min_ad_spend_for_signal
            and ((s.revenue or 0) <= 0 or (s.units_sold or 0) <= 0)):
        return "triggered", {"ad_spend": s.ad_spend, "revenue": s.revenue, "units_sold": s.units_sold,
                             "thresholds_used": {"min_ad_spend_for_signal": t.min_ad_spend_for_signal}}
    return "not_triggered", None


def _p_ad_on_unprofitable_product(s: AdvertisingSnapshot) -> _Pred:
    t = s.thresholds
    if s.ad_spend and s.ad_spend > 0 and s.margin is not None and s.margin < t.low_margin_threshold:
        return "triggered", {"ad_spend": s.ad_spend, "margin": s.margin, "net_profit": s.net_profit,
                             "thresholds_used": {"low_margin_threshold": t.low_margin_threshold}}
    return "not_triggered", None


def _p_ad_on_low_stock(s: AdvertisingSnapshot) -> _Pred:
    t = s.thresholds
    if s.ad_spend and s.ad_spend > 0 and s.stock_units is not None and s.stock_units <= t.low_stock_units:
        return "triggered", {"ad_spend": s.ad_spend, "stock_units": s.stock_units,
                             "thresholds_used": {"low_stock_units": t.low_stock_units}}
    return "not_triggered", None


def _p_ad_on_bad_listing(s: AdvertisingSnapshot) -> _Pred:
    if s.ad_spend and s.ad_spend > 0 and (s.active_seo_problems or 0) > 0:
        return "triggered", {"ad_spend": s.ad_spend, "active_seo_problems": s.active_seo_problems,
                             "critical_seo_problems": s.critical_seo_problems}
    return "not_triggered", None


def _p_ad_on_oos_risk(s: AdvertisingSnapshot) -> _Pred:
    t = s.thresholds
    if s.ad_spend and s.ad_spend > 0 and s.days_to_oos is not None and s.days_to_oos <= t.oos_risk_days:
        return "triggered", {"ad_spend": s.ad_spend, "days_to_oos": s.days_to_oos,
                             "thresholds_used": {"oos_risk_days": t.oos_risk_days}}
    return "not_triggered", None


# ── closed, stable-ordered registry ──────────────────────────────────────────

RULE_REGISTRY: Tuple[Rule, ...] = (
    Rule("ad_destroying_profit", "Profitability", "critical", "margin_loss",
         "finance", ("ad_spend", "net_profit", "drr", "revenue", "thresholds"),
         _p_ad_destroying_profit),
    Rule("ad_spend_without_sales", "Efficiency", "high", "wasted_spend",
         "finance", ("ad_spend", "revenue", "units_sold", "thresholds"),
         _p_ad_spend_without_sales),
    Rule("ad_on_unprofitable_product", "Profitability", "high", "margin_loss",
         "finance", ("ad_spend", "margin", "thresholds"), _p_ad_on_unprofitable_product),
    Rule("ad_on_low_stock", "Operations", "medium", "wasted_spend",
         "requires_operations", ("ad_spend", "stock_units", "thresholds"), _p_ad_on_low_stock),
    Rule("ad_on_bad_listing", "Listing", "medium", "conversion_loss",
         "requires_seo", ("ad_spend", "active_seo_problems"), _p_ad_on_bad_listing),
    Rule("ad_on_oos_risk", "Operations", "medium", "wasted_spend",
         "requires_operations", ("ad_spend", "days_to_oos", "thresholds"), _p_ad_on_oos_risk),
)
