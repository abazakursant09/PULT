"""
Advertising Rule Engine — pure deterministic evaluation (A4).

AdvertisingSnapshot → run every registered rule in stable order →
tuple[RuleEvaluation]. No DB persist, no API, no signal building, no Decision
bridge, no measurement. Same snapshot → same result, always.
"""
from __future__ import annotations

from typing import Tuple

from .snapshot import AdvertisingSnapshot
from .evaluation import RuleEvaluation
from .rules import RULE_REGISTRY


def evaluate_snapshot(snapshot: AdvertisingSnapshot) -> Tuple[RuleEvaluation, ...]:
    """Evaluate all advertising rules against a snapshot, in registry order. Pure."""
    return tuple(rule.evaluate(snapshot) for rule in RULE_REGISTRY)
