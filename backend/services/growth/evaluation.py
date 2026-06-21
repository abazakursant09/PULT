"""
Growth Rule Engine result types (A4).

A rule evaluation is one of three OUTCOMES — never collapsed:
  triggered      → opportunity found, with deterministic evidence (snapshot-derived).
  not_triggered  → rule ran, predicate false (definitively no opportunity).
  not_evaluated  → a required snapshot field OR a required external threshold is
                   absent; carries a reason.

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
class GrowthRuleEvaluation:
    problem_type: str
    category: str                 # pricing|advertising|seo|inventory|reputation
    severity: str
    estimated_effect_type: str    # revenue_gain|margin_gain|traffic_gain|reputation_upside
    detectability: str
    result: RuleResult
    evidence: Optional[Mapping[str, object]] = None   # set only when TRIGGERED
    reason: Optional[str] = None                       # set only when NOT_EVALUATED
