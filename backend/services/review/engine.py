"""
Review Rule Engine — pure deterministic evaluation (A4).

ReviewSnapshot → run every registered rule in stable order → tuple[RuleEvaluation].
No DB persist, no API, no signal building, no AI, no reply generation. Same
snapshot → same result, always.
"""
from __future__ import annotations

from typing import Tuple

from .snapshot import ReviewSnapshot
from .evaluation import RuleEvaluation
from .rules import RULE_REGISTRY


def evaluate_snapshot(snapshot: ReviewSnapshot) -> Tuple[RuleEvaluation, ...]:
    """Evaluate all review rules against a snapshot, in registry order. Pure."""
    return tuple(rule.evaluate(snapshot) for rule in RULE_REGISTRY)
