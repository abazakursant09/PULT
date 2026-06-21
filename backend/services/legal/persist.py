"""
Legal Audit persistence (Legal A5 + A6).

Pipeline:
  LegalSnapshot
   → evaluate_snapshot()                      (A4, pure)
   → create legal_audit (status=completed, append-only)
   → create legal_rule_evaluation for ALL 6 requirements (detected / not_detected /
     not_evaluated; reason + evidence persisted)
   → for each DETECTED: create an immutable legal_finding
   → reconcile_signals() (A6) — lifecycle the single live legal_signal per
     insight_key (create / update / reopen / resolve / unchanged)
   → return LegalPersistResult

A legal_signal is ADVISORY — never a legal conclusion, never compliance=true,
never a guarantee. expected_effect is QUALITATIVE risk-reduction only (no rubles,
no forecast, no money). not_detected / not_evaluated persist to the ledger only —
never a finding. Detection layer (audit / finding / ledger) is append-only; the
signal is the lifecycle entity. Flush-only — the caller owns the transaction.

No API, no UI, no AI.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.legal_audit import LegalAudit
from models.legal_finding import LegalFinding
from models.legal_rule_evaluation import LegalRuleEvaluation

from .snapshot import LegalSnapshot, LegalDataUnavailable
from .internal_source import build_snapshot_from_internal
from .rule_engine import evaluate_snapshot, LegalResult, RULE_CATALOG_VERSION
from .signal_builder import CATEGORY, EFFECT_KIND
from .reconciliation import reconcile_signals, LegalReconciliationResult

_SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class LegalPersistResult:
    audit_id: str
    total_findings: int
    total_not_detected: int
    total_not_evaluated: int
    top_severity: Optional[str]
    rule_evaluation_count: int
    finding_ids: List[str] = field(default_factory=list)
    reconciliation: Optional[LegalReconciliationResult] = None
    # A5-compatible signal counters (sourced from reconciliation)
    signals_created: int = 0
    signals_updated: int = 0
    signals_unchanged: int = 0


def snapshot_hash(s: LegalSnapshot) -> str:
    parts = [s.seller_id, s.marketplace, s.subject_type, s.subject_ref, s.sku,
             s.content_text, s.status, "".join(sorted(s.available_inputs))]
    canon = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _top_severity(sevs) -> Optional[str]:
    return max(sevs, key=lambda s: _SEV_ORDER.get(s, 0)) if sevs else None


async def persist_audit(
    db: AsyncSession, *, seller_id: str, snapshot: LegalSnapshot, evaluations,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> LegalPersistResult:
    """Persist a completed legal audit + full ledger + findings, then reconcile signals."""
    ts = now or datetime.utcnow()
    detected = [r for r in evaluations if r.result == LegalResult.DETECTED]
    not_detected = [r for r in evaluations if r.result == LegalResult.NOT_DETECTED]
    not_eval = [r for r in evaluations if r.result == LegalResult.NOT_EVALUATED]

    audit = LegalAudit(
        user_id=seller_id, listing_id=snapshot.listing_id, marketplace=snapshot.marketplace,
        sku=snapshot.sku, subject_type=snapshot.subject_type, subject_ref=snapshot.subject_ref,
        source=snapshot.source, status="completed", rule_catalog_version=RULE_CATALOG_VERSION,
        snapshot_hash=snapshot_hash(snapshot), total_findings=len(detected),
        total_not_evaluated=len(not_eval),
        top_severity=_top_severity([r.severity for r in detected]),
        triggered_by=triggered_by, created_at=ts, completed_at=ts,
    )
    db.add(audit)
    await db.flush()

    # full coverage ledger — every requirement outcome persisted (append-only)
    for r in evaluations:
        db.add(LegalRuleEvaluation(
            audit_id=audit.id, user_id=seller_id, listing_id=snapshot.listing_id,
            requirement_type=r.requirement_type, result=r.result.value, reason=r.reason,
            evidence=json.dumps(dict(r.evidence)) if r.evidence else None, created_at=ts,
        ))

    result = LegalPersistResult(
        audit_id=audit.id, total_findings=len(detected), total_not_detected=len(not_detected),
        total_not_evaluated=len(not_eval), top_severity=audit.top_severity,
        rule_evaluation_count=len(evaluations),
    )

    # detected → immutable finding (append-only); collect finding ids by requirement
    finding_id_by_requirement: dict = {}
    for r in detected:
        finding = LegalFinding(
            audit_id=audit.id, user_id=seller_id, listing_id=snapshot.listing_id,
            marketplace=snapshot.marketplace, sku=snapshot.sku,
            subject_type=snapshot.subject_type, subject_ref=snapshot.subject_ref,
            requirement_type=r.requirement_type, category=CATEGORY[r.requirement_type],
            severity=r.severity, risk_level=r.risk_band,
            estimated_effect_type=EFFECT_KIND[r.requirement_type], detectability="listing",
            evidence=json.dumps(dict(r.evidence)), created_at=ts,
        )
        db.add(finding)
        await db.flush()
        result.finding_ids.append(finding.id)
        finding_id_by_requirement[r.requirement_type] = finding.id

    # A6: reconcile lifecycle — one live signal per insight_key. Detected → create/
    # update/reopen; not_detected → resolve live; not_evaluated → leave untouched.
    rec = await reconcile_signals(
        db, seller_id=seller_id, snapshot=snapshot, audit_id=audit.id,
        evaluations=evaluations, finding_id_by_requirement=finding_id_by_requirement, now=ts,
    )
    result.reconciliation = rec
    result.signals_created = rec.created
    result.signals_updated = rec.updated
    result.signals_unchanged = rec.unchanged

    await db.flush()
    return result


async def audit_and_persist(
    db: AsyncSession, *, seller_id: str, marketplace: str, subject_type: Optional[str] = None,
    subject_ref: Optional[str] = None, sku: Optional[str] = None, listing_id: Optional[str] = None,
    triggered_by: str = "manual", now: Optional[datetime] = None,
):
    """Build snapshot (A3) → evaluate (A4) → persist + reconcile (A5/A6). Flush-only.

    Returns LegalPersistResult, or LegalDataUnavailable when no snapshot can be built
    (honest degradation — never a fake audit)."""
    snap = await build_snapshot_from_internal(
        db, seller_id=seller_id, marketplace=marketplace, subject_type=subject_type,
        subject_ref=subject_ref, sku=sku, listing_id=listing_id, now=now)
    if isinstance(snap, LegalDataUnavailable):
        return snap
    evaluations = evaluate_snapshot(snap)
    return await persist_audit(db, seller_id=seller_id, snapshot=snap, evaluations=evaluations,
                               triggered_by=triggered_by, now=now)
