"""
Signal Age Decay — Sprint 27.
Tracks operational evidence freshness. Old unconfirmed signals lose certainty.
Recurring signals are persistent — recurrence itself is confirmation.
No ML. Pure temporal heuristics on signal age and lifecycle state.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Literal, Optional

DecayState = Literal["fresh", "aging", "fading", "stale", "persistent"]

# Structurally persistent signal types — these don't age out on their own
_STRUCTURAL_TYPES = frozenset({"margin_crisis"})

_NOTES: dict[str, str] = {
    "fresh":      "Сигнал подтверждён недавней операционной динамикой.",
    "aging":      "Сигнал сохраняется без усиления давления.",
    "fading":     "Операционное давление постепенно теряет подтверждение.",
    "stale":      "Сигнал больше не подтверждается активной динамикой.",
    "persistent": "Давление сохраняет устойчивый операционный паттерн.",
}


@dataclasses.dataclass
class SignalDecay:
    decay_state:        DecayState
    age_days:           int
    decay_factor:       float   # 1.0 = no decay; lower = more decayed
    confidence_penalty: int     # subtracted from decision_confidence_score; always <= 0
    operational_note:   str


def compute_signal_decay(
    *,
    insight_type:     str,
    lifecycle_stage:  str | None,
    first_detected:   datetime | None,
    last_confirmed:   datetime | None,
    recurrence_count: int,
    confidence_band:  str | None,
) -> SignalDecay:
    """
    Computes temporal freshness of operational signal evidence.
    Decision order: persistent → fresh → aging → fading → stale.

    age_days is derived from last_confirmed if available (re-confirmation resets freshness),
    otherwise from first_detected. 0 when no date is known (treated as fresh).
    """
    now = datetime.utcnow()

    # Best available timestamp for freshness: last_confirmed resets the clock
    ref = last_confirmed or first_detected
    age_days = (now - ref).days if ref else 0

    # E. PERSISTENT — recurring lifecycle or structurally irreversible type
    # Recurrence itself is confirmation; these don't decay meaningfully.
    is_recurring  = lifecycle_stage == "recurring"
    is_structural = insight_type in _STRUCTURAL_TYPES and recurrence_count >= 1
    if is_recurring or is_structural:
        penalty = 0 if recurrence_count >= 3 else (-2 if recurrence_count >= 2 else -3)
        return SignalDecay(
            decay_state="persistent",
            age_days=age_days,
            decay_factor=1.0,
            confidence_penalty=penalty,
            operational_note=_NOTES["persistent"],
        )

    # A. FRESH — recent evidence
    if age_days < 7:
        return SignalDecay(
            decay_state="fresh",
            age_days=age_days,
            decay_factor=1.0,
            confidence_penalty=0,
            operational_note=_NOTES["fresh"],
        )

    # B. AGING — present but not escalating
    if age_days < 21:
        return SignalDecay(
            decay_state="aging",
            age_days=age_days,
            decay_factor=0.85,
            confidence_penalty=-4,
            operational_note=_NOTES["aging"],
        )

    # C. FADING — no recurrence, weakening evidence
    if age_days < 45:
        return SignalDecay(
            decay_state="fading",
            age_days=age_days,
            decay_factor=0.65,
            confidence_penalty=-10,
            operational_note=_NOTES["fading"],
        )

    # D. STALE — evidence no longer operationally active
    return SignalDecay(
        decay_state="stale",
        age_days=age_days,
        decay_factor=0.35,
        confidence_penalty=-18,
        operational_note=_NOTES["stale"],
    )
