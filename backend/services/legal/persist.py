"""
Legal Audit persistence + Signal Builder (Legal A5).

Pipeline:
  LegalSnapshot
   → evaluate_snapshot()                      (A4, pure)
   → create legal_audit (status=completed, append-only)
   → create legal_rule_evaluation for ALL 6 requirements (detected / not_detected /
     not_evaluated; reason + evidence persisted)
   → for each DETECTED: create immutable legal_finding + a seller-facing
     legal_signal (5-part doctrine), with minimal dedup so a repeated run does not
     spawn duplicate ACTIVE signals for the same (seller, subject, insight_key)
   → return LegalPersistResult

A legal_signal is ADVISORY — never a legal conclusion, never compliance=true,
never a guarantee. expected_effect is QUALITATIVE risk-reduction only (no rubles,
no forecast, no money). not_detected / not_evaluated persist to the ledger only —
never a finding, never a signal. Detection layer (audit / finding / ledger) is
append-only; the signal is the lifecycle entity.

No API, no UI, no AI, no full reconciliation (A6) — only create-or-refresh of the
single live signal per insight_key. Flush-only — the caller owns the transaction.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.legal_audit import LegalAudit
from models.legal_finding import LegalFinding
from models.legal_rule_evaluation import LegalRuleEvaluation
from models.legal_signal import LegalSignal

from .snapshot import LegalSnapshot, LegalDataUnavailable
from .internal_source import build_snapshot_from_internal
from .rule_engine import evaluate_snapshot, LegalResult, RULE_CATALOG_VERSION

_SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_LIVE = ("active", "acknowledged", "reopened")

# requirement → legal domain category (A2 legal_finding.category)
_CATEGORY = {
    "product_certification": "certification",
    "trademark_usage": "ip",
    "labeling_requirements": "labeling",
    "marketplace_offer_terms": "content",
    "return_policy_obligations": "content",
    "content_claim_risk": "content",
}

# requirement → inherent risk kind (finding.estimated_effect_type)
_EFFECT_KIND = {
    "product_certification": "block_risk",
    "trademark_usage": "takedown_risk",
    "labeling_requirements": "fine_risk",
    "marketplace_offer_terms": "compliance_risk",
    "return_policy_obligations": "compliance_risk",
    "content_claim_risk": "takedown_risk",
}

# qualitative expected effect — NO money, NO forecast, NO guarantee
_EFFECT_TEXT = {
    "compliance_risk": "может снизить риск претензий по соответствию требованиям",
    "takedown_risk": "может снизить риск снятия или блокировки карточки по претензии",
    "fine_risk": "может снизить риск штрафных санкций",
    "block_risk": "может снизить риск блокировки карточки",
}

# advisory recommended_action text (mirrors the A4 allowlist)
_ACTION_TEXT = {
    "check_requirement": "Проверить, применимо ли это требование к данному товару.",
    "collect_document": "Собрать и проверить подтверждающие документы.",
    "verify_marketplace_terms": "Сверить условия с правилами маркетплейса.",
    "consult_lawyer": "При сомнениях обратиться к юристу или профильному специалисту.",
    "review_content_claim": "Проверить формулировки в карточке товара.",
}

# 5-part doctrine templates (what_happened / why_it_matters / meaning)
_TEMPLATES: Mapping[str, Mapping[str, str]] = {
    "product_certification": {
        "what_happened": "По товару не подтверждена сертификация/декларация.",
        "why_it_matters": "Для части категорий обязательны документы соответствия.",
        "meaning": "Возможно, требуется проверить, нужны ли документы для этой категории.",
    },
    "trademark_usage": {
        "what_happened": "Использование бренда/обозначения требует проверки прав.",
        "why_it_matters": "Использование чужого товарного знака может вызвать претензию.",
        "meaning": "Стоит проверить основания использования обозначения.",
    },
    "labeling_requirements": {
        "what_happened": "Не подтверждено соответствие требованиям маркировки/этикетки.",
        "why_it_matters": "Для ряда категорий маркировка обязательна.",
        "meaning": "Возможно, требуется проверить требования к маркировке.",
    },
    "marketplace_offer_terms": {
        "what_happened": "Не сверены условия оффера с правилами маркетплейса.",
        "why_it_matters": "Нарушение правил площадки может привести к санкциям.",
        "meaning": "Стоит сверить карточку с актуальными требованиями площадки.",
    },
    "return_policy_obligations": {
        "what_happened": "Не подтверждены обязательства по возврату.",
        "why_it_matters": "Правила возврата регулируются законом и площадкой.",
        "meaning": "Стоит проверить, соответствуют ли условия возврата требованиям.",
    },
    "content_claim_risk": {
        "what_happened": "В карточке есть формулировки, требующие проверки.",
        "why_it_matters": "Некоторые утверждения могут требовать обоснования.",
        "meaning": "Стоит проверить, подтверждены ли заявленные свойства.",
    },
}


@dataclass
class LegalPersistResult:
    audit_id: str
    total_findings: int
    total_not_detected: int
    total_not_evaluated: int
    top_severity: Optional[str]
    rule_evaluation_count: int
    finding_ids: List[str] = field(default_factory=list)
    signal_ids: List[str] = field(default_factory=list)
    signals_created: int = 0
    signals_updated: int = 0
    signals_unchanged: int = 0


def snapshot_hash(s: LegalSnapshot) -> str:
    parts = [s.seller_id, s.marketplace, s.subject_type, s.subject_ref, s.sku,
             s.content_text, s.status, "".join(sorted(s.available_inputs))]
    canon = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _evidence_hash(evidence) -> str:
    return hashlib.sha256(
        json.dumps(evidence or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _top_severity(sevs) -> Optional[str]:
    return max(sevs, key=lambda s: _SEV_ORDER.get(s, 0)) if sevs else None


def _insight_key(snap: LegalSnapshot, requirement_type: str) -> str:
    ref = snap.sku or snap.subject_ref or "unknown"
    return f"legal_{requirement_type}:{snap.marketplace or 'unknown'}:{ref}"


def _signal_fields(snap: LegalSnapshot, r) -> dict:
    """Deterministic advisory signal payload for a DETECTED requirement."""
    rt = r.requirement_type
    tpl = _TEMPLATES[rt]
    effect_kind = _EFFECT_KIND[rt]
    return {
        "signal_key": f"legal_{rt}",
        "insight_key": _insight_key(snap, rt),
        "requirement_type": rt,
        "category": _CATEGORY[rt],
        "recommended_action_key": r.recommended_action,
        "alternative_action_keys": json.dumps(["consult_lawyer"]),
        # 5-part doctrine → DB columns what/why/meaning/what_to_do/expected_effect
        "what": tpl["what_happened"],
        "why": tpl["why_it_matters"],
        "meaning": tpl["meaning"],
        "what_to_do": _ACTION_TEXT.get(r.recommended_action, "Проверить требование."),
        "expected_effect": _EFFECT_TEXT[effect_kind],
        "priority_level": r.severity,
        "risk_level": r.risk_band,
        "effect_type": f"{effect_kind}_reduction",   # qualitative, never money
        "effect_band": r.risk_band,
        "confidence": None,                            # no numeric score
    }


async def persist_audit(
    db: AsyncSession, *, seller_id: str, snapshot: LegalSnapshot, evaluations,
    triggered_by: str = "manual", now: Optional[datetime] = None,
) -> LegalPersistResult:
    """Persist a completed legal audit + full ledger + findings + advisory signals."""
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

    # full coverage ledger — every requirement outcome persisted
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

    # detected → immutable finding + advisory signal (deduped to one live per insight_key)
    for r in detected:
        evh = _evidence_hash(dict(r.evidence))
        finding = LegalFinding(
            audit_id=audit.id, user_id=seller_id, listing_id=snapshot.listing_id,
            marketplace=snapshot.marketplace, sku=snapshot.sku,
            subject_type=snapshot.subject_type, subject_ref=snapshot.subject_ref,
            requirement_type=r.requirement_type, category=_CATEGORY[r.requirement_type],
            severity=r.severity, risk_level=r.risk_band,
            estimated_effect_type=_EFFECT_KIND[r.requirement_type], detectability="listing",
            evidence=json.dumps(dict(r.evidence)), created_at=ts,
        )
        db.add(finding)
        await db.flush()
        result.finding_ids.append(finding.id)

        sf = _signal_fields(snapshot, r)
        existing = (await db.execute(select(LegalSignal).where(
            LegalSignal.user_id == seller_id,
            LegalSignal.insight_key == sf["insight_key"],
            LegalSignal.status.in_(_LIVE)))).scalars().first()

        if existing is None:
            sig = LegalSignal(
                audit_id=audit.id, finding_id=finding.id, user_id=seller_id,
                listing_id=snapshot.listing_id, marketplace=snapshot.marketplace,
                sku=snapshot.sku, subject_type=snapshot.subject_type,
                subject_ref=snapshot.subject_ref, status="active", evidence_hash=evh,
                created_at=ts, updated_at=ts, **sf,
            )
            db.add(sig)
            await db.flush()
            result.signal_ids.append(sig.id)
            result.signals_created += 1
        elif existing.evidence_hash != evh:
            for k, v in sf.items():
                setattr(existing, k, v)
            existing.audit_id = audit.id
            existing.finding_id = finding.id
            existing.evidence_hash = evh
            existing.updated_at = ts
            result.signal_ids.append(existing.id)
            result.signals_updated += 1
        else:
            result.signal_ids.append(existing.id)
            result.signals_unchanged += 1

    await db.flush()
    return result


async def audit_and_persist(
    db: AsyncSession, *, seller_id: str, marketplace: str, subject_type: Optional[str] = None,
    subject_ref: Optional[str] = None, sku: Optional[str] = None, listing_id: Optional[str] = None,
    triggered_by: str = "manual", now: Optional[datetime] = None,
):
    """Build snapshot (A3) → evaluate (A4) → persist (A5). Flush-only.

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
