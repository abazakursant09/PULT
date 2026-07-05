"""
Rating Diagnosis persist + reconcile (Phase 5.1) — flush-only, DB-headless.

Writes one append-only rating_audit per (mp, sku) run and reconciles the observed rating
decline into a single live rating_signal per insight_key. PURE DIAGNOSIS: no
recommended_action_key that binds an executor, no Decision, no measurement — an
evidence-backed symptom only. DISTINCT from the Review contour; does not touch review_signal.

Honesty rule: a confirmed decline upserts its live signal; honest absence (no confirmed
decline — thin history, flat/rising rating, or unconfirmed blip) touches nothing — it does
not auto-resolve a live signal (a quiet window does not prove the reputation recovered).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.rating_signal import RatingSignal
from .diagnosis_source import RatingDiagnosis

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


def _doctrine(diag: RatingDiagnosis) -> dict:
    mp, sku = diag.marketplace or "—", diag.sku or "—"
    drop = round(diag.rating_drop, 2)
    return {
        "what": f"Рейтинг {sku} снизился с {round(diag.baseline_rating, 2)} до "
                f"{round(diag.latest_rating, 2)} ({mp})",
        "why": f"Наблюдаемое падение на {drop} по {diag.snapshot_count} датированным снимкам "
               f"(последний ниже базового, подтверждено предыдущим снимком)",
        "meaning": "Падение рейтинга снижает конверсию, ослабляет позицию в поиске и "
                   "эффективность рекламы — риск будущей потери выручки",
        "what_to_do": "Проверьте причины падения рейтинга: отзывы, качество, доставку "
                      "(диагноз, не действие)",
        "expected_effect": "Раннее внимание к падению рейтинга может остановить эрозию репутации",
    }


def _assign(sig: RatingSignal, diag: RatingDiagnosis, *, audit_id, evh, now, status):
    d = _doctrine(diag)
    sig.audit_id = audit_id
    sig.signal_key = diag.signal_key
    sig.insight_key = diag.insight_key
    sig.problem_type = diag.problem_type
    sig.category = "rating"
    sig.recommended_action_key = None          # PURE DIAGNOSIS — never binds an executor
    sig.alternative_action_keys = None
    sig.what = d["what"]; sig.why = d["why"]; sig.meaning = d["meaning"]
    sig.what_to_do = d["what_to_do"]; sig.expected_effect = d["expected_effect"]
    sig.priority_level = diag.priority_level
    sig.effect_type = "reputation_decline"     # deterministic class, no number/forecast
    sig.effect_band = diag.effect_band
    sig.confidence = None
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new(diag: RatingDiagnosis, *, audit_id, user_id, listing_id, evh, now) -> RatingSignal:
    sig = RatingSignal(
        audit_id=audit_id, user_id=user_id, listing_id=listing_id,
        marketplace=diag.marketplace, sku=diag.sku, signal_key=diag.signal_key,
        problem_type=diag.problem_type, status=ACTIVE, created_at=now)
    _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=ACTIVE)
    return sig


async def reconcile_rating_signal(
    db: AsyncSession, *, user_id: str, listing_id, audit_id: str, marketplace, sku,
    diagnosis: Optional[RatingDiagnosis], now: datetime) -> ReconcileResult:
    """One live rating_signal per insight_key for this (user, mp, sku)."""
    res = ReconcileResult()
    if diagnosis is None:
        return res                                     # honest absence — touch nothing

    rows = (await db.execute(select(RatingSignal).where(
        RatingSignal.user_id == user_id,
        RatingSignal.marketplace == marketplace,
        RatingSignal.sku == sku))).scalars().all()
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
