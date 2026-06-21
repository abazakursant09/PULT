"""
Advertising Signal Reconciliation (A6) — give signals memory + lifecycle.

Instead of creating a fresh signal every audit, reconcile the current audit's
rule outcomes against existing signals for the same (user, listing), keyed by the
canonical insight_key. One signal row per insight_key — a duplicate live signal
is structurally impossible.

Lifecycle: active | dismissed | promoted_to_decision | resolved | reopened.

Honesty rule: a signal is RESOLVED only when its rule ran and is NOT_TRIGGERED
(problem definitively gone). A NOT_EVALUATED rule NEVER resolves a signal.

Pure DB reconciliation: no API, no Decision bridge, no measurement, no AI, no
marketplace-specific code. Flush-only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.advertising_signal import AdvertisingSignal
from .evaluation import RuleResult
from .signal_builder import build_signal

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


def evidence_hash(evidence: Optional[Mapping[str, object]]) -> str:
    return hashlib.sha256(
        json.dumps(evidence or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _apply_draft(sig: AdvertisingSignal, draft, *, evh, audit_id, problem_id, now, status):
    sig.audit_id = audit_id
    sig.problem_id = problem_id
    sig.what = draft.what
    sig.why = draft.why
    sig.meaning = draft.meaning
    sig.what_to_do = draft.what_to_do
    sig.expected_effect = draft.expected_effect
    sig.recommended_action_key = draft.recommended_action_key
    sig.alternative_action_keys = json.dumps(list(draft.alternative_action_keys))
    sig.priority_level = draft.priority_level
    sig.expected_effect_type = draft.expected_effect_type
    sig.effect_band = draft.effect_band
    sig.confidence = draft.confidence
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new_signal(*, audit_id, user_id, listing_id, marketplace, sku, draft, evh, problem_id, now):
    return AdvertisingSignal(
        audit_id=audit_id, problem_id=problem_id, user_id=user_id, listing_id=listing_id,
        marketplace=marketplace, sku=sku, signal_key=draft.signal_key,
        insight_key=draft.insight_key, problem_type=draft.problem_type,
        recommended_action_key=draft.recommended_action_key,
        alternative_action_keys=json.dumps(list(draft.alternative_action_keys)),
        what=draft.what, why=draft.why, meaning=draft.meaning, what_to_do=draft.what_to_do,
        expected_effect=draft.expected_effect, priority_level=draft.priority_level,
        expected_effect_type=draft.expected_effect_type, effect_band=draft.effect_band,
        confidence=draft.confidence, status=ACTIVE, evidence_hash=evh,
        created_at=now, updated_at=now,
    )


async def reconcile_signals(
    db: AsyncSession, *, user_id: str, listing_id, audit_id: str, marketplace, sku,
    evaluations, problem_id_by_type: Mapping[str, str], now: datetime,
) -> ReconcileResult:
    """Reconcile this audit's outcomes against existing signals by insight_key."""
    rows = (await db.execute(select(AdvertisingSignal).where(
        AdvertisingSignal.user_id == user_id, AdvertisingSignal.listing_id == listing_id))).scalars().all()
    by_key = {r.insight_key: r for r in rows}
    res = ReconcileResult()

    for e in evaluations:
        ikey = f"adv_{e.problem_type}:{marketplace or 'unknown'}:{sku or 'unknown'}"
        sig = by_key.get(ikey)

        if e.result == RuleResult.TRIGGERED:
            evh = evidence_hash(e.evidence)
            draft = build_signal(e, marketplace=marketplace, sku=sku)
            pid = problem_id_by_type.get(e.problem_type)
            if sig is None:
                new = _new_signal(audit_id=audit_id, user_id=user_id, listing_id=listing_id,
                                  marketplace=marketplace, sku=sku, draft=draft,
                                  evh=evh, problem_id=pid, now=now)
                db.add(new); by_key[ikey] = new; res.created += 1
            elif sig.status == RESOLVED:
                _apply_draft(sig, draft, evh=evh, audit_id=audit_id, problem_id=pid,
                             now=now, status=REOPENED); res.reopened += 1
            elif sig.status == DISMISSED:
                if sig.evidence_hash != evh:
                    _apply_draft(sig, draft, evh=evh, audit_id=audit_id, problem_id=pid,
                                 now=now, status=REOPENED); res.reopened += 1
                else:
                    res.unchanged += 1
            elif sig.status in _LIVE:
                if sig.evidence_hash != evh:
                    _apply_draft(sig, draft, evh=evh, audit_id=audit_id, problem_id=pid,
                                 now=now, status=sig.status); res.updated += 1
                else:
                    res.unchanged += 1
            else:  # promoted_to_decision — Decision owns its life
                res.unchanged += 1

        elif e.result == RuleResult.NOT_TRIGGERED:
            if sig is not None and sig.status in _LIVE:
                sig.status = RESOLVED; sig.audit_id = audit_id; sig.updated_at = now
                res.resolved += 1
            elif sig is not None:
                res.unchanged += 1

        else:  # NOT_EVALUATED — unknown, never resolve/touch
            if sig is not None:
                res.unchanged += 1

    await db.flush()
    return res
