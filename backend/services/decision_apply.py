"""
Decision apply (Slice B) — turn a Decision into an executor call.

Applies a Decision through the existing marketplace executor using EXPLICIT
caller-supplied overrides as the payload. It never infers execution parameters
from Decision's human action text — irreversible marketplace payloads are never
auto-built from prose. The caller confirms exact parameters via `overrides`.

Boundaries (this slice):
- No endpoint, no measurement hook, no validation scheduler, no attribution,
  no learning, no migration.
- Does NOT mutate Decision.status. Application is recorded as an ExecutionLog
  (with decision_id provenance, Slice A); the Decision row is left untouched.
- Does not import marketplace adapters — goes through `executor.execute`, the
  single sanctioned entry point (which owns credentials/scope/idempotency).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.marketplace import executor
from services import decision_measurement
from models.decision import Decision

log = logging.getLogger(__name__)


@dataclass
class DecisionApplyResult:
    ok: bool
    decision_id: str
    execution_log_id: str | None
    status: str
    reason: str | None = None
    decision_outcome_id: str | None = None   # opened still_open outcome (Slice C), None unless measured


async def apply_decision(
    *,
    db: AsyncSession,
    user_id: str,
    decision_id: str,
    overrides: dict[str, Any],
    mode: str = "manual_l3",
    connection_id: str | None = None,
    idempotency_key: str | None = None,
    dry_run: bool = False,
    measure: bool = False,
    entity_id: str | None = None,
    window_days: int = 7,
    token: str | None = None,
    now: datetime | None = None,
) -> DecisionApplyResult:
    """
    Apply `decision_id` through executor.execute using `overrides` as the exact
    payload. Returns a DecisionApplyResult; never raises for the normal failure
    modes below. Does NOT mutate Decision.status.

    Slice C: when `measure=True` and the apply REALLY succeeds (status=="success",
    not dry_run, not failed/rejected), open measurement — capture the baseline
    metric fact and open a still_open outcome. Opening is best-effort: any failure
    there is logged and never affects the already-successful apply. dry_run never
    opens measurement. Opening measurement does NOT close it and makes no causal
    claim. Requires caller-supplied `entity_id` + `token` (explicit, no credential
    hack); marketplace is taken from the executor result.
    """
    # 1) load decision (scoped to user)
    decision = (
        await db.execute(
            select(Decision).where(Decision.id == decision_id, Decision.user_id == user_id)
        )
    ).scalar_one_or_none()
    if decision is None:
        return DecisionApplyResult(False, decision_id, None, "not_applied", "decision_not_found")

    # 2) require an action_key
    action_key = getattr(decision, "action_key", None)
    if not action_key:
        return DecisionApplyResult(False, decision_id, None, "not_applied", "missing_action_key")

    # 3) require explicit caller overrides (never build payload from text)
    if not overrides:
        return DecisionApplyResult(False, decision_id, None, "not_applied", "missing_overrides")

    # 4) payload = overrides, exactly (no normalization from Decision text)
    payload = dict(overrides)

    # 5) apply through the single executor entry point
    res = await executor.execute(
        db=db,
        user_id=user_id,
        action_type=action_key,
        payload=payload,
        mode=mode,
        connection_id=connection_id,
        insight_key=None,
        decision_id=decision.id,
        idempotency_key=idempotency_key or f"decision:{decision.id}",
        dry_run=dry_run,
    )

    # 6/7) map executor result honestly; do not mutate Decision.status
    reason = None if res.ok else ((res.error or {}).get("code") or res.status)

    # Slice C: open measurement only after a REAL successful apply.
    decision_outcome_id = None
    if measure and res.status == "success" and entity_id and token:
        try:
            outcome = await decision_measurement.open_measurement(
                db, decision=decision, entity_id=entity_id, marketplace=res.marketplace,
                window_days=window_days, token=token, now=now,
            )
            decision_outcome_id = outcome.id if outcome is not None else None
        except Exception:  # best-effort: never break a successful apply
            log.exception("open_measurement failed after apply: decision=%s", decision.id)

    return DecisionApplyResult(
        ok=res.ok,
        decision_id=decision_id,
        execution_log_id=res.log_id,
        status=res.status,
        reason=reason,
        decision_outcome_id=decision_outcome_id,
    )
