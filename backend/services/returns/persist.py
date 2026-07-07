"""
Returns Diagnosis persist + reconcile (Phase R1b) — flush-only, DB-headless.

Writes one append-only returns_audit per (mp, sku) run and reconciles the observed return-rate
rise into a single live returns_signal per insight_key. PURE DIAGNOSIS: no recommended_action_key
that binds an executor, no Decision, no measurement, no marketplace write — an evidence-backed
symptom only.

DOUBLE-COUNT DISCIPLINE: the doctrine text states a return-FREQUENCY rise only — it never quotes a
money loss from returns (net_profit may already reflect returns; return_amount is not used).

Honesty rule: a confirmed rise upserts its live signal; honest absence (no confirmed rise — thin
history, insufficient volume, flat/falling rate, or earlier_rate 0) touches nothing — it does not
auto-resolve a live signal (a quiet window does not prove the return rate recovered).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from typing import List

from models.returns_signal import ReturnsSignal
from .diagnosis_source import ReturnsDiagnosis, ReasonShareDiagnosis

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
    reopened: int = 0
    unchanged: int = 0


def evidence_hash(evidence: Optional[dict]) -> str:
    return hashlib.sha256(
        json.dumps(evidence or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _doctrine(diag: ReturnsDiagnosis) -> dict:
    mp, sku = diag.marketplace or "—", diag.sku or "—"
    rise_pct = round(diag.relative_rise * 100)
    return {
        "what": f"Частота возвратов {sku} выросла на {rise_pct}% относительно собственного "
                f"прежнего темпа ({mp})",
        "why": f"Наблюдаемая доля возвратов на продажу выросла с "
               f"{round(diag.earlier_return_rate, 3)} до {round(diag.recent_return_rate, 3)} "
               f"(ранее {diag.earlier_returns} возвратов на {diag.earlier_units} продаж, недавно "
               f"{diag.recent_returns} на {diag.recent_units})",
        "meaning": "Рост частоты возвратов — ранний сигнал проблем с качеством товара или "
                   "точностью карточки; риск давления на маржу и рейтинг",
        "what_to_do": "Проверьте причины возвратов по этому товару: качество, размер, описание "
                      "(диагноз, не действие)",
        "expected_effect": "Раннее внимание к росту возвратов может остановить эрозию маржи и "
                           "репутации",
    }


def _assign(sig: ReturnsSignal, diag: ReturnsDiagnosis, *, audit_id, evh, now, status):
    d = _doctrine(diag)
    sig.audit_id = audit_id
    sig.signal_key = diag.signal_key
    sig.insight_key = diag.insight_key
    sig.problem_type = diag.problem_type
    sig.category = "returns"
    sig.recommended_action_key = None          # PURE DIAGNOSIS — never binds an executor
    sig.alternative_action_keys = None
    sig.what = d["what"]; sig.why = d["why"]; sig.meaning = d["meaning"]
    sig.what_to_do = d["what_to_do"]; sig.expected_effect = d["expected_effect"]
    sig.priority_level = diag.priority_level
    sig.effect_type = "return_rate_rise"       # deterministic class, no number/forecast
    sig.effect_band = diag.effect_band
    sig.confidence = None
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new(diag: ReturnsDiagnosis, *, audit_id, user_id, listing_id, evh, now) -> ReturnsSignal:
    sig = ReturnsSignal(
        audit_id=audit_id, user_id=user_id, listing_id=listing_id,
        marketplace=diag.marketplace, sku=diag.sku, signal_key=diag.signal_key,
        problem_type=diag.problem_type, status=ACTIVE, created_at=now)
    _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=ACTIVE)
    return sig


async def reconcile_returns_signal(
    db: AsyncSession, *, user_id: str, listing_id, audit_id: str, marketplace, sku,
    diagnosis: Optional[ReturnsDiagnosis], now: datetime) -> ReconcileResult:
    """One live returns_signal per insight_key for this (user, mp, sku)."""
    res = ReconcileResult()
    if diagnosis is None:
        return res                                     # honest absence — touch nothing

    rows = (await db.execute(select(ReturnsSignal).where(
        ReturnsSignal.user_id == user_id,
        ReturnsSignal.marketplace == marketplace,
        ReturnsSignal.sku == sku))).scalars().all()
    by_key = {r.insight_key: r for r in rows}
    evh = evidence_hash(diagnosis.evidence)
    sig = by_key.get(diagnosis.insight_key)

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
    else:                                              # promoted_to_decision — Decision owns it
        res.unchanged += 1

    await db.flush()
    return res


# ══════════════════════════════════════════════════════════════════════════════
# Returns-Reason Diagnosis — reason_share_rise_<bucket>, list reconcile (mirrors the
# Money Leak list pattern). Observed COMPOSITION drift, counts-only, advisory-only.
# The existing single return_rate_rise reconcile above is untouched.
# ══════════════════════════════════════════════════════════════════════════════

# reported-reason label used ONLY in seller copy — reported-fact framing, never a claim
# that the product HAS this defect. We describe what buyers increasingly reported.
_REASON_LABEL = {
    "quality_defect":       "«брак / дефект»",
    "size_fit":             "«размер не подошёл»",
    "mismatch_description": "«не соответствует описанию»",
    "damaged_delivery":     "«повреждён при доставке»",
    "changed_mind":         "«передумал / не понравился»",
}


def _reason_doctrine(diag: ReasonShareDiagnosis) -> dict:
    mp, sku = diag.marketplace or "—", diag.sku or "—"
    label = _REASON_LABEL.get(diag.bucket, "«прочее»")
    e_pct, r_pct = round(diag.earlier_share * 100), round(diag.recent_share * 100)
    return {
        # reported FACT — "buyers increasingly cite this reason", NOT "the product is defective"
        "what": f"В возвратах {sku} чаще стали указывать причину {label} ({mp})",
        "why": f"Доля этой причины среди возвратов выросла с {e_pct}% до {r_pct}% "
               f"(ранее {diag.earlier_bucket_returns} из {diag.earlier_total_returns} возвратов, "
               f"недавно {diag.recent_bucket_returns} из {diag.recent_total_returns})",
        "meaning": "Наблюдаемое смещение состава возвратов к этой заявленной причине — ранний "
                   "сигнал по конкретной теме, отдельно от общей частоты возвратов",
        "what_to_do": "Проверьте эту заявленную причину возвратов по товару (диагноз по "
                      "заявленной причине, не действие)",
        "expected_effect": "Раннее внимание к причине возвратов может помочь снизить её долю",
    }


def _assign_reason(sig: ReturnsSignal, diag: ReasonShareDiagnosis, *, audit_id, evh, now, status):
    d = _reason_doctrine(diag)
    sig.audit_id = audit_id
    sig.signal_key = diag.signal_key
    sig.insight_key = diag.insight_key
    sig.problem_type = diag.problem_type
    sig.category = "returns"
    sig.recommended_action_key = None          # PURE DIAGNOSIS — never binds an executor
    sig.alternative_action_keys = None
    sig.what = d["what"]; sig.why = d["why"]; sig.meaning = d["meaning"]
    sig.what_to_do = d["what_to_do"]; sig.expected_effect = d["expected_effect"]
    sig.priority_level = diag.priority_level
    sig.effect_type = "return_reason_shift"    # deterministic class, distinct from return_rate_rise
    sig.effect_band = diag.effect_band
    sig.confidence = None
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new_reason(diag: ReasonShareDiagnosis, *, audit_id, user_id, listing_id, evh, now) -> ReturnsSignal:
    sig = ReturnsSignal(
        audit_id=audit_id, user_id=user_id, listing_id=listing_id,
        marketplace=diag.marketplace, sku=diag.sku, signal_key=diag.signal_key,
        problem_type=diag.problem_type, status=ACTIVE, created_at=now)
    _assign_reason(sig, diag, audit_id=audit_id, evh=evh, now=now, status=ACTIVE)
    return sig


async def reconcile_returns_reason_signals(
    db: AsyncSession, *, user_id: str, listing_id, audit_id: str, marketplace, sku,
    diagnoses: List[ReasonShareDiagnosis], now: datetime) -> ReconcileResult:
    """Upsert each confirmed reason-share rise as one live returns_signal per insight_key.
    A (mp, sku) may carry several rising buckets — each is an independent insight_key. Honest
    absence (empty list) touches nothing. Does NOT auto-resolve the return_rate_rise signal or
    any bucket not present this run (a quiet window is not proof the mix reverted)."""
    res = ReconcileResult()
    if not diagnoses:
        return res

    rows = (await db.execute(select(ReturnsSignal).where(
        ReturnsSignal.user_id == user_id,
        ReturnsSignal.marketplace == marketplace,
        ReturnsSignal.sku == sku))).scalars().all()
    by_key = {r.insight_key: r for r in rows}

    for diag in diagnoses:
        evh = evidence_hash(diag.evidence)
        sig = by_key.get(diag.insight_key)
        if sig is None:
            db.add(_new_reason(diag, audit_id=audit_id, user_id=user_id, listing_id=listing_id,
                               evh=evh, now=now)); res.created += 1
        elif sig.status in _LIVE:
            if sig.evidence_hash != evh:
                _assign_reason(sig, diag, audit_id=audit_id, evh=evh, now=now, status=sig.status)
                res.updated += 1
            else:
                res.unchanged += 1
        elif sig.status == RESOLVED:
            _assign_reason(sig, diag, audit_id=audit_id, evh=evh, now=now, status=REOPENED)
            res.reopened += 1
        elif sig.status == DISMISSED:
            if sig.evidence_hash != evh:
                _assign_reason(sig, diag, audit_id=audit_id, evh=evh, now=now, status=REOPENED)
                res.reopened += 1
            else:
                res.unchanged += 1
        else:                                          # promoted_to_decision — Decision owns it
            res.unchanged += 1

    await db.flush()
    return res
