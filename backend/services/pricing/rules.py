"""
Pricing/Margin Rule Catalog — engine implementation (A3-pre).

Deterministic rules over a PricingSnapshot + external PricingThresholds. Pure: no
DB, no API, no persist, no signal building, no AI, no forecast, NO competitor data,
NO recommended/generated price, NO pricing score. Detects observed margin problems
in finance PULT already stores.

Every rule:
  1. declares `required_fields` (field_availability keys) and `required_thresholds`;
  2. is NOT_EVALUATED (with reason) when any required field is unavailable OR any
     required threshold is None — absence is never "no problem", thresholds have NO
     defaults (must come from outside);
  3. when evaluable, is TRIGGERED (snapshot-derived evidence + thresholds_used) or
     NOT_TRIGGERED.

Evidence holds ONLY observed snapshot facts + the thresholds applied.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Union

from .snapshot import PricingSnapshot
from .evaluation import PricingRuleEvaluation, RuleResult

RULE_CATALOG_VERSION = "1"

_Pred = Tuple[str, Union[dict, str, None]]


@dataclass(frozen=True)
class PricingThresholds:
    """External pricing thresholds. NO defaults — a missing (None) threshold makes
    any rule that needs it NOT_EVALUATED."""
    min_revenue_for_pricing_signal: Optional[float] = None
    target_margin_pct: Optional[float] = None


@dataclass(frozen=True)
class Rule:
    problem_type: str
    category: str
    severity: str
    estimated_effect_type: str
    detectability: str
    required_fields: Tuple[str, ...]
    required_thresholds: Tuple[str, ...]
    predicate: Callable[[PricingSnapshot, PricingThresholds], _Pred]

    def evaluate(self, snap: PricingSnapshot, th: PricingThresholds) -> PricingRuleEvaluation:
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

    def _mk(self, result, *, evidence=None, reason=None) -> PricingRuleEvaluation:
        return PricingRuleEvaluation(
            problem_type=self.problem_type, category=self.category, severity=self.severity,
            estimated_effect_type=self.estimated_effect_type, detectability=self.detectability,
            result=result, evidence=evidence, reason=reason,
        )


def _base_evidence(s: PricingSnapshot) -> dict:
    """Observed snapshot facts only — no forecast / recommended / competitor data."""
    return {"revenue": s.revenue, "net_profit": s.net_profit, "margin": s.margin,
            "current_price": s.current_price, "floor_price": s.floor_price}


def _ev(s: PricingSnapshot, thresholds_used: dict) -> dict:
    e = _base_evidence(s)
    e["thresholds_used"] = thresholds_used
    return e


# ── predicates (observed finance only) ───────────────────────────────────────

def _p_negative_margin(s: PricingSnapshot, th: PricingThresholds) -> _Pred:
    # losing money at the current price: net_profit < 0 over a non-trivial revenue
    used = {"min_revenue_for_pricing_signal": th.min_revenue_for_pricing_signal}
    if s.revenue >= th.min_revenue_for_pricing_signal and s.net_profit < 0:
        return "triggered", _ev(s, used)
    return "not_triggered", None


def _p_margin_below_target(s: PricingSnapshot, th: PricingThresholds) -> _Pred:
    # thin but non-negative margin under the seller's target floor
    used = {"min_revenue_for_pricing_signal": th.min_revenue_for_pricing_signal,
            "target_margin_pct": th.target_margin_pct}
    if (s.revenue >= th.min_revenue_for_pricing_signal
            and s.margin is not None and s.margin >= 0
            and s.margin < th.target_margin_pct):
        return "triggered", _ev(s, used)
    return "not_triggered", None


def _p_price_below_floor(s: PricingSnapshot, th: PricingThresholds) -> _Pred:
    # current selling price below the seller-configured floor (rule, never a recommendation)
    if s.current_price < s.floor_price:
        return "triggered", _ev(s, {})
    return "not_triggered", None


# ── closed, stable-ordered registry ──────────────────────────────────────────

RULE_REGISTRY: Tuple[Rule, ...] = (
    Rule("negative_margin", "pricing", "critical", "margin_loss", "finance",
         ("revenue", "net_profit"),
         ("min_revenue_for_pricing_signal",), _p_negative_margin),
    Rule("margin_below_target", "pricing", "high", "margin_below_target", "finance",
         ("revenue", "margin"),
         ("min_revenue_for_pricing_signal", "target_margin_pct"), _p_margin_below_target),
    Rule("price_below_floor", "pricing", "high", "price_below_floor", "rule",
         ("current_price", "floor_price"),
         (), _p_price_below_floor),
)
