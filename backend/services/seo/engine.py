"""
SEO Audit Engine — pure deterministic evaluation (A4).

CardSnapshot → run every registered rule in stable order → tuple[RuleEvaluation].
No DB persist, no API, no signal building, no Decision bridge, no measurement.
Same snapshot → same result, always.
"""
from __future__ import annotations

from typing import Tuple

from .card_snapshot import CardSnapshot
from .evaluation import RuleEvaluation
from .rules import RULE_REGISTRY


def evaluate_snapshot(snapshot: CardSnapshot) -> Tuple[RuleEvaluation, ...]:
    """Evaluate all rules against a snapshot, in registry order. Pure function."""
    return tuple(rule.evaluate(snapshot) for rule in RULE_REGISTRY)
