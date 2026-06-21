"""
Growth Rule Catalog — engine implementation (A4).

5 deterministic rules over a GrowthSnapshot + external GrowthThresholds. Pure: no
DB, no API, no persist, no signal building, no AI, no forecast, no competitors, no
external API, no growth score, no fabricated money estimates. Growth Engine finds
unrealised UPSIDE in data PULT already stores.

Every rule:
  1. declares `required_fields` (field_availability keys) and `required_thresholds`
     (GrowthThresholds attrs it needs);
  2. is NOT_EVALUATED (with reason) when any required field is unavailable OR any
     required threshold is None — absence is never treated as "no opportunity",
     and thresholds have NO defaults (must come from outside);
  3. when evaluable, is TRIGGERED (snapshot-derived evidence + thresholds_used) or
     NOT_TRIGGERED.

Evidence holds ONLY deterministic snapshot facts + the thresholds applied — never
a forecast, expected revenue, market size, or competitor data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Union

from .snapshot import GrowthSnapshot
from .evaluation import GrowthRuleEvaluation, RuleResult

RULE_CATALOG_VERSION = "1"

_Pred = Tuple[str, Union[dict, str, None]]


@dataclass(frozen=True)
class GrowthThresholds:
    """External growth thresholds. NO defaults — a missing (None) threshold makes
    any rule that needs it NOT_EVALUATED."""
    low_stock_units: Optional[int] = None
    min_revenue_for_growth_signal: Optional[float] = None
    min_net_profit_for_growth_signal: Optional[float] = None


@dataclass(frozen=True)
class Rule:
    problem_type: str
    category: str
    severity: str
    estimated_effect_type: str
    detectability: str
    required_fields: Tuple[str, ...]
    required_thresholds: Tuple[str, ...]
    predicate: Callable[[GrowthSnapshot, GrowthThresholds], _Pred]

    def evaluate(self, snap: GrowthSnapshot, th: GrowthThresholds) -> GrowthRuleEvaluation:
        missing = [f for f in self.required_fields if not snap.field_availability.get(f)]
        if missing:
            return self._mk(RuleResult.NOT_EVALUATED, reason=f"missing_fields: {','.join(missing)}")
        missing_th = [t for t in self.required_thresholds if getattr(th, t, None) is None]
        if missing_th:
            return self._mk(RuleResult.NOT_EVALUATED, reason=f"missing_threshold: {','.join(missing_th)}")
        kind, payload = self.predicate(snap, th)
        if kind == "triggered":
            return self._mk(RuleResult.TRIGGERED, evidence=dict(payload))   # type: ignore[arg-type]
        if kind == "not_evaluated":
            return self._mk(RuleResult.NOT_EVALUATED, reason=str(payload))
        return self._mk(RuleResult.NOT_TRIGGERED)

    def _mk(self, result, *, evidence=None, reason=None) -> GrowthRuleEvaluation:
        return GrowthRuleEvaluation(
            problem_type=self.problem_type, category=self.category, severity=self.severity,
            estimated_effect_type=self.estimated_effect_type, detectability=self.detectability,
            result=result, evidence=evidence, reason=reason,
        )


def _base_evidence(s: GrowthSnapshot) -> dict:
    """Deterministic snapshot facts only — no forecast / expected / competitor data."""
    return {
        "revenue": s.revenue, "net_profit": s.net_profit, "margin": s.margin,
        "margin_band": s.margin_band, "ad_spend": s.ad_spend, "drr": s.drr,
        "units_sold": s.units_sold,
        "active_seo_signals": s.active_seo_signals, "critical_seo_signals": s.critical_seo_signals,
        "active_review_signals": s.active_review_signals, "risk_review_signals": s.risk_review_signals,
        "stock_units": s.stock_units,
    }


def _ev(s: GrowthSnapshot, thresholds_used: dict) -> dict:
    e = _base_evidence(s)
    e["thresholds_used"] = thresholds_used
    return e


# ── predicates ───────────────────────────────────────────────────────────────

def _p_profitable_ad_candidate(s: GrowthSnapshot, th: GrowthThresholds) -> _Pred:
    # profitable but NOT advertised → start_advertising
    used = {"min_net_profit_for_growth_signal": th.min_net_profit_for_growth_signal}
    if ((s.net_profit or 0) > 0
            and (s.net_profit or 0) >= th.min_net_profit_for_growth_signal
            and s.margin_band in ("high", "medium")
            and (s.ad_spend is None or s.ad_spend == 0)):
        return "triggered", _ev(s, used)
    return "not_triggered", None


def _p_seo_leverage_candidate(s: GrowthSnapshot, th: GrowthThresholds) -> _Pred:
    # already selling but SEO limits growth → improve_listing
    used = {"min_revenue_for_growth_signal": th.min_revenue_for_growth_signal}
    if ((s.revenue or 0) > 0
            and (s.revenue or 0) >= th.min_revenue_for_growth_signal
            and (s.active_seo_signals or 0) > 0):
        return "triggered", _ev(s, used)
    return "not_triggered", None


def _p_review_leverage_candidate(s: GrowthSnapshot, th: GrowthThresholds) -> _Pred:
    # selling but reputation may limit growth → handle_reviews
    used = {"min_revenue_for_growth_signal": th.min_revenue_for_growth_signal}
    if ((s.revenue or 0) > 0
            and (s.revenue or 0) >= th.min_revenue_for_growth_signal
            and ((s.risk_review_signals or 0) > 0 or (s.active_review_signals or 0) > 0)):
        return "triggered", _ev(s, used)
    return "not_triggered", None


def _p_stock_expansion_candidate(s: GrowthSnapshot, th: GrowthThresholds) -> _Pred:
    # selling but running low on stock → replenish_stock
    used = {"min_revenue_for_growth_signal": th.min_revenue_for_growth_signal,
            "low_stock_units": th.low_stock_units}
    if ((s.revenue or 0) > 0
            and (s.revenue or 0) >= th.min_revenue_for_growth_signal
            and s.stock_units is not None
            and s.stock_units <= th.low_stock_units):
        return "triggered", _ev(s, used)
    return "not_triggered", None


def _p_margin_expansion_candidate(s: GrowthSnapshot, th: GrowthThresholds) -> _Pred:
    # high margin + revenue → room for price/margin upside → review_price_upside
    used = {"min_net_profit_for_growth_signal": th.min_net_profit_for_growth_signal,
            "min_revenue_for_growth_signal": th.min_revenue_for_growth_signal}
    if ((s.net_profit or 0) > 0
            and (s.net_profit or 0) >= th.min_net_profit_for_growth_signal
            and s.margin_band == "high"
            and (s.revenue or 0) > 0
            and (s.revenue or 0) >= th.min_revenue_for_growth_signal):
        return "triggered", _ev(s, used)
    return "not_triggered", None


# ── closed, stable-ordered registry ──────────────────────────────────────────

RULE_REGISTRY: Tuple[Rule, ...] = (
    Rule("profitable_ad_candidate", "advertising", "high", "revenue_gain", "finance",
         ("net_profit", "margin_band", "ad_spend"),
         ("min_net_profit_for_growth_signal",), _p_profitable_ad_candidate),
    Rule("seo_leverage_candidate", "seo", "medium", "traffic_gain", "signals",
         ("revenue", "active_seo_signals"),
         ("min_revenue_for_growth_signal",), _p_seo_leverage_candidate),
    Rule("review_leverage_candidate", "reputation", "medium", "reputation_upside", "signals",
         ("revenue", "active_review_signals", "risk_review_signals"),
         ("min_revenue_for_growth_signal",), _p_review_leverage_candidate),
    Rule("stock_expansion_candidate", "inventory", "high", "revenue_gain", "operations",
         ("revenue", "stock_units"),
         ("min_revenue_for_growth_signal", "low_stock_units"), _p_stock_expansion_candidate),
    Rule("margin_expansion_candidate", "pricing", "medium", "margin_gain", "finance",
         ("net_profit", "margin_band", "revenue"),
         ("min_net_profit_for_growth_signal", "min_revenue_for_growth_signal"),
         _p_margin_expansion_candidate),
)
