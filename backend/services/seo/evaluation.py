"""
SEO Rule Engine result types (A4).

A rule evaluation is one of three OUTCOMES — never collapsed:
  triggered      → problem found, with deterministic evidence (snapshot-derived).
  not_triggered  → rule ran, predicate false (problem definitively absent).
  not_evaluated  → required snapshot fields/constraints absent; carries a reason.

Pure data only. No I/O, no DB, no metrics.
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
class RuleEvaluation:
    problem_type: str
    category: str
    severity: str
    estimated_effect_type: str
    detectability: str
    result: RuleResult
    evidence: Optional[Mapping[str, object]] = None   # set only when TRIGGERED
    reason: Optional[str] = None                       # set only when NOT_EVALUATED
