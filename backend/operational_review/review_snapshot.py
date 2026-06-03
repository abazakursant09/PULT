"""Review snapshot (Sprint 76) — immutable observation of one Runtime Application.

A snapshot binds the reviewed `runtime_application_hash` to the descriptive
findings observed at that point. Deterministic; same application -> same snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass

from runtime_application import build_runtime_application

from .review_finding import derive_findings, findings_hash
from .review_hash import review_domain_hash, SNAPSHOT_DOMAIN


@dataclass(frozen=True)
class ReviewSnapshot:
    label: str
    runtime_application_hash: str
    runtime_event_count: int
    findings: tuple
    snapshot_hash: str

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "runtime_application_hash": self.runtime_application_hash,
            "runtime_event_count": self.runtime_event_count,
            "findings": [f.to_dict() for f in self.findings],
        }


def build_snapshot(label: str, event_log) -> ReviewSnapshot:
    app = build_runtime_application(event_log)
    findings = derive_findings(app)
    snapshot_hash = review_domain_hash(SNAPSHOT_DOMAIN, {
        "label": label,
        "runtime_application_hash": app.runtime_application_hash,
        "findings_hash": findings_hash(findings),
        "runtime_event_count": app.runtime_event_count,
    })
    return ReviewSnapshot(
        label=label,
        runtime_application_hash=app.runtime_application_hash,
        runtime_event_count=app.runtime_event_count,
        findings=findings,
        snapshot_hash=snapshot_hash,
    )
