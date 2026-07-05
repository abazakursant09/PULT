"""
Review Velocity Diagnosis persist + reconcile (Phase 6.1) — flush-only, DB-headless.

Writes one append-only review_velocity_audit per (mp, sku) run and reconciles the observed
review-acquisition stall into a single live review_velocity_signal per insight_key. PURE
DIAGNOSIS: no recommended_action_key that binds an executor, no Decision, no measurement — an
evidence-backed symptom only. DISTINCT from BOTH the Rating contour (does not touch
rating_signal) and the Review contour (does not touch review_signal).

Honesty rule: a confirmed stall upserts its live signal; honest absence (no confirmed stall —
thin history, insufficient sales, flat/accelerating rate, or unconfirmed) touches nothing — it
does not auto-resolve a live signal (a quiet window does not prove acquisition recovered).

Doctrine text is strictly self-referential — it compares ONLY to this product's own earlier
observed rate. No absolute floor, no category benchmark, no competitor, no "too few reviews".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.review_velocity_signal import ReviewVelocitySignal
from .diagnosis_source import ReviewVelocityDiagnosis

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


def _doctrine(diag: ReviewVelocityDiagnosis) -> dict:
    mp, sku = diag.marketplace or "—", diag.sku or "—"
    drop_pct = round(diag.relative_drop * 100)
    return {
        "what": f"Скорость набора отзывов у {sku} замедлилась на {drop_pct}% относительно "
                f"собственного прежнего темпа ({mp})",
        "why": f"Наблюдаемый темп отзывов на продажу упал с "
               f"{round(diag.earlier_review_rate, 3)} до {round(diag.recent_review_rate, 3)} "
               f"(ранее {diag.earlier_reviews_gained} отзывов на {diag.earlier_units_sold} продаж, "
               f"недавно {diag.recent_reviews_gained} на {diag.recent_units_sold}) — продажи "
               f"продолжались",
        "meaning": "Замедление набора отзывов ослабляет социальное доказательство карточки — "
                   "риск снижения конверсии и доверия покупателей со временем",
        "what_to_do": "Проверьте, почему покупатели стали реже оставлять отзывы "
                      "(диагноз, не действие)",
        "expected_effect": "Раннее внимание к замедлению отзывов может сохранить силу "
                           "социального доказательства карточки",
    }


def _assign(sig: ReviewVelocitySignal, diag: ReviewVelocityDiagnosis, *, audit_id, evh, now, status):
    d = _doctrine(diag)
    sig.audit_id = audit_id
    sig.signal_key = diag.signal_key
    sig.insight_key = diag.insight_key
    sig.problem_type = diag.problem_type
    sig.category = "review_velocity"
    sig.recommended_action_key = None          # PURE DIAGNOSIS — never binds an executor
    sig.alternative_action_keys = None
    sig.what = d["what"]; sig.why = d["why"]; sig.meaning = d["meaning"]
    sig.what_to_do = d["what_to_do"]; sig.expected_effect = d["expected_effect"]
    sig.priority_level = diag.priority_level
    sig.effect_type = "social_proof_stall"     # deterministic class, no number/forecast
    sig.effect_band = diag.effect_band
    sig.confidence = None
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new(diag: ReviewVelocityDiagnosis, *, audit_id, user_id, listing_id, evh, now) -> ReviewVelocitySignal:
    sig = ReviewVelocitySignal(
        audit_id=audit_id, user_id=user_id, listing_id=listing_id,
        marketplace=diag.marketplace, sku=diag.sku, signal_key=diag.signal_key,
        problem_type=diag.problem_type, status=ACTIVE, created_at=now)
    _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=ACTIVE)
    return sig


async def reconcile_review_velocity_signal(
    db: AsyncSession, *, user_id: str, listing_id, audit_id: str, marketplace, sku,
    diagnosis: Optional[ReviewVelocityDiagnosis], now: datetime) -> ReconcileResult:
    """One live review_velocity_signal per insight_key for this (user, mp, sku)."""
    res = ReconcileResult()
    if diagnosis is None:
        return res                                     # honest absence — touch nothing

    rows = (await db.execute(select(ReviewVelocitySignal).where(
        ReviewVelocitySignal.user_id == user_id,
        ReviewVelocitySignal.marketplace == marketplace,
        ReviewVelocitySignal.sku == sku))).scalars().all()
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
