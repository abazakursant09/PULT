"""
Apply Preview (Decision Apply UX A2) — read-only eligibility + dry-run preview.

Answers, for one Decision: can it be applied, with which action_key and payload,
does the marketplace support it, and why not. It does this by delegating to the
EXISTING execution bridge in dry_run mode — so the gate decisions (binding,
capability, payload, guard) come from the single authoritative path, never a
re-implementation. It NEVER applies, NEVER writes to the executor, NEVER calls a
marketplace, NEVER opens measurement, NEVER creates an effect observation, NEVER
touches a source signal.

record_apply_intent appends a decision_apply_intent row (previewed/confirmed/
rejected) for audit — a pure ledger write, still no execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.decision_apply_intent import DecisionApplyIntent

from services.action_binding.registry import BY_SIGNAL_TYPE
from services.action_binding.execution_bridge import execute_bound_decision, NOT_EXECUTED

PAYLOAD_OK = "ok"
PAYLOAD_NOT_DERIVABLE = "payload_not_derivable"


@dataclass
class ApplyPreview:
    applyable: bool
    decision_id: str
    action_key: Optional[str]
    payload: Optional[Mapping[str, object]]
    capability_ok: bool
    payload_status: Optional[str]
    safety_class: Optional[str]
    dry_run_status: Optional[str]
    reason: Optional[str]
    marketplace: Optional[str]
    sku: Optional[str]


async def _safety_class_for(db, user_id: str, decision_id: str) -> Optional[str]:
    link = (await db.execute(select(EngineSignalDecisionLink).where(
        EngineSignalDecisionLink.decision_id == decision_id,
        EngineSignalDecisionLink.user_id == user_id))).scalars().first()
    if link is None or not link.insight_key:
        return None
    b = BY_SIGNAL_TYPE.get(link.insight_key.split(":", 1)[0])
    return b.safety_class if b else None


async def build_apply_preview(
    db: AsyncSession, *, user_id: str, decision_id: str, marketplace: str,
    sku: Optional[str], now: Optional[datetime] = None,
) -> ApplyPreview:
    """Read-only eligibility + dry-run preview for applying a Decision. No apply."""
    safety_class = await _safety_class_for(db, user_id, decision_id)

    # single authoritative gate: the existing bridge in dry_run mode (no apply)
    res = await execute_bound_decision(
        db, user_id=user_id, decision_id=decision_id, marketplace=marketplace,
        sku=sku, dry_run=True, now=now)

    if res.status == NOT_EXECUTED:
        # blocked before the executor — map the bridge reason to preview fields
        capability_ok = res.reason != "unsupported_capability"
        payload_status = PAYLOAD_NOT_DERIVABLE if res.reason == PAYLOAD_NOT_DERIVABLE else None
        return ApplyPreview(
            applyable=False, decision_id=decision_id, action_key=res.action_key,
            payload=None, capability_ok=capability_ok, payload_status=payload_status,
            safety_class=safety_class, dry_run_status=None, reason=res.reason,
            marketplace=marketplace, sku=sku)

    # reached the executor in dry_run — every gate up to here passed
    return ApplyPreview(
        applyable=res.ok, decision_id=decision_id, action_key=res.action_key,
        payload=res.payload, capability_ok=True, payload_status=PAYLOAD_OK,
        safety_class=safety_class, dry_run_status=res.status,
        reason=None if res.ok else res.status, marketplace=marketplace, sku=sku)


async def record_apply_intent(
    db: AsyncSession, *, user_id: str, decision_id: str, action_key: Optional[str],
    intent_status: str, dry_run_status: Optional[str] = None, reason: Optional[str] = None,
    marketplace: Optional[str] = None, now: Optional[datetime] = None,
) -> DecisionApplyIntent:
    """Append an apply-intent row (previewed|confirmed|rejected). Append-only,
    flush-only — never applies, never executes."""
    row = DecisionApplyIntent(
        user_id=user_id, decision_id=decision_id, action_key=action_key,
        intent_status=intent_status, dry_run_status=dry_run_status, reason=reason,
        marketplace=marketplace, created_at=now or datetime.utcnow())
    db.add(row)
    await db.flush()
    return row
