"""Operational Review Layer (Sprint 76) — Observe -> Review -> Record.

Deterministic, append-only, descriptive-only review over Runtime Application
topology. Never mutates, executes, recommends, ranks, or infers future outcomes.
Read-only consumer of the frozen substrate.
"""
from __future__ import annotations

from .review_boundary import (
    EXECUTION_AUTHORITY, MUTATION_AUTHORITY, RECOMMENDATION_AUTHORITY,
    PREDICTION_AUTHORITY, RANKING_AUTHORITY, DESCRIPTIVE_ONLY, APPEND_ONLY,
    DETERMINISTIC, ReviewBoundaryViolation, assert_observe_review_record_only,
)
from .review_finding import (
    ReviewFinding, FINDING_CATALOG, derive_findings, findings_hash,
)
from .review_snapshot import ReviewSnapshot, build_snapshot
from .review_ledger import ReviewLedger, ReviewLedgerEntry
from .review_session import OperationalReviewSession, build_review_session
from .review_attestation import ReviewAttestation, attest_review, verify_attestation
from .review_hash import canonical_bytes, sha256_hex, domain_hash, REVIEW_DOMAIN

__all__ = [
    "EXECUTION_AUTHORITY", "MUTATION_AUTHORITY", "RECOMMENDATION_AUTHORITY",
    "PREDICTION_AUTHORITY", "RANKING_AUTHORITY", "DESCRIPTIVE_ONLY", "APPEND_ONLY",
    "DETERMINISTIC", "ReviewBoundaryViolation", "assert_observe_review_record_only",
    "ReviewFinding", "FINDING_CATALOG", "derive_findings", "findings_hash",
    "ReviewSnapshot", "build_snapshot",
    "ReviewLedger", "ReviewLedgerEntry",
    "OperationalReviewSession", "build_review_session",
    "ReviewAttestation", "attest_review", "verify_attestation",
    "canonical_bytes", "sha256_hex", "domain_hash", "REVIEW_DOMAIN",
]
