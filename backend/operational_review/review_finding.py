"""Review findings (Sprint 76) — descriptive only.

A finding states WHAT WAS OBSERVED, in the past tense, with no judgement, no
modality, and no future inference. Every finding's text comes from a closed
catalog of descriptive phrases. There is no "should", "recommend", "likely",
"expected", "investigate", "risk", or "failure" — those are structurally absent.
"""
from __future__ import annotations

from dataclasses import dataclass

from .review_hash import review_domain_hash, FINDING_DOMAIN

# Closed catalog of allowed descriptive phrases (observational, past tense).
FINDING_CATALOG = {
    "pressure_accumulation": "pressure accumulation observed",
    "dissipation_surface": "dissipation surface observed",
    "drift_visible": "drift visible",
    "instability_marker": "instability marker observed",
    "intervention_surface": "intervention surface present",
}


@dataclass(frozen=True)
class ReviewFinding:
    finding_code: str
    subject: str
    descriptive_text: str
    evidence: dict

    def to_dict(self) -> dict:
        return {
            "finding_code": self.finding_code,
            "subject": self.subject,
            "descriptive_text": self.descriptive_text,
            "evidence": self.evidence,
        }


def _finding(code: str, subject: str, evidence: dict) -> ReviewFinding:
    return ReviewFinding(
        finding_code=code,
        subject=subject,
        descriptive_text=FINDING_CATALOG[code],
        evidence=evidence,
    )


def derive_findings(application) -> tuple:
    """Derive descriptive findings from a Runtime Application. Sorted, deterministic.

    Pure observation: each finding mirrors a structure already present in the
    topology. No ranking, no scoring beyond the stated numbers, no inference.
    """
    findings = []

    for region, weight in application.pressure["accumulation_regions"].items():
        findings.append(_finding("pressure_accumulation", region, {"accumulated_weight": weight}))

    for region in application.pressure["dissipation_surfaces"]:
        findings.append(_finding("dissipation_surface", region, {}))

    for region, count in application.drift["drift_regions"].items():
        findings.append(_finding("drift_visible", region, {"drift_events": count}))

    for region in application.drift["instability_markers"]:
        findings.append(_finding("instability_marker", region, {}))

    for surf in application.interventions:
        findings.append(_finding("intervention_surface", surf["region"],
                                 {"accumulated_weight": surf["accumulated_weight"]}))

    return tuple(sorted(findings, key=lambda f: (f.finding_code, f.subject)))


def findings_hash(findings) -> str:
    return review_domain_hash(FINDING_DOMAIN, [f.to_dict() for f in findings])
