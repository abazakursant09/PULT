"""Operational Review boundary (Sprint 76).

The review layer observes Runtime Application topology, derives descriptive
findings, and records them in an append-only ledger. It holds NO authority to
mutate, execute, recommend, rank, or infer future outcomes. Observe -> Review ->
Record. Nothing more.

Determinism note: the review layer uses NO clocks. "When reviewed" is recorded
as a deterministic review sequence ordinal, never a wall-clock timestamp.
"""
from __future__ import annotations

EXECUTION_AUTHORITY = False
MUTATION_AUTHORITY = False
RECOMMENDATION_AUTHORITY = False
PREDICTION_AUTHORITY = False
RANKING_AUTHORITY = False
DESCRIPTIVE_ONLY = True
APPEND_ONLY = True
DETERMINISTIC = True


class ReviewBoundaryViolation(Exception):
    """Raised when an operation would exceed observe/review/record authority."""


def assert_observe_review_record_only() -> None:
    if (EXECUTION_AUTHORITY or MUTATION_AUTHORITY or RECOMMENDATION_AUTHORITY
            or PREDICTION_AUTHORITY or RANKING_AUTHORITY or not DESCRIPTIVE_ONLY):
        raise ReviewBoundaryViolation("operational_review is observe/review/record only")
