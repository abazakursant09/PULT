"""
Legal Signal Reconciliation (Legal A6) — give legal signals memory + lifecycle.

After a new audit run, reconcile the current evaluations against existing signals
for the same seller, keyed by insight_key (legal_<requirement_type>:<mp>:<sku>).
One live signal per insight_key — a duplicate live signal is structurally
impossible.

Lifecycle: active | acknowledged | dismissed | promoted_to_decision | resolved |
reopened.

Honesty rules:
  * A signal is RESOLVED only when its requirement ran and is NOT_DETECTED — and
    the recorded reason is cautious ("risk_not_detected_in_latest_audit"), NEVER
    "compliant".
  * A NOT_EVALUATED requirement NEVER resolves and NEVER reopens — missing inputs
    do not change the lifecycle.
  * `dismissed` is a USER/action state — reconciliation never auto-dismisses; it
    only reopens a dismissed signal when the risk is detected again.

Pure DB reconciliation: no API, no AI, no forecast, no money, no legal conclusion,
no score. Flush-only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.legal_signal import LegalSignal
from .snapshot import LegalSnapshot
from .rule_engine import LegalResult
from .signal_builder import signal_fields, insight_key

ACTIVE = "active"
ACKNOWLEDGED = "acknowledged"
DISMISSED = "dismissed"
PROMOTED = "promoted_to_decision"
RESOLVED = "resolved"
REOPENED = "reopened"
_LIVE = {ACTIVE, ACKNOWLEDGED, REOPENED}

_RESOLVED_REASON = "risk_not_detected_in_latest_audit"
_REOPENED_REASON = "risk_detected_again_in_latest_audit"
_DETECTED_REASON = "risk_detected_in_latest_audit"


@dataclass
class LegalReconciliationResult:
    created: int = 0
    updated: int = 0
    reopened: int = 0
    resolved: int = 0
    unchanged: int = 0


def evidence_hash(evidence: Optional[Mapping[str, object]]) -> str:
    return hashlib.sha256(
        json.dumps(evidence or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _apply_fields(sig: LegalSignal, sf: dict, *, audit_id, finding_id, evh, now, status, reason):
    for k, v in sf.items():
        setattr(sig, k, v)
    sig.audit_id = audit_id
    sig.finding_id = finding_id
    sig.evidence_hash = evh
    sig.status = status
    sig.lifecycle_reason = reason
    sig.updated_at = now


async def reconcile_signals(
    db: AsyncSession, *, seller_id: str, snapshot: LegalSnapshot, audit_id: str,
    evaluations, finding_id_by_requirement: Mapping[str, str], now: datetime,
) -> LegalReconciliationResult:
    """Reconcile this audit's outcomes against existing signals by insight_key."""
    rows = (await db.execute(select(LegalSignal).where(
        LegalSignal.user_id == seller_id))).scalars().all()
    # one row per insight_key (A5/A6 keep it deduped); prefer the latest if ever many
    by_key: dict = {}
    for r in rows:
        cur = by_key.get(r.insight_key)
        if cur is None or (r.updated_at or r.created_at) >= (cur.updated_at or cur.created_at):
            by_key[r.insight_key] = r

    res = LegalReconciliationResult()

    for ev in evaluations:
        ikey = insight_key(snapshot, ev.requirement_type)
        sig = by_key.get(ikey)

        if ev.result == LegalResult.DETECTED:
            evh = evidence_hash(dict(ev.evidence))
            sf = signal_fields(snapshot, ev)
            fid = finding_id_by_requirement.get(ev.requirement_type)
            if sig is None:
                new = LegalSignal(
                    audit_id=audit_id, finding_id=fid, user_id=seller_id,
                    listing_id=snapshot.listing_id, marketplace=snapshot.marketplace,
                    sku=snapshot.sku, subject_type=snapshot.subject_type,
                    subject_ref=snapshot.subject_ref, status=ACTIVE,
                    lifecycle_reason=_DETECTED_REASON, evidence_hash=evh,
                    created_at=now, updated_at=now, **sf,
                )
                db.add(new); by_key[ikey] = new; res.created += 1
            elif sig.status in (RESOLVED, DISMISSED):
                _apply_fields(sig, sf, audit_id=audit_id, finding_id=fid, evh=evh,
                              now=now, status=REOPENED, reason=_REOPENED_REASON)
                res.reopened += 1
            elif sig.status in _LIVE:
                if sig.evidence_hash != evh:
                    _apply_fields(sig, sf, audit_id=audit_id, finding_id=fid, evh=evh,
                                  now=now, status=sig.status, reason=sig.lifecycle_reason)
                    res.updated += 1
                else:
                    res.unchanged += 1
            else:  # promoted_to_decision — Decision owns its life
                res.unchanged += 1

        elif ev.result == LegalResult.NOT_DETECTED:
            if sig is not None and sig.status in _LIVE:
                sig.status = RESOLVED
                sig.lifecycle_reason = _RESOLVED_REASON   # cautious, never "compliant"
                sig.audit_id = audit_id
                sig.updated_at = now
                res.resolved += 1
            elif sig is not None:
                res.unchanged += 1

        else:  # NOT_EVALUATED — never resolve, never reopen, keep current status
            if sig is not None:
                res.unchanged += 1

    await db.flush()
    return res
