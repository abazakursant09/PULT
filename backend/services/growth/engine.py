"""
Growth Rule Engine — pure deterministic evaluation (A4).

GrowthSnapshot + GrowthThresholds → run every registered rule in stable order →
tuple[GrowthRuleEvaluation]. No DB persist, no API, no signal building, no AI, no
forecast. Same snapshot + same thresholds → same result, always.
"""
from __future__ import annotations

from typing import Tuple

from .snapshot import GrowthSnapshot
from .evaluation import GrowthRuleEvaluation
from .rules import RULE_REGISTRY, GrowthThresholds


def evaluate_snapshot(
    snapshot: GrowthSnapshot, thresholds: GrowthThresholds,
) -> Tuple[GrowthRuleEvaluation, ...]:
    """Evaluate all growth rules against a snapshot + thresholds, in registry order. Pure."""
    return tuple(rule.evaluate(snapshot, thresholds) for rule in RULE_REGISTRY)
