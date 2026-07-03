"""
Revenue Diagnosis persist + reconcile (Phase 2.1) — flush-only, DB-headless.

Writes one append-only revenue_audit per (mp, sku) run and reconciles the confirmed
trajectory into a single live revenue_signal per insight_key (mirrors the growth
reconciliation shape). PURE DIAGNOSIS: no recommended_action_key that binds an executor,
no Decision, no measurement — an evidence-backed symptom only.

Honesty rule: a live signal RESOLVES only when THIS run produced a CONFIRMED DIFFERENT
trajectory for the same (mp, sku). Honest absence (no confirmed trajectory) never resolves
a live signal — an unconfirmed/noisy window does not prove recovery.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.revenue_signal import RevenueSignal
from .diagnosis_source import RevenueDiagnosis

ACTIVE = "active"
DISMISSED = "dismissed"
PROMOTED = "promoted_to_decision"
RESOLVED = "resolved"
REOPENED = "reopened"
_LIVE = {ACTIVE, REOPENED}


@dataclass
class ReconcileResult:
    created: int = 0
    updated: int = 0
    resolved: int = 0
    reopened: int = 0
    unchanged: int = 0


def evidence_hash(evidence: Optional[dict]) -> str:
    return hashlib.sha256(
        json.dumps(evidence or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ── deterministic doctrine text (observed-only; % is observed, never a forecast) ──
_DECL = {"slowing": "замедляется", "sustained_decline": "устойчиво снижается",
         "collapse": "обвально падает"}


def _doctrine(diag: RevenueDiagnosis) -> dict:
    mp, sku = diag.marketplace or "—", diag.sku or "—"
    days, pct = diag.distinct_days, diag.growth_pct
    if diag.problem_type == "acceleration":
        return {
            "what": f"Выручка {sku} подтверждённо растёт ({mp})",
            "why": f"Наблюдаемый рост {pct}% по {len(diag.windows)} окнам за {days} дн.",
            "meaning": "Товар набирает выручку — подтверждённая (не разовый всплеск) траектория роста",
            "what_to_do": "Проверьте, чем усилить рост: реклама, остаток, цена (диагноз, не действие)",
            "expected_effect": "Раннее внимание к растущему товару может закрепить рост выручки",
        }
    verb = _DECL.get(diag.problem_type, "снижается")
    return {
        "what": f"Выручка {sku} подтверждённо {verb} ({mp})",
        "why": f"Наблюдаемое падение {pct}% по {len(diag.windows)} окнам за {days} дн. (не разовый спад)",
        "meaning": "С этого товара уходит выручка — подтверждённая нисходящая траектория",
        "what_to_do": "Проверьте вероятные причины: остаток, цена, реклама, рейтинг (диагноз, не действие)",
        "expected_effect": "Раннее вмешательство может остановить дальнейшую потерю выручки",
    }


def _assign(sig: RevenueSignal, diag: RevenueDiagnosis, *, audit_id, evh, now, status):
    d = _doctrine(diag)
    sig.audit_id = audit_id
    sig.signal_key = diag.signal_key
    sig.insight_key = diag.insight_key
    sig.problem_type = diag.problem_type
    sig.category = "revenue"
    sig.recommended_action_key = None          # PURE DIAGNOSIS — never binds an executor
    sig.alternative_action_keys = None
    sig.what = d["what"]; sig.why = d["why"]; sig.meaning = d["meaning"]
    sig.what_to_do = d["what_to_do"]; sig.expected_effect = d["expected_effect"]
    sig.priority_level = diag.priority_level
    sig.effect_type = "revenue_change"         # deterministic class, no number/forecast
    sig.effect_band = diag.effect_band
    sig.confidence = None
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new(diag: RevenueDiagnosis, *, audit_id, user_id, listing_id, evh, now) -> RevenueSignal:
    sig = RevenueSignal(
        audit_id=audit_id, user_id=user_id, listing_id=listing_id,
        marketplace=diag.marketplace, sku=diag.sku, signal_key=diag.signal_key,
        problem_type=diag.problem_type, status=ACTIVE, created_at=now)
    _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=ACTIVE)
    return sig


async def reconcile_revenue_signal(
    db: AsyncSession, *, user_id: str, listing_id, audit_id: str, marketplace, sku,
    diagnosis: Optional[RevenueDiagnosis], now: datetime) -> ReconcileResult:
    """One live revenue_signal per insight_key for this (user, mp, sku)."""
    rows = (await db.execute(select(RevenueSignal).where(
        RevenueSignal.user_id == user_id,
        RevenueSignal.marketplace == marketplace,
        RevenueSignal.sku == sku))).scalars().all()
    by_key = {r.insight_key: r for r in rows}
    res = ReconcileResult()
    current_key = diagnosis.insight_key if diagnosis else None

    # Resolve stale live signals ONLY when a confirmed DIFFERENT trajectory replaces them.
    for r in rows:
        if diagnosis is not None and r.insight_key != current_key and r.status in _LIVE:
            r.status = RESOLVED; r.audit_id = audit_id; r.updated_at = now
            res.resolved += 1

    if diagnosis is None:
        await db.flush()
        return res                                          # honest absence — touch nothing else

    evh = evidence_hash(diagnosis.evidence)
    sig = by_key.get(current_key)
    if sig is None:
        db.add(_new(diagnosis, audit_id=audit_id, user_id=user_id, listing_id=listing_id,
                    evh=evh, now=now)); res.created += 1
    elif sig.status in _LIVE:
        if sig.evidence_hash != evh:
            _assign(sig, diagnosis, audit_id=audit_id, evh=evh, now=now, status=sig.status)
            res.updated += 1
        else:
            res.unchanged += 1
    elif sig.status == RESOLVED:
        _assign(sig, diagnosis, audit_id=audit_id, evh=evh, now=now, status=REOPENED)
        res.reopened += 1
    elif sig.status == DISMISSED:
        if sig.evidence_hash != evh:
            _assign(sig, diagnosis, audit_id=audit_id, evh=evh, now=now, status=REOPENED)
            res.reopened += 1
        else:
            res.unchanged += 1
    else:                                                   # promoted_to_decision — Decision owns it
        res.unchanged += 1

    await db.flush()
    return res
