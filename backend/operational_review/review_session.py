"""Operational review session (Sprint 76) — immutable, append-only, deterministic.

A session reviews an ordered sequence of Runtime Application inputs (each a label
+ event log), producing one ReviewSnapshot per input, appended to an append-only
ledger, and sealed with a deterministic review_hash. Same inputs -> same session
-> same review_hash.
"""
from __future__ import annotations

from dataclasses import dataclass

from .review_snapshot import build_snapshot
from .review_ledger import ReviewLedger
from .review_hash import review_domain_hash, REVIEW_DOMAIN


@dataclass(frozen=True)
class OperationalReviewSession:
    snapshots: tuple
    ledger: ReviewLedger
    review_hash: str

    def summary(self) -> dict:
        return {
            "review_hash": self.review_hash,
            "snapshot_count": len(self.snapshots),
            "ledger_length": self.ledger.length,
            "total_findings": sum(len(s.findings) for s in self.snapshots),
            "reviewed_application_hashes": [s.runtime_application_hash for s in self.snapshots],
        }


def build_review_session(reviews) -> OperationalReviewSession:
    """Build a deterministic review session.

    reviews: ordered iterable of (label, event_log). Order is preserved
    (append-only); same order -> same review_hash.
    """
    snapshots = []
    ledger = ReviewLedger()
    for label, event_log in reviews:
        snap = build_snapshot(label, event_log)
        snapshots.append(snap)
        ledger = ledger.record(snap)

    review_hash = review_domain_hash(REVIEW_DOMAIN, {
        "snapshots": [s.to_dict() for s in snapshots],
        "ledger": [e.to_dict() for e in ledger.entries],
    })
    return OperationalReviewSession(
        snapshots=tuple(snapshots),
        ledger=ledger,
        review_hash=review_hash,
    )
