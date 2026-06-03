"""Runtime Binding boundary (Sprint 80).

The first runtime-to-constitution bridge. Reads the live UserEvent stream
READ-ONLY, canonicalizes it into deterministic constitutional events, and feeds
the existing substrate without changing it. No execution, mutation,
recommendation, or prediction authority. Fail-closed.
"""
from __future__ import annotations

EXECUTION_AUTHORITY = False
MUTATION_AUTHORITY = False
RECOMMENDATION_AUTHORITY = False
PREDICTION_AUTHORITY = False
DESCRIPTIVE_ONLY = True
FAIL_CLOSED = True
DETERMINISTIC = True
READ_ONLY = True


class BindingViolation(Exception):
    """Raised when input cannot be canonicalized under fail-closed rules."""
