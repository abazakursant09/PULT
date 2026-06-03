"""Review attestation (Sprint 76) — replay reconstruction + tamper detection.

Binds the ordered review inputs to the sealed session. Verification recomputes
the entire session from the inputs and confirms byte-identical review_hash and
every snapshot/ledger hash. Any tamper -> False (fail-closed).
"""
from __future__ import annotations

from dataclasses import dataclass

from .review_session import OperationalReviewSession, build_review_session


@dataclass(frozen=True)
class ReviewAttestation:
    reviews: tuple
    session: OperationalReviewSession


def attest_review(reviews) -> ReviewAttestation:
    reviews = tuple((label, tuple(log)) for label, log in reviews)
    return ReviewAttestation(reviews=reviews, session=build_review_session(reviews))


def verify_attestation(att: ReviewAttestation) -> bool:
    rebuilt = build_review_session(att.reviews)
    s = att.session
    if rebuilt.review_hash != s.review_hash:
        return False
    if rebuilt.ledger.ledger_hash() != s.ledger.ledger_hash():
        return False
    if len(rebuilt.snapshots) != len(s.snapshots):
        return False
    return all(a.snapshot_hash == b.snapshot_hash
               for a, b in zip(rebuilt.snapshots, s.snapshots))
