"""
Measurement close bridge (Slice 4: manual close).

Closes due still_open DecisionOutcome rows by reusing the existing, tested
decision_validation.select_due_outcomes + close_measurement. Close only — no
attribution, no learning, no effect calc, no marketplace writes. No scheduler:
this is a manual/service-level entry point (background wiring is a later slice).

Honesty rules (owned by close_measurement / metric_reader):
- null baseline  → insufficient_data (nothing honest to compare; no token).
- metric unreadable / non-WB / no adapter → insufficient_data (never fabricated).
- real baseline + favorable/unfavorable realized delta → confirmed / refuted.

Missing credential → SKIP (leave still_open). A credential gap may be temporary;
we never burn an outcome to insufficient_data because a token was momentarily
absent.

Token resolved server-side via the same credential path as Slice 3
(execution_measurement_bridge). No token over HTTP, no marketplace client import,
no executor import.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision import Decision
from models.decision_memory import DecisionMemory
from models.decision_outcome import DecisionOutcomeLabel
from models.observation import Observation
from repositories import decision_outcome as outcome_repo
from services.decision_memory import record_decision_memory
from services.decision_validation import close_measurement, select_due_outcomes
from services.refuted_loop import create_followup_for_refuted
# Reuse the exact Slice 3 credential path (server-side, no clients/executor).
from services.execution_measurement_bridge import _ACTION_SCOPE, _resolve_token

log = logging.getLogger(__name__)

_STILL_OPEN = DecisionOutcomeLabel.STILL_OPEN.value
_CONFIRMED = DecisionOutcomeLabel.CONFIRMED.value
_REFUTED = DecisionOutcomeLabel.REFUTED.value
_INSUFFICIENT = DecisionOutcomeLabel.INSUFFICIENT_DATA.value


@dataclass
class CloseSummary:
    total_due: int = 0
    confirmed: int = 0
    refuted: int = 0
    insufficient: int = 0
    skipped: int = 0
    errors: int = 0


async def close_due_measurements(
    db: AsyncSession, *, now: Optional[datetime] = None, limit: Optional[int] = None
) -> CloseSummary:
    """
    Close every due still_open outcome. Idempotent: select returns only
    still_open and close_measurement guards on label, so re-runs never overwrite
    a closed outcome. Per-outcome failures are isolated (rollback + continue).
    """
    due = await select_due_outcomes(db, now=now, limit=limit)
    # Snapshot ids before any commit/rollback so the loop re-fetches fresh,
    # attached rows (avoids post-commit attribute expiry on async sessions).
    decision_ids = [o.decision_id for o in due]
    summary = CloseSummary(total_due=len(decision_ids))

    for decision_id in decision_ids:
        try:
            outcome = await outcome_repo.get_by_decision_id(db, decision_id)
            if outcome is None or outcome.outcome_label != _STILL_OPEN:
                summary.skipped += 1
                continue

            if outcome.baseline_observation_id is None:
                # Nothing honest to compare → insufficient_data (no token needed).
                closed = await close_measurement(db, outcome=outcome, token=None, now=now)
            else:
                baseline = await db.get(Observation, outcome.baseline_observation_id)
                if baseline is None:
                    # Referenced baseline gone → honest insufficient_data, no token.
                    closed = await close_measurement(db, outcome=outcome, token=None, now=now)
                else:
                    token = await _resolve_close_token(db, outcome.decision_id, baseline)
                    if token is None:
                        # Credential gap may be temporary → leave still_open.
                        summary.skipped += 1
                        continue
                    closed = await close_measurement(db, outcome=outcome, token=token, now=now)

            # Capture the label BEFORE the post-close hooks: a hook rollback can
            # expire `closed`, and a later attribute access would lazy-load.
            label = closed.outcome_label
            await db.commit()
            _tally(summary, label)
            # Memory OS: record the terminal outcome. Best-effort, in its OWN
            # transaction AFTER the close committed — a memory failure can never
            # break or roll back the measurement close.
            await _record_memory_safe(db, decision_id, label)
            # Learning OS L1: a refuted decision is not a dead end — create the
            # next alternative. Best-effort, isolated; never breaks the close.
            await _refuted_followup_safe(db, decision_id, label)
        except Exception:
            await db.rollback()
            summary.errors += 1
            log.exception("close_due_measurements failed for decision %s", decision_id)

    return summary


# Terminal outcome_label → DecisionMemory.outcome string. still_open is absent →
# never written. insufficient_data is normalized to "insufficient".
_MEMORY_OUTCOME = {
    _CONFIRMED: "confirmed",
    _REFUTED: "refuted",
    _INSUFFICIENT: "insufficient",
}


async def _record_memory_safe(db: AsyncSession, decision_id: str, outcome_label: str) -> None:
    """
    Append one DecisionMemory row for a terminal outcome. Best-effort and fully
    isolated: own commit, swallows errors, never raises, never touches the
    already-committed close. No refuted-loop, no learning — just records.
    """
    label = _MEMORY_OUTCOME.get(outcome_label)
    if label is None:
        return  # still_open / unknown → no memory
    try:
        decision = await db.get(Decision, decision_id)
        if decision is None:
            return  # missing decision → skip, close already succeeded
        # Duplicate protection: one row per (decision_id, outcome).
        dup = (
            await db.execute(
                select(DecisionMemory.id).where(
                    DecisionMemory.decision_id == decision_id,
                    DecisionMemory.outcome == label,
                )
            )
        ).first()
        if dup is not None:
            return
        outcome = await outcome_repo.get_by_decision_id(db, decision_id)
        realized = getattr(outcome, "realized_delta", None) if outcome is not None else None
        await record_decision_memory(
            db, decision=decision, outcome=label,
            effect_value=realized,                      # measured delta; null for insufficient
            estimate_value=getattr(decision, "pnl_impact", None),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        log.warning("decision_memory write failed for decision %s", decision_id, exc_info=True)


async def _refuted_followup_safe(db: AsyncSession, decision_id: str, outcome_label: str) -> None:
    """
    On REFUTED only: create the next-alternative follow-up Decision (same chain,
    next action) and append a FOLLOWUP_CREATED memory event. Best-effort, own
    transaction; never raises, never affects the already-committed close. No
    ranking, no learning — deterministic next-step only.
    """
    if outcome_label != _REFUTED:
        return
    try:
        decision = await db.get(Decision, decision_id)
        if decision is None:
            return
        followup = await create_followup_for_refuted(db, decision)
        if followup is not None:
            await record_decision_memory(db, decision=followup, outcome="followup_created")
        await db.commit()
    except Exception:
        await db.rollback()
        log.warning("refuted follow-up failed for decision %s", decision_id, exc_info=True)


async def _resolve_close_token(db: AsyncSession, decision_id: str, baseline: Observation) -> Optional[str]:
    """Server-side token for reading the realized metric. None → caller skips."""
    decision = await db.get(Decision, decision_id)
    if decision is None or decision.action_key not in _ACTION_SCOPE:
        return None
    return await _resolve_token(
        db, baseline.user_id, baseline.marketplace, _ACTION_SCOPE[decision.action_key]
    )


def _tally(summary: CloseSummary, label: str) -> None:
    if label == _CONFIRMED:
        summary.confirmed += 1
    elif label == _REFUTED:
        summary.refuted += 1
    elif label == _INSUFFICIENT:
        summary.insufficient += 1
    else:  # still_open (should not occur post-close) — count as skipped, never lost
        summary.skipped += 1
