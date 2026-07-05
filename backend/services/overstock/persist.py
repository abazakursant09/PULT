"""
Overstock / Dead Stock Diagnosis persist + reconcile (Phase 7.1) — flush-only, DB-headless.

Writes one append-only overstock_audit per (mp, sku) run and reconciles the observed overstock
into a single live overstock_signal per insight_key. PURE DIAGNOSIS: no recommended_action_key
that binds an executor, no Decision, no measurement — an evidence-backed symptom only. The mirror
of Supply; does NOT touch supply_signal.

Honesty rule: a confirmed overstock upserts its live signal; honest absence (no overstock this
run — depleted stock, thin history, or ample demand) touches nothing — it does not auto-resolve
a live signal (a quiet window does not prove the inventory freed up). Doctrine text is strictly
self/observed — no absolute benchmark, no competitor, no discount/liquidation instruction.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.overstock_signal import OverstockSignal
from .diagnosis_source import OverstockDiagnosis

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


def _doctrine(diag: OverstockDiagnosis) -> dict:
    mp, sku = diag.marketplace or "—", diag.sku or "—"
    if diag.problem_type == "dead_stock":
        return {
            "what": f"{sku}: остаток {diag.stock} шт. без наблюдаемых продаж за {diag.distinct_days} дн. ({mp})",
            "why": f"Наблюдаемый темп продаж 0 шт./дн по {diag.distinct_days} дн. при остатке "
                   f"{diag.stock} шт. — товар не движется",
            "meaning": "Мёртвый сток — деньги заморожены в запасе, растут издержки хранения",
            "what_to_do": "Проверьте причины отсутствия продаж по этому товару (диагноз, не действие)",
            "expected_effect": "Раннее внимание к мёртвому стоку может высвободить замороженные средства",
        }
    days = round(diag.days_of_cover) if diag.days_of_cover is not None else "—"
    return {
        "what": f"{sku}: запаса хватит примерно на {days} дн. при текущем темпе продаж ({mp})",
        "why": f"Остаток {diag.stock} шт., наблюдаемый темп {round(diag.velocity, 1)} шт./дн по "
               f"{diag.distinct_days} дн. → покрытие ≈ {days} дн.",
        "meaning": "Избыточный запас — деньги заморожены в излишке относительно спроса, растут "
                   "издержки хранения",
        "what_to_do": "Проверьте объём закупки по этому товару относительно спроса "
                      "(диагноз, не действие)",
        "expected_effect": "Раннее внимание к избыточному запасу может высвободить замороженные средства",
    }


def _assign(sig: OverstockSignal, diag: OverstockDiagnosis, *, audit_id, evh, now, status):
    d = _doctrine(diag)
    sig.audit_id = audit_id
    sig.signal_key = diag.signal_key
    sig.insight_key = diag.insight_key
    sig.problem_type = diag.problem_type
    sig.category = "overstock"
    sig.recommended_action_key = None          # PURE DIAGNOSIS — never binds an executor
    sig.alternative_action_keys = None
    sig.what = d["what"]; sig.why = d["why"]; sig.meaning = d["meaning"]
    sig.what_to_do = d["what_to_do"]; sig.expected_effect = d["expected_effect"]
    sig.priority_level = diag.priority_level
    sig.effect_type = "frozen_capital"         # deterministic class, no number/forecast
    sig.effect_band = diag.effect_band
    sig.confidence = None
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new(diag: OverstockDiagnosis, *, audit_id, user_id, listing_id, evh, now) -> OverstockSignal:
    sig = OverstockSignal(
        audit_id=audit_id, user_id=user_id, listing_id=listing_id,
        marketplace=diag.marketplace, sku=diag.sku, signal_key=diag.signal_key,
        problem_type=diag.problem_type, status=ACTIVE, created_at=now)
    _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=ACTIVE)
    return sig


async def reconcile_overstock_signal(
    db: AsyncSession, *, user_id: str, listing_id, audit_id: str, marketplace, sku,
    diagnosis: Optional[OverstockDiagnosis], now: datetime) -> ReconcileResult:
    """One live overstock_signal per insight_key for this (user, mp, sku)."""
    res = ReconcileResult()
    if diagnosis is None:
        return res                                     # honest absence — touch nothing

    rows = (await db.execute(select(OverstockSignal).where(
        OverstockSignal.user_id == user_id,
        OverstockSignal.marketplace == marketplace,
        OverstockSignal.sku == sku))).scalars().all()
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
