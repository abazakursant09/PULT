"""
Advertising Audit persistence (A5) — wire the pure Rule Engine to A2 storage.

Pipeline:
  AdvertisingSnapshot
   → evaluate_snapshot()                       (A4, pure)
   → create advertising_audit (status=completed)
   → create advertising_rule_evaluation for EVERY rule (all three outcomes
     persisted — honesty: absence is never inferred)
   → for each TRIGGERED: create advertising_problem + a deterministic
     advertising_signal (A5 builder)
   → return AdvertisingAuditPersistResult

No API, no Decision bridge, no measurement, no content-write, no AI, no lifecycle
reconciliation (A6). Flush-only — the caller owns the transaction. No public
score is computed. Signals are created directly (one per triggered problem);
dedup/reconciliation lands in A6.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.advertising_audit import AdvertisingAudit
from models.advertising_problem import AdvertisingProblem
from models.advertising_rule_evaluation import AdvertisingRuleEvaluation
from models.advertising_signal import AdvertisingSignal

from .snapshot import AdvertisingSnapshot
from .engine import evaluate_snapshot
from .evaluation import RuleResult
from .rules import RULE_CATALOG_VERSION
from .signal_builder import build_signal

_SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class AdvertisingAuditPersistResult:
    audit_id: str
    total_problems: int
    total_not_evaluated: int
    top_severity: Optional[str]
    rule_evaluation_count: int
    problem_ids: List[str] = field(default_factory=list)
    signal_ids: List[str] = field(default_factory=list)


def snapshot_hash(s: AdvertisingSnapshot) -> str:
    """Deterministic content hash of an advertising snapshot."""
    t = s.thresholds
    t_repr = "no_thresholds" if t is None else repr(
        (t.max_drr, t.min_revenue_for_signal, t.min_ad_spend_for_signal,
         t.low_margin_threshold, t.low_stock_units, t.oos_risk_days))
    parts = [
        s.listing_id, s.marketplace, s.sku, s.revenue, s.net_profit, s.ad_spend,
        s.units_sold, s.margin, s.drr, s.stock_units, s.days_to_oos,
        s.active_seo_problems, s.critical_seo_problems, t_repr,
    ]
    canon = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _evidence_hash(evidence) -> str:
    return hashlib.sha256(
        json.dumps(evidence or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _top_severity(sevs) -> Optional[str]:
    return max(sevs, key=lambda s: _SEV_ORDER.get(s, 0)) if sevs else None


async def persist_audit(
    db: AsyncSession, *, user_id: str, snapshot: AdvertisingSnapshot, evaluations,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> AdvertisingAuditPersistResult:
    """Persist a completed advertising audit + ledger + problems + signals. Flush-only."""
    ts = now or datetime.utcnow()
    triggered = [e for e in evaluations if e.result == RuleResult.TRIGGERED]
    not_eval = [e for e in evaluations if e.result == RuleResult.NOT_EVALUATED]

    audit = AdvertisingAudit(
        user_id=user_id, listing_id=snapshot.listing_id, marketplace=snapshot.marketplace,
        sku=snapshot.sku, source=snapshot.source, status="completed",
        rule_catalog_version=RULE_CATALOG_VERSION, snapshot_hash=snapshot_hash(snapshot),
        total_problems=len(triggered), total_not_evaluated=len(not_eval),
        top_severity=_top_severity([e.severity for e in triggered]),
        triggered_by=triggered_by, created_at=ts, completed_at=ts,
    )
    db.add(audit)
    await db.flush()

    # full coverage ledger: every rule outcome recorded
    for e in evaluations:
        db.add(AdvertisingRuleEvaluation(
            audit_id=audit.id, user_id=user_id, listing_id=snapshot.listing_id,
            problem_type=e.problem_type, result=e.result.value, reason=e.reason,
            evidence=json.dumps(e.evidence) if e.evidence else None, created_at=ts,
        ))

    result = AdvertisingAuditPersistResult(
        audit_id=audit.id, total_problems=len(triggered), total_not_evaluated=len(not_eval),
        top_severity=audit.top_severity, rule_evaluation_count=len(evaluations),
    )

    for e in triggered:
        prob = AdvertisingProblem(
            audit_id=audit.id, user_id=user_id, listing_id=snapshot.listing_id,
            marketplace=snapshot.marketplace, sku=snapshot.sku, problem_type=e.problem_type,
            category=e.category, severity=e.severity, estimated_effect_type=e.estimated_effect_type,
            detectability=e.detectability, evidence=json.dumps(e.evidence), created_at=ts,
        )
        db.add(prob)
        await db.flush()
        result.problem_ids.append(prob.id)

        d = build_signal(e, marketplace=snapshot.marketplace, sku=snapshot.sku)
        sig = AdvertisingSignal(
            audit_id=audit.id, problem_id=prob.id, user_id=user_id, listing_id=snapshot.listing_id,
            marketplace=snapshot.marketplace, sku=snapshot.sku, signal_key=d.signal_key,
            insight_key=d.insight_key, problem_type=d.problem_type,
            recommended_action_key=d.recommended_action_key,
            alternative_action_keys=json.dumps(list(d.alternative_action_keys)),
            what=d.what, why=d.why, meaning=d.meaning, what_to_do=d.what_to_do,
            expected_effect=d.expected_effect, priority_level=d.priority_level,
            expected_effect_type=d.expected_effect_type, effect_band=d.effect_band,
            confidence=d.confidence, status="active", evidence_hash=_evidence_hash(e.evidence),
            created_at=ts, updated_at=ts,
        )
        db.add(sig)
        await db.flush()
        result.signal_ids.append(sig.id)

    await db.flush()
    return result


async def audit_and_persist(
    db: AsyncSession, *, user_id: str, snapshot: AdvertisingSnapshot,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> AdvertisingAuditPersistResult:
    """Convenience: evaluate the snapshot (A4) then persist (A5). Flush-only."""
    evaluations = evaluate_snapshot(snapshot)
    return await persist_audit(db, user_id=user_id, snapshot=snapshot, evaluations=evaluations,
                               triggered_by=triggered_by, now=now)
