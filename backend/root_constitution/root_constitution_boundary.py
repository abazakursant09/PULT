"""Root Constitution boundary (Sprint 77).

The root constitution aggregates the seven frozen constitutional hashes into one
deterministic identity. It is read-only: no execution, mutation, recommendation,
or prediction authority. It computes a hash and verifies it. Nothing else.
"""
from __future__ import annotations

EXECUTION_AUTHORITY = False
MUTATION_AUTHORITY = False
RECOMMENDATION_AUTHORITY = False
PREDICTION_AUTHORITY = False
DESCRIPTIVE_ONLY = True
DETERMINISTIC = True


class RootConstitutionViolation(Exception):
    """Raised when an operation would exceed read-only attestation authority."""
