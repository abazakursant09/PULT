"""
SEO Audit persistence (A5) — wire the pure Rule Engine to the A2 storage layer.

Pipeline:
  CardSnapshot
   → evaluate_snapshot()                  (A4, pure)
   → create seo_audit (status=completed)
   → create seo_rule_evaluation for EVERY rule (triggered/not_triggered/not_evaluated
     are ALL persisted — honesty: absence is never inferred)
   → for each TRIGGERED: create seo_problem + a deterministic seo_signal (A5 builder)
   → return AuditPersistResult

No API, no Decision bridge, no measurement, no content-write, no AI, no lifecycle
reconciliation (that is A6), no marketplace-specific code. Flush-only — the caller
owns the transaction. The public-sounding `internal_health_index` is NEVER
computed or set here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.seo_audit import SeoAudit
from models.seo_problem import SeoProblem
from models.seo_rule_evaluation import SeoRuleEvaluation

from .card_snapshot import CardSnapshot
from .engine import evaluate_snapshot
from .evaluation import RuleResult
from .rules import RULE_CATALOG_VERSION
from .reconciliation import reconcile_signals, ReconcileResult

_SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class AuditPersistResult:
    audit_id: str
    total_problems: int
    total_not_evaluated: int
    top_severity: Optional[str]
    rule_evaluation_count: int
    problem_ids: List[str] = field(default_factory=list)
    reconciliation: Optional[ReconcileResult] = None


def snapshot_hash(s: CardSnapshot) -> str:
    """Deterministic content hash of a snapshot (dedup/throttle marker)."""
    c = s.constraints
    cons_repr = "no_constraints" if c is None else repr(
        (c.title_min_len, c.title_max_len, c.description_min_len, c.media_min_images,
         c.attribute_fill_rate_threshold, c.content_completeness_threshold))
    parts = [
        s.listing_id, s.marketplace, s.sku, s.title, s.description, s.brand,
        "|".join(s.category_path), "|".join(s.expected_category_path or ()),
        repr([(a.key, a.value, a.is_filled, a.is_valid_format) for a in s.attributes]),
        "|".join(s.variants), str(s.media.image_count), str(s.media.video_present),
        cons_repr,
    ]
    canon = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _top_severity(sevs) -> Optional[str]:
    return max(sevs, key=lambda s: _SEV_ORDER.get(s, 0)) if sevs else None


async def persist_audit(
    db: AsyncSession, *, user_id: str, snapshot: CardSnapshot, evaluations,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> AuditPersistResult:
    """Persist a completed audit + ledger + problems + signals. Flush-only."""
    ts = now or datetime.utcnow()
    triggered = [e for e in evaluations if e.result == RuleResult.TRIGGERED]
    not_eval = [e for e in evaluations if e.result == RuleResult.NOT_EVALUATED]

    audit = SeoAudit(
        user_id=user_id, listing_id=snapshot.listing_id, marketplace=snapshot.marketplace,
        sku=snapshot.sku, source=snapshot.source, status="completed",
        rule_catalog_version=RULE_CATALOG_VERSION, snapshot_hash=snapshot_hash(snapshot),
        total_problems=len(triggered), total_not_evaluated=len(not_eval),
        top_severity=_top_severity([e.severity for e in triggered]),
        triggered_by=triggered_by, created_at=ts, completed_at=ts,
        # internal_health_index intentionally left NULL — not a public score.
    )
    db.add(audit)
    await db.flush()

    # full coverage ledger: every rule outcome recorded
    for e in evaluations:
        db.add(SeoRuleEvaluation(
            audit_id=audit.id, user_id=user_id, listing_id=snapshot.listing_id,
            problem_type=e.problem_type, result=e.result.value, reason=e.reason,
            evidence=json.dumps(e.evidence) if e.evidence else None, created_at=ts,
        ))

    result = AuditPersistResult(
        audit_id=audit.id, total_problems=len(triggered), total_not_evaluated=len(not_eval),
        top_severity=audit.top_severity, rule_evaluation_count=len(evaluations),
    )

    # append-only detection records for every triggered problem
    problem_id_by_type: dict = {}
    for e in triggered:
        prob = SeoProblem(
            audit_id=audit.id, user_id=user_id, listing_id=snapshot.listing_id,
            marketplace=snapshot.marketplace, sku=snapshot.sku, problem_type=e.problem_type,
            category=e.category, severity=e.severity, estimated_effect_type=e.estimated_effect_type,
            detectability=e.detectability, evidence=json.dumps(e.evidence), created_at=ts,
        )
        db.add(prob)
        await db.flush()
        result.problem_ids.append(prob.id)
        problem_id_by_type[e.problem_type] = prob.id

    # A6: reconcile signals by insight_key (create/update/resolve/reopen) instead of
    # blindly creating a new signal per audit. One live signal per insight_key.
    result.reconciliation = await reconcile_signals(
        db, user_id=user_id, listing_id=snapshot.listing_id, audit_id=audit.id,
        marketplace=snapshot.marketplace, sku=snapshot.sku, evaluations=evaluations,
        problem_id_by_type=problem_id_by_type, now=ts,
    )

    await db.flush()
    return result


async def audit_and_persist(
    db: AsyncSession, *, user_id: str, snapshot: CardSnapshot,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> AuditPersistResult:
    """Convenience: evaluate the snapshot (A4) then persist (A5). Flush-only."""
    evaluations = evaluate_snapshot(snapshot)
    return await persist_audit(db, user_id=user_id, snapshot=snapshot, evaluations=evaluations,
                               triggered_by=triggered_by, now=now)
