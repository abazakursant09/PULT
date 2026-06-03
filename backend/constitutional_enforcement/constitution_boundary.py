"""Constitutional Enforcement boundary (Sprint 78).

The enforcement layer verifies the constitution and fails closed on any
violation. It is read-only: no execution, mutation, recommendation, or
prediction authority. It returns VALID/INVALID and a deterministic report.
"""
from __future__ import annotations

EXECUTION_AUTHORITY = False
MUTATION_AUTHORITY = False
RECOMMENDATION_AUTHORITY = False
PREDICTION_AUTHORITY = False
DESCRIPTIVE_ONLY = True
FAIL_CLOSED = True
DETERMINISTIC = True


class EnforcementViolation(Exception):
    """Raised when an operation would exceed read-only enforcement authority."""
