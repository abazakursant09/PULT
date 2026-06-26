"""
Pricing Rule Engine result types (A3-pre).

A rule evaluation is one of three OUTCOMES — never collapsed:
  triggered      → margin problem found, with deterministic evidence (observed finance).
  not_triggered  → rule ran, predicate false (definitively no problem).
  not_evaluated  → a required snapshot field OR a required external threshold is
                   absent; carries a reason. Absence is NEVER "no problem".

Pure data. No I/O, no DB, no AI, no forecast.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Mapping, Optional


class RuleResult(str, enum.Enum):
    TRIGGERED = "triggered"
    NOT_TRIGGERED = "not_triggered"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class PricingRuleEvaluation:
    problem_type: str
    category: str                 # always "pricing"
    severity: str
    estimated_effect_type: str    # margin_loss | margin_below_target | price_below_floor
    detectability: str            # finance | rule
    result: RuleResult
    evidence: Optional[Mapping[str, object]] = None   # set only when TRIGGERED
    reason: Optional[str] = None                       # set only when NOT_EVALUATED
