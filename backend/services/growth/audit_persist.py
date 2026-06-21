"""
Growth Audit persistence (A5) — wire the pure Rule Engine to A2 storage.

Pipeline:
  GrowthSnapshot
   → evaluate_snapshot()                     (A4, pure)
   → create growth_audit (status=completed)
   → create growth_rule_evaluation for EVERY rule (all three outcomes persisted;
     reason for not_evaluated, evidence for triggered)
   → for each TRIGGERED: create growth_problem + a deterministic growth_signal
     (A5 builder, status=active, evidence_hash)
   → return GrowthAuditPersistResult

No API, no Decision bridge, no measurement, no AI, no forecast, no growth score.
A6: signals are no longer created directly — they are routed through
reconcile_signals (one live signal per user/listing/insight_key). Flush-only —
the caller owns the transaction.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.growth_audit import GrowthAudit
from models.growth_problem import GrowthProblem
from models.growth_rule_evaluation import GrowthRuleEvaluation

from .snapshot import GrowthSnapshot
from .engine import evaluate_snapshot
from .evaluation import RuleResult
from .rules import RULE_CATALOG_VERSION, GrowthThresholds
from .reconciliation import reconcile_signals, ReconcileResult

_SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class GrowthAuditPersistResult:
    audit_id: str
    total_problems: int
    total_not_evaluated: int
    top_severity: Optional[str]
    rule_evaluation_count: int
    problem_ids: List[str] = field(default_factory=list)
    reconciliation: Optional[ReconcileResult] = None


def snapshot_hash(s: GrowthSnapshot) -> str:
    """Deterministic content hash of a growth snapshot."""
    parts = [
        s.marketplace, s.sku, s.revenue, s.net_profit, s.margin, s.margin_band,
        s.ad_spend, s.drr, s.units_sold, s.active_seo_signals, s.active_review_signals,
        s.risk_review_signals, s.stock_units,
    ]
    canon = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _top_severity(sevs) -> Optional[str]:
    return max(sevs, key=lambda s: _SEV_ORDER.get(s, 0)) if sevs else None


async def persist_audit(
    db: AsyncSession, *, user_id: str, snapshot: GrowthSnapshot, evaluations,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> GrowthAuditPersistResult:
    """Persist a completed growth audit + ledger + problems + signals. Flush-only."""
    ts = now or datetime.utcnow()
    triggered = [e for e in evaluations if e.result == RuleResult.TRIGGERED]
    not_eval = [e for e in evaluations if e.result == RuleResult.NOT_EVALUATED]

    audit = GrowthAudit(
        user_id=user_id, listing_id=snapshot.listing_id, marketplace=snapshot.marketplace,
        sku=snapshot.sku, source=snapshot.source, status="completed",
        rule_catalog_version=RULE_CATALOG_VERSION, snapshot_hash=snapshot_hash(snapshot),
        total_problems=len(triggered), total_not_evaluated=len(not_eval),
        top_severity=_top_severity([e.severity for e in triggered]),
        triggered_by=triggered_by, created_at=ts, completed_at=ts,
    )
    db.add(audit)
    await db.flush()

    # full coverage ledger: every rule outcome persisted
    for e in evaluations:
        db.add(GrowthRuleEvaluation(
            audit_id=audit.id, user_id=user_id, listing_id=snapshot.listing_id,
            problem_type=e.problem_type, result=e.result.value, reason=e.reason,
            evidence=json.dumps(e.evidence) if e.evidence else None, created_at=ts,
        ))

    result = GrowthAuditPersistResult(
        audit_id=audit.id, total_problems=len(triggered), total_not_evaluated=len(not_eval),
        top_severity=audit.top_severity, rule_evaluation_count=len(evaluations),
    )

    # append-only detection record for every triggered opportunity
    problem_id_by_type: dict = {}
    for e in triggered:
        prob = GrowthProblem(
            audit_id=audit.id, user_id=user_id, listing_id=snapshot.listing_id,
            marketplace=snapshot.marketplace, sku=snapshot.sku,
            problem_type=e.problem_type, category=e.category, severity=e.severity,
            estimated_effect_type=e.estimated_effect_type, detectability=e.detectability,
            evidence=json.dumps(e.evidence), created_at=ts,
        )
        db.add(prob)
        await db.flush()
        result.problem_ids.append(prob.id)
        problem_id_by_type[e.problem_type] = prob.id

    # A6: reconcile signals by insight_key (growth_<type>:<mp>:<sku>) instead of
    # blindly creating a new signal per audit. One live signal per key.
    result.reconciliation = await reconcile_signals(
        db, user_id=user_id, listing_id=snapshot.listing_id, audit_id=audit.id,
        marketplace=snapshot.marketplace, sku=snapshot.sku,
        evaluations=evaluations, problem_id_by_type=problem_id_by_type, now=ts,
    )

    await db.flush()
    return result


async def audit_and_persist(
    db: AsyncSession, *, user_id: str, snapshot: GrowthSnapshot, thresholds: GrowthThresholds,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> GrowthAuditPersistResult:
    """Convenience: evaluate the snapshot + thresholds (A4) then persist (A5). Flush-only."""
    evaluations = evaluate_snapshot(snapshot, thresholds)
    return await persist_audit(db, user_id=user_id, snapshot=snapshot, evaluations=evaluations,
                               triggered_by=triggered_by, now=now)
