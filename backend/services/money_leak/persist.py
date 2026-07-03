"""
Money Leak persist + reconcile (Phase 3.1) — flush-only, DB-headless.

Writes one append-only money_leak_audit per (mp, sku) run and reconciles each confirmed
cost-share drift into a single live money_leak_signal per insight_key. A (mp, sku) may
carry BOTH commission_drift and logistics_drift — they are independent insight_keys.

PURE DIAGNOSIS: no recommended_action_key that binds an executor, no Decision, no
measurement — an evidence-backed symptom only.

Honesty rule: a confirmed drift upserts its own live signal; honest absence (no confirmed
drift this run) touches nothing — a noisy/flat window does not prove the leak sealed, so
existing live signals are never auto-resolved from absence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.money_leak_signal import MoneyLeakSignal
from .diagnosis_source import MoneyLeakDiagnosis

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


_LABEL = {"commission_drift": "Комиссия", "logistics_drift": "Логистика"}


def _doctrine(diag: MoneyLeakDiagnosis) -> dict:
    mp, sku, pct = diag.marketplace or "—", diag.sku or "—", diag.ratio_growth_pct
    label = _LABEL.get(diag.problem_type, "Издержки")
    return {
        "what": f"{label} съедает всё большую долю выручки {sku} ({mp})",
        "why": f"Наблюдаемый рост доли на {pct}% по {len(diag.ratio_windows)} окнам "
               f"за {diag.distinct_days} дн. (не разовый скачок)",
        "meaning": "Тихая утечка: издержки растут как доля выручки — маржа размывается без вашего действия",
        "what_to_do": "Проверьте условия/тариф этой статьи затрат (диагноз, не действие)",
        "expected_effect": "Раннее внимание к дрейфу издержек может остановить размывание маржи",
    }


def _assign(sig: MoneyLeakSignal, diag: MoneyLeakDiagnosis, *, audit_id, evh, now, status):
    d = _doctrine(diag)
    sig.audit_id = audit_id
    sig.signal_key = diag.signal_key
    sig.insight_key = diag.insight_key
    sig.problem_type = diag.problem_type
    sig.category = "money_leak"
    sig.recommended_action_key = None          # PURE DIAGNOSIS — never binds an executor
    sig.alternative_action_keys = None
    sig.what = d["what"]; sig.why = d["why"]; sig.meaning = d["meaning"]
    sig.what_to_do = d["what_to_do"]; sig.expected_effect = d["expected_effect"]
    sig.priority_level = diag.priority_level
    sig.effect_type = "margin_erosion"         # deterministic class, no number/forecast
    sig.effect_band = diag.effect_band
    sig.confidence = None
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new(diag: MoneyLeakDiagnosis, *, audit_id, user_id, listing_id, evh, now) -> MoneyLeakSignal:
    sig = MoneyLeakSignal(
        audit_id=audit_id, user_id=user_id, listing_id=listing_id,
        marketplace=diag.marketplace, sku=diag.sku, signal_key=diag.signal_key,
        problem_type=diag.problem_type, status=ACTIVE, created_at=now)
    _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=ACTIVE)
    return sig


async def reconcile_money_leak_signals(
    db: AsyncSession, *, user_id: str, listing_id, audit_id: str, marketplace, sku,
    diagnoses: List[MoneyLeakDiagnosis], now: datetime) -> ReconcileResult:
    """Upsert each confirmed drift as one live money_leak_signal per insight_key."""
    rows = (await db.execute(select(MoneyLeakSignal).where(
        MoneyLeakSignal.user_id == user_id,
        MoneyLeakSignal.marketplace == marketplace,
        MoneyLeakSignal.sku == sku))).scalars().all()
    by_key = {r.insight_key: r for r in rows}
    res = ReconcileResult()

    for diag in diagnoses:
        evh = evidence_hash(diag.evidence)
        sig = by_key.get(diag.insight_key)
        if sig is None:
            db.add(_new(diag, audit_id=audit_id, user_id=user_id, listing_id=listing_id,
                        evh=evh, now=now)); res.created += 1
        elif sig.status in _LIVE:
            if sig.evidence_hash != evh:
                _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=sig.status)
                res.updated += 1
            else:
                res.unchanged += 1
        elif sig.status == RESOLVED:
            _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=REOPENED)
            res.reopened += 1
        elif sig.status == DISMISSED:
            if sig.evidence_hash != evh:
                _assign(sig, diag, audit_id=audit_id, evh=evh, now=now, status=REOPENED)
                res.reopened += 1
            else:
                res.unchanged += 1
        else:                                          # promoted_to_decision — Decision owns it
            res.unchanged += 1

    await db.flush()
    return res
