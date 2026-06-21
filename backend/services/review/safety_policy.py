"""
Review Safety Policy (Review A3) — deterministic classification of a review into a
safety category + the ONLY reply modes PULT may recommend for it.

Human-Control + Negative-Review doctrine, encoded as pure rules (no AI):
  RISK      (1-2 stars, or hard complaint markers: defect/counterfeit/return/
            not-arrived) → allowed {off, manual_only}, default manual_only. AUTO
            is NEVER allowed.
  ATTENTION (3 stars, or a 4-star WITH text — treated as possibly mixed) →
            allowed {off, manual_approval}, default manual_approval. No AUTO.
  SAFE      (5 stars, or a 4-star without text) → allowed {off, manual_approval,
            auto}, default manual_approval.

Conservative by design: AUTO is offered only where it is clearly safe. No fake
impact — this only sets which modes are permitted, never a rating promise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# reply modes (match review_signal.safety_mode values)
OFF = "off"
MANUAL_APPROVAL = "manual_approval"
AUTO = "auto"
MANUAL_ONLY = "manual_only"

# safety categories
SAFE = "SAFE"
ATTENTION = "ATTENTION"
RISK = "RISK"

# Hard complaint markers → RISK regardless of stars (defect/counterfeit/return/
# not-arrived/broken/fraud). Targeted to avoid false positives on praise.
_COMPLAINT_MARKERS = (
    "брак", "дефект", "подделк", "контрафакт", "возврат", "вернул",
    "сломал", "разбит", "не работает", "не пришл", "не доставил", "обман",
)


@dataclass(frozen=True)
class SafetyDecision:
    category: str
    allowed_modes: Tuple[str, ...]
    default_mode: str


def has_complaint_marker(text: Optional[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in _COMPLAINT_MARKERS)


def classify_safety(rating: Optional[int], has_text: bool, text: Optional[str]) -> SafetyDecision:
    """Pure deterministic safety classification of one review."""
    if rating is not None and rating <= 2:
        return SafetyDecision(RISK, (OFF, MANUAL_ONLY), MANUAL_ONLY)
    if has_text and has_complaint_marker(text):
        return SafetyDecision(RISK, (OFF, MANUAL_ONLY), MANUAL_ONLY)
    if rating == 3:
        return SafetyDecision(ATTENTION, (OFF, MANUAL_APPROVAL), MANUAL_APPROVAL)
    if rating == 4:
        # positive 4 (no text) is SAFE; a 4-star with text may be mixed → ATTENTION
        if has_text:
            return SafetyDecision(ATTENTION, (OFF, MANUAL_APPROVAL), MANUAL_APPROVAL)
        return SafetyDecision(SAFE, (OFF, MANUAL_APPROVAL, AUTO), MANUAL_APPROVAL)
    if rating == 5:
        return SafetyDecision(SAFE, (OFF, MANUAL_APPROVAL, AUTO), MANUAL_APPROVAL)
    # unknown rating → conservative, no AUTO
    return SafetyDecision(ATTENTION, (OFF, MANUAL_APPROVAL), MANUAL_APPROVAL)
