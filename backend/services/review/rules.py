"""
Review Rule Catalog — engine implementation (A4).

6 deterministic rules over a ReviewSnapshot. Pure: no DB, no API, no signal
building, no AI, no reply generation, no autoresponder, no marketplace-specific
logic. Review Assistant is a reputation contour.

Every rule:
  1. declares `required_fields` (field_availability keys it needs);
  2. is NOT_EVALUATED (with reason) when any required field is unavailable —
     absence of data is never treated as "ok";
  3. when evaluable, is TRIGGERED (snapshot-derived evidence) or NOT_TRIGGERED.

Safety doctrine guard: evidence-building raises if a RISK/ATTENTION review somehow
carries AUTO in allowed_modes — AUTO is never permitted there. Evidence stores a
short text_excerpt, never the full review text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Union

from .snapshot import ReviewSnapshot
from .evaluation import RuleEvaluation, RuleResult
from .safety_policy import (
    SAFE, ATTENTION, RISK, AUTO, MANUAL_APPROVAL, complaint_markers_found,
)

RULE_CATALOG_VERSION = "1"

_Pred = Tuple[str, Union[dict, str, None]]
_EXCERPT_LEN = 80


@dataclass(frozen=True)
class Rule:
    problem_type: str
    category: str
    severity: str
    estimated_effect_type: str
    detectability: str
    required_fields: Tuple[str, ...]
    predicate: Callable[[ReviewSnapshot], _Pred]

    def evaluate(self, snap: ReviewSnapshot) -> RuleEvaluation:
        missing = [f for f in self.required_fields if not snap.field_availability.get(f)]
        if missing:
            return self._mk(RuleResult.NOT_EVALUATED, reason=f"missing_fields: {','.join(missing)}")
        kind, payload = self.predicate(snap)
        if kind == "triggered":
            return self._mk(RuleResult.TRIGGERED, evidence=dict(payload))   # type: ignore[arg-type]
        if kind == "not_evaluated":
            return self._mk(RuleResult.NOT_EVALUATED, reason=str(payload))
        return self._mk(RuleResult.NOT_TRIGGERED)

    def _mk(self, result, *, evidence=None, reason=None) -> RuleEvaluation:
        return RuleEvaluation(
            problem_type=self.problem_type, category=self.category, severity=self.severity,
            estimated_effect_type=self.estimated_effect_type, detectability=self.detectability,
            result=result, evidence=evidence, reason=reason,
        )


def _excerpt(t: Optional[str]) -> Optional[str]:
    return t[:_EXCERPT_LEN] if t else None


def _evidence(s: ReviewSnapshot, markers: Optional[list] = None) -> dict:
    # Doctrine guard: AUTO must never be permitted for RISK/ATTENTION reviews.
    if s.safety_category in (RISK, ATTENTION) and AUTO in (s.allowed_modes or ()):
        raise AssertionError("safety violation: auto allowed for non-SAFE review")
    return {
        "review_id": s.review_id, "rating": s.rating, "has_text": s.has_text,
        "answered": s.answered, "safety_category": s.safety_category,
        "allowed_modes": list(s.allowed_modes), "default_mode": s.default_mode,
        "complaint_markers_found": markers if markers is not None else complaint_markers_found(s.text),
        "text_excerpt": _excerpt(s.text),
    }


# ── predicates ───────────────────────────────────────────────────────────────

def _p_unanswered_negative(s: ReviewSnapshot) -> _Pred:
    if s.safety_category == RISK and not s.answered:
        return "triggered", _evidence(s)
    return "not_triggered", None


def _p_unanswered_attention(s: ReviewSnapshot) -> _Pred:
    if s.safety_category == ATTENTION and not s.answered:
        return "triggered", _evidence(s)
    return "not_triggered", None


def _p_safe_can_reply(s: ReviewSnapshot) -> _Pred:
    if (s.safety_category == SAFE and not s.answered
            and any(m in (s.allowed_modes or ()) for m in (MANUAL_APPROVAL, AUTO))):
        return "triggered", _evidence(s)
    return "not_triggered", None


def _p_five_star_without_text(s: ReviewSnapshot) -> _Pred:
    if s.rating == 5 and not s.has_text and not s.answered:
        return "triggered", _evidence(s)
    return "not_triggered", None


def _p_complaint_detected(s: ReviewSnapshot) -> _Pred:
    markers = complaint_markers_found(s.text)
    if s.safety_category == RISK and markers:
        return "triggered", _evidence(s, markers)
    return "not_triggered", None


def _p_already_answered(s: ReviewSnapshot) -> _Pred:
    if s.answered:
        return "triggered", _evidence(s)
    return "not_triggered", None


# ── closed, stable-ordered registry ──────────────────────────────────────────

RULE_REGISTRY: Tuple[Rule, ...] = (
    Rule("unanswered_negative_review", "RISK", "critical", "reputation_risk",
         "reviews", ("safety_category", "answered"), _p_unanswered_negative),
    Rule("unanswered_attention_review", "ATTENTION", "medium", "reputation_risk",
         "reviews", ("safety_category", "answered"), _p_unanswered_attention),
    Rule("safe_review_can_reply", "SAFE", "low", "reputation_opportunity",
         "reviews", ("safety_category", "answered"), _p_safe_can_reply),
    Rule("five_star_without_text", "SAFE", "low", "reputation_opportunity",
         "reviews", ("rating", "has_text", "answered"), _p_five_star_without_text),
    Rule("complaint_detected", "RISK", "critical", "reputation_risk",
         "requires_text", ("safety_category", "text"), _p_complaint_detected),
    Rule("already_answered", "STATUS", "low", "none",
         "reviews", ("answered",), _p_already_answered),
)
