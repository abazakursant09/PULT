"""Cognition Binding boundary (Sprint 83).

Observes cognition output (the InsightItem list from _compute_insights) AFTER
computation, canonicalizes it, and feeds the existing substrate. Read-only,
append-only, descriptive-only, fail-closed. Never alters cognition, Telegram
output, or InsightRecord persistence.
"""
from __future__ import annotations

EXECUTION_AUTHORITY = False
MUTATION_AUTHORITY = False
RECOMMENDATION_AUTHORITY = False
PREDICTION_AUTHORITY = False
DESCRIPTIVE_ONLY = True
APPEND_ONLY = True
FAIL_CLOSED = True
DETERMINISTIC = True
READ_ONLY = True


class CognitionBindingViolation(Exception):
    """Raised when cognition output cannot be canonicalized (fail-closed)."""
