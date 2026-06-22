"""
Confirm + Real Apply (Decision Apply UX A3) — apply a bound decision ONLY after the
seller has explicitly confirmed it.

No auto-execution, no background apply, no guard/capability bypass, no direct
marketplace call. It previews first (the single authoritative gate), records the
seller's intent (confirmed/rejected) in the append-only ledger, and — only when the
preview says applyable — hands off to the EXISTING execution bridge with
dry_run=False. After a real successful apply it opens measurement through the
existing Decision Outcome path (best-effort: a measurement failure never turns a
successful apply into a failure). It never mutates Feed state or source signals,
never opens measurement before a successful apply, and exposes no API/UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.engine_effect_observation import EngineEffectObservation

from services.action_binding.execution_bridge import execute_bound_decision
from services.decision_outcome.effect_measurement import open_effect_measurement
from .preview import build_apply_preview, record_apply_intent


@dataclass
class ApplyConfirmResult:
    ok: bool
    decision_id: str
    action_key: Optional[str]
    execution_log_id: Optional[str]
    status: str
    reason: Optional[str]
    measurement_opened: bool
    intent_id: Optional[str]


async def _open_measurement_if_needed(db, user_id: str, decision_id: str, now) -> bool:
    """Open measurement for this decision's link via the existing DO path — only if
    not already open. Best-effort: any failure → False, never raises."""
    try:
        link = (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.decision_id == decision_id,
            EngineSignalDecisionLink.user_id == user_id))).scalars().first()
        if link is None:
            return False
        existing = (await db.execute(select(EngineEffectObservation).where(
            EngineEffectObservation.link_id == link.id))).scalars().first()
        if existing is not None:
            return True   # already open — never reopen
        await open_effect_measurement(db, user_id=user_id, now=now)
        obs = (await db.execute(select(EngineEffectObservation).where(
            EngineEffectObservation.link_id == link.id))).scalars().first()
        return obs is not None
    except Exception:
        return False


async def confirm_and_apply_decision(
    db: AsyncSession, *, user_id: str, decision_id: str, marketplace: str,
    sku: Optional[str], idempotency_key: str, now: Optional[datetime] = None,
) -> ApplyConfirmResult:
    """Apply a bound decision after explicit confirmation. idempotency_key required."""
    if not idempotency_key:
        return ApplyConfirmResult(False, decision_id, None, None, "not_applied",
                                  "idempotency_key_required", False, None)

    # 1) preview = the single authoritative gate (dry_run, no apply)
    preview = await build_apply_preview(
        db, user_id=user_id, decision_id=decision_id, marketplace=marketplace, sku=sku, now=now)

    # 2) not applyable → record a rejected intent, never call real apply
    if not preview.applyable:
        intent = await record_apply_intent(
            db, user_id=user_id, decision_id=decision_id, action_key=preview.action_key,
            intent_status="rejected", dry_run_status=preview.dry_run_status,
            reason=preview.reason, marketplace=marketplace, now=now)
        await db.commit()
        return ApplyConfirmResult(False, decision_id, preview.action_key, None,
                                  "not_applied", preview.reason, False, intent.id)

    # 3) applyable → the seller confirms; persist the confirmation before applying
    intent = await record_apply_intent(
        db, user_id=user_id, decision_id=decision_id, action_key=preview.action_key,
        intent_status="confirmed", dry_run_status=preview.dry_run_status, reason=None,
        marketplace=marketplace, now=now)
    await db.commit()

    # 4) real apply through the EXISTING bridge/apply path (same idempotency_key)
    res = await execute_bound_decision(
        db, user_id=user_id, decision_id=decision_id, marketplace=marketplace,
        sku=sku, dry_run=False, idempotency_key=idempotency_key, now=now)

    # 5/6) open measurement only after a real successful apply (best-effort)
    measurement_opened = False
    if res.ok:
        measurement_opened = await _open_measurement_if_needed(db, user_id, decision_id, now)

    await db.commit()
    return ApplyConfirmResult(
        ok=res.ok, decision_id=decision_id, action_key=res.action_key,
        execution_log_id=res.execution_log_id, status=res.status, reason=res.reason,
        measurement_opened=measurement_opened, intent_id=intent.id)
