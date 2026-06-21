"""
Legal Rule Engine (Legal A4) — pure, read-only requirement evaluation.

Takes a LegalSnapshot and returns one LegalRuleEvaluationResult per requirement
candidate. Pure: no DB, no persist, no legal_finding, no legal_signal, no API, no
AI, no forecast, no money, no score.

Three outcomes — never collapsed:
  detected      → a POTENTIAL risk / needs_review was observed (NOT a legal
                  conclusion, NOT a violation, NOT compliance=false).
  not_detected  → required inputs are present AND the rule actually ran without
                  observing the risk (NOT "compliant", NOT a guarantee).
  not_evaluated → required inputs are missing — the rule could NOT run. A missing
                  input is NEVER read as compliance.

recommended_action is always ADVISORY and drawn from a closed allowlist:
  check_requirement | collect_document | verify_marketplace_terms |
  consult_lawyer | review_content_claim

severity / risk_band describe the INHERENT advisory weight of the requirement
(how serious IF it applied) — not a verdict on the subject.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from .snapshot import LegalSnapshot, REQUIREMENT_CANDIDATES

RULE_CATALOG_VERSION = "1"

# closed advisory action allowlist
ACTION_CHECK_REQUIREMENT = "check_requirement"
ACTION_COLLECT_DOCUMENT = "collect_document"
ACTION_VERIFY_MARKETPLACE_TERMS = "verify_marketplace_terms"
ACTION_CONSULT_LAWYER = "consult_lawyer"
ACTION_REVIEW_CONTENT_CLAIM = "review_content_claim"
ALLOWED_ACTIONS = frozenset({
    ACTION_CHECK_REQUIREMENT, ACTION_COLLECT_DOCUMENT, ACTION_VERIFY_MARKETPLACE_TERMS,
    ACTION_CONSULT_LAWYER, ACTION_REVIEW_CONTENT_CLAIM,
})

# minimal, cautious claim denylist — a match is POTENTIAL risk only, never a verdict
CLAIM_DENYLIST: Tuple[str, ...] = (
    "лечит", "гарантирует", "100%", "сертифицирован", "официальный", "оригинал",
)


class LegalResult(str, enum.Enum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class LegalRuleEvaluationResult:
    requirement_type:   str
    result:             LegalResult
    reason:             Optional[str]
    evidence:           Mapping[str, object]
    recommended_action: str            # advisory, from ALLOWED_ACTIONS
    severity:           str            # inherent advisory: critical|high|medium|low
    risk_band:          str            # inherent advisory: high|medium|low
    source:             str            # provenance


# requirement → (severity, risk_band, action, required field_availability keys)
# required keys reference LegalSnapshot.field_availability; keys PULT cannot store
# (certificate_data, trademark_data, labeling_data, offer_terms_data,
# return_policy_data) are simply absent → not_evaluated, never assumed compliant.
_RULES: Mapping[str, dict] = {
    "product_certification": {
        "severity": "high", "risk_band": "high", "action": ACTION_COLLECT_DOCUMENT,
        "required": ("product_category", "certificate_data"),
    },
    "trademark_usage": {
        "severity": "high", "risk_band": "high", "action": ACTION_CHECK_REQUIREMENT,
        "required": ("trademark_data",),
    },
    "labeling_requirements": {
        "severity": "medium", "risk_band": "medium", "action": ACTION_COLLECT_DOCUMENT,
        "required": ("product_category", "labeling_data"),
    },
    "marketplace_offer_terms": {
        "severity": "medium", "risk_band": "medium", "action": ACTION_VERIFY_MARKETPLACE_TERMS,
        "required": ("marketplace", "offer_terms_data"),
    },
    "return_policy_obligations": {
        "severity": "medium", "risk_band": "medium", "action": ACTION_VERIFY_MARKETPLACE_TERMS,
        "required": ("marketplace", "return_policy_data"),
    },
    # content_claim_risk is handled specially (deterministic keyword scan)
    "content_claim_risk": {
        "severity": "medium", "risk_band": "medium", "action": ACTION_REVIEW_CONTENT_CLAIM,
        "required": ("product_text",),
    },
}


def _mk(rt: str, result: LegalResult, *, reason, evidence) -> LegalRuleEvaluationResult:
    spec = _RULES[rt]
    res = LegalRuleEvaluationResult(
        requirement_type=rt, result=result, reason=reason, evidence=dict(evidence),
        recommended_action=spec["action"], severity=spec["severity"],
        risk_band=spec["risk_band"], source="internal",
    )
    assert res.recommended_action in ALLOWED_ACTIONS, res.recommended_action
    return res


def _matched_claims(text: Optional[str]) -> list:
    if not text:
        return []
    low = text.lower()
    return [k for k in CLAIM_DENYLIST if k in low]


def _eval_content_claim(snap: LegalSnapshot) -> LegalRuleEvaluationResult:
    rt = "content_claim_risk"
    if not snap.field_availability.get("product_text") or not snap.content_text:
        return _mk(rt, LegalResult.NOT_EVALUATED,
                   reason="missing_inputs: product_text",
                   evidence={"content_present": False})
    matched = _matched_claims(snap.content_text)
    if matched:
        # potential risk / needs_review — NOT a legal conclusion
        return _mk(rt, LegalResult.DETECTED, reason="potential_risk: claim keywords matched",
                   evidence={"content_present": True, "matched_keywords": matched})
    return _mk(rt, LegalResult.NOT_DETECTED, reason=None,
               evidence={"content_present": True, "matched_keywords": []})


def _eval_generic(rt: str, snap: LegalSnapshot) -> LegalRuleEvaluationResult:
    required = _RULES[rt]["required"]
    fa = snap.field_availability
    absent = [i for i in required if not fa.get(i)]
    if absent:
        return _mk(rt, LegalResult.NOT_EVALUATED,
                   reason=f"missing_inputs: {','.join(absent)}",
                   evidence={"missing_inputs": absent})
    # all required inputs present → rule ran, no risk observed (NOT "compliant")
    return _mk(rt, LegalResult.NOT_DETECTED, reason=None, evidence={"inputs_present": list(required)})


def evaluate_snapshot(snapshot: LegalSnapshot) -> Tuple[LegalRuleEvaluationResult, ...]:
    """Evaluate every requirement candidate against the snapshot, stable order. Pure."""
    out = []
    for rt in REQUIREMENT_CANDIDATES:
        if rt == "content_claim_risk":
            out.append(_eval_content_claim(snapshot))
        else:
            out.append(_eval_generic(rt, snapshot))
    return tuple(out)
