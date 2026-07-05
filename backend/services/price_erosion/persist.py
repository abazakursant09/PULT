"""
Price Erosion Diagnosis persist + reconcile (Phase 8.1) — flush-only, DB-headless.

Writes one append-only price_erosion_audit per (mp, sku) run and reconciles the observed price
erosion into a single live price_erosion_signal per insight_key. PURE DIAGNOSIS: no
recommended_action_key that binds an executor, no Decision, no measurement, no price-write — an
evidence-backed symptom only. DISTINCT from the executable Pricing contour; does NOT touch
pricing_signal.

Honesty rule: a confirmed erosion upserts its live signal; honest absence (no confirmed drift —
thin history, flat/rising price, or unconfirmed dip) touches nothing — it does not auto-resolve a
live signal (a quiet window does not prove the price recovered). Doctrine text is strictly
self-referential — it compares ONLY to this product's own baseline price. No floor, no benchmark,
no competitor, no price-change instruction.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.price_erosion_signal import PriceErosionSignal
from .diagnosis_source import PriceErosionDiagnosis

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


def _doctrine(diag: PriceErosionDiagnosis) -> dict:
    mp, sku = diag.marketplace or "—", diag.sku or "—"
    drop_pct = round(diag.relative_drop * 100)
    return {
        "what": f"Цена {sku} снизилась с {round(diag.baseline_price, 2)} до "
                f"{round(diag.latest_price, 2)} (−{drop_pct}% относительно собственной базы) ({mp})",
        "why": f"Наблюдаемое снижение цены на {drop_pct}% по {diag.snapshot_count} датированным "
               f"снимкам (последний ниже базового, подтверждено предыдущим снимком)",
        "meaning": "Эрозия цены сжимает маржу — деньги теряются на каждой продаже при том же объёме",
        "what_to_do": "Проверьте причины снижения цены: скидки, акции, демпинг "
                      "(диагноз, не действие)",
        "expected_effect": "Раннее внимание к эрозии цены может остановить сжатие маржи",
    }


def _assign(sig: PriceErosionSignal, diag: PriceErosionDiagnosis, *, audit_id, evh, now, status):
    d = _doctrine(diag)
    sig.audit_id = audit_id
    sig.signal_key = diag.signal_key
    sig.insight_key = diag.insight_key
    sig.problem_type = diag.problem_type
    sig.category = "price_erosion"
    sig.recommended_action_key = None          # PURE DIAGNOSIS — never binds an executor / price-write
    sig.alternative_action_keys = None
    sig.what = d["what"]; sig.why = d["why"]; sig.meaning = d["meaning"]
    sig.what_to_do = d["what_to_do"]; sig.expected_effect = d["expected_effect"]
    sig.priority_level = diag.priority_level
    sig.effect_type = "margin_compression"     # deterministic class, no number/forecast
    sig.effect_band = diag.effect_band
    sig.confidence = None
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new(diag: PriceErosionDiagnosis, *, audit_id, user_id, listing_id, evh, now) -> PriceErosionSignal:
    sig = PriceErosionSignal(
        audit_id=audit_id, user_id=user_id, listing_id=listing_id,
        marketplace=diag.marketplace, sku=diag.sku, signal_key=diag.signal_key,
        problem_type=diag.problem_type, status=ACTIVE, created_at=now)
    _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=ACTIVE)
    return sig


async def reconcile_price_erosion_signal(
    db: AsyncSession, *, user_id: str, listing_id, audit_id: str, marketplace, sku,
    diagnosis: Optional[PriceErosionDiagnosis], now: datetime) -> ReconcileResult:
    """One live price_erosion_signal per insight_key for this (user, mp, sku)."""
    res = ReconcileResult()
    if diagnosis is None:
        return res                                     # honest absence — touch nothing

    rows = (await db.execute(select(PriceErosionSignal).where(
        PriceErosionSignal.user_id == user_id,
        PriceErosionSignal.marketplace == marketplace,
        PriceErosionSignal.sku == sku))).scalars().all()
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
