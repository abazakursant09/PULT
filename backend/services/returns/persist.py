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

from models.returns_signal import ReturnsSignal
from .diagnosis_source import ReturnsDiagnosis

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
