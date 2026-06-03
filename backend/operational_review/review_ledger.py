"""Review ledger (Sprint 76) — append-only record.

Records what was observed, WHEN it was reviewed (as a deterministic review
sequence ordinal — never a clock), and which runtime_application_hash was
reviewed. No execution authority, no workflow/task/ticket system, no mutation.
"""
from __future__ import annotations

from dataclasses import dataclass

from .review_hash import review_domain_hash, LEDGER_DOMAIN


@dataclass(frozen=True)
class ReviewLedgerEntry:
    review_sequence: int          # deterministic "when reviewed" (ordinal, not a clock)
    label: str
    runtime_application_hash: str
    snapshot_hash: str
    finding_count: int

    def to_dict(self) -> dict:
        return {
            "review_sequence": self.review_sequence,
            "label": self.label,
            "runtime_application_hash": self.runtime_application_hash,
            "snapshot_hash": self.snapshot_hash,
            "finding_count": self.finding_count,
        }


@dataclass(frozen=True)
class ReviewLedger:
    entries: tuple = ()

    def record(self, snapshot) -> "ReviewLedger":
        """Return a NEW ledger with one entry appended (append-only lineage)."""
        entry = ReviewLedgerEntry(
            review_sequence=len(self.entries),
            label=snapshot.label,
            runtime_application_hash=snapshot.runtime_application_hash,
            snapshot_hash=snapshot.snapshot_hash,
            finding_count=len(snapshot.findings),
        )
        return ReviewLedger(entries=self.entries + (entry,))

    @property
    def length(self) -> int:
        return len(self.entries)

    def ledger_hash(self) -> str:
        return review_domain_hash(LEDGER_DOMAIN, [e.to_dict() for e in self.entries])
