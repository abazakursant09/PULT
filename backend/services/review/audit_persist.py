"""
Review Audit persistence (A5) — wire the pure Rule Engine to A2 storage.

Pipeline:
  ReviewSnapshot
   → evaluate_snapshot()                    (A4, pure)
   → create review_audit (status=completed)
   → create review_rule_evaluation for EVERY rule (all three outcomes persisted)
   → for each TRIGGERED: create review_problem + a deterministic review_signal
     (A5 builder, carrying safety_category/safety_mode/review_id)
   → return ReviewAuditPersistResult

No API, no Decision bridge, no measurement, no content-write, no AI, no reply
generation, no autoresponder, no lifecycle reconciliation (A6). Flush-only — the
caller owns the transaction. No public score. Signals created directly (one per
triggered problem); dedup/reconciliation lands in A6.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.review_audit import ReviewAudit
from models.review_problem import ReviewProblem
from models.review_rule_evaluation import ReviewRuleEvaluation

from .snapshot import ReviewSnapshot
from .engine import evaluate_snapshot
from .evaluation import RuleResult
from .rules import RULE_CATALOG_VERSION
from .reconciliation import reconcile_signals, ReconcileResult

_SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class ReviewAuditPersistResult:
    audit_id: str
    total_problems: int
    total_not_evaluated: int
    top_severity: Optional[str]
    rule_evaluation_count: int
    problem_ids: List[str] = field(default_factory=list)
    reconciliation: Optional[ReconcileResult] = None


def snapshot_hash(s: ReviewSnapshot) -> str:
    """Deterministic content hash of a review snapshot."""
    parts = [
        s.review_id, s.marketplace, s.sku, s.rating, s.text, s.has_text,
        s.answered, s.answer_text, s.safety_category, s.default_mode,
    ]
    canon = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _top_severity(sevs) -> Optional[str]:
    return max(sevs, key=lambda s: _SEV_ORDER.get(s, 0)) if sevs else None


async def persist_audit(
    db: AsyncSession, *, user_id: str, snapshot: ReviewSnapshot, evaluations,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> ReviewAuditPersistResult:
    """Persist a completed review audit + ledger + problems + signals. Flush-only."""
    ts = now or datetime.utcnow()
    triggered = [e for e in evaluations if e.result == RuleResult.TRIGGERED]
    not_eval = [e for e in evaluations if e.result == RuleResult.NOT_EVALUATED]

    audit = ReviewAudit(
        user_id=user_id, listing_id=snapshot.listing_id, marketplace=snapshot.marketplace,
        sku=snapshot.sku, source=snapshot.source, status="completed",
        rule_catalog_version=RULE_CATALOG_VERSION, snapshot_hash=snapshot_hash(snapshot),
        total_problems=len(triggered), total_not_evaluated=len(not_eval),
        top_severity=_top_severity([e.severity for e in triggered]),
        triggered_by=triggered_by, created_at=ts, completed_at=ts,
    )
    db.add(audit)
    await db.flush()

    for e in evaluations:
        db.add(ReviewRuleEvaluation(
            audit_id=audit.id, user_id=user_id, listing_id=snapshot.listing_id,
            problem_type=e.problem_type, result=e.result.value, reason=e.reason,
            evidence=json.dumps(e.evidence) if e.evidence else None, created_at=ts,
        ))

    result = ReviewAuditPersistResult(
        audit_id=audit.id, total_problems=len(triggered), total_not_evaluated=len(not_eval),
        top_severity=audit.top_severity, rule_evaluation_count=len(evaluations),
    )

    # append-only detection records for every triggered problem
    problem_id_by_type: dict = {}
    for e in triggered:
        prob = ReviewProblem(
            audit_id=audit.id, user_id=user_id, listing_id=snapshot.listing_id,
            review_id=snapshot.review_id, marketplace=snapshot.marketplace, sku=snapshot.sku,
            problem_type=e.problem_type, category=e.category, severity=e.severity,
            estimated_effect_type=e.estimated_effect_type, detectability=e.detectability,
            evidence=json.dumps(e.evidence), created_at=ts,
        )
        db.add(prob)
        await db.flush()
        result.problem_ids.append(prob.id)
        problem_id_by_type[e.problem_type] = prob.id

    # A6: reconcile signals by insight_key (rev_<type>:<mp>:<sku>:<review_id>)
    # instead of blindly creating a new signal per audit. One live signal per key.
    result.reconciliation = await reconcile_signals(
        db, user_id=user_id, listing_id=snapshot.listing_id, audit_id=audit.id,
        marketplace=snapshot.marketplace, sku=snapshot.sku, review_id=snapshot.review_id,
        evaluations=evaluations, problem_id_by_type=problem_id_by_type, now=ts,
    )

    await db.flush()
    return result


async def audit_and_persist(
    db: AsyncSession, *, user_id: str, snapshot: ReviewSnapshot,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> ReviewAuditPersistResult:
    """Convenience: evaluate the snapshot (A4) then persist (A5). Flush-only."""
    evaluations = evaluate_snapshot(snapshot)
    return await persist_audit(db, user_id=user_id, snapshot=snapshot, evaluations=evaluations,
                               triggered_by=triggered_by, now=now)
