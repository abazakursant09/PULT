"""
Pricing Signal Reconciliation (A3-pre) — give pricing signals memory + lifecycle.

Instead of creating a fresh signal every run, reconcile the current run's rule
outcomes against existing signals for the same (user, marketplace, sku), keyed by the
canonical insight_key `pricing_<problem_type>:<marketplace>:<sku>`. One signal row per
insight_key — a duplicate live signal is structurally impossible.

Lifecycle: active | dismissed | promoted_to_decision | resolved | reopened.

Honesty rule: a signal is RESOLVED only when its rule ran and is NOT_TRIGGERED.
A NOT_EVALUATED rule NEVER resolves a signal — a missing threshold/field does not
mean the margin problem disappeared.

Pure DB reconciliation: no API, no Decision bridge, no measurement, no AI, no
forecast. Marketplace-isolated (queries scoped to one canonical marketplace's sku).
Flush-only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.pricing_signal import PricingSignal
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


def _apply_draft(sig: PricingSignal, draft, *, evh, now, status):
    sig.category = draft.category
    sig.what = draft.what
    sig.why = draft.why
    sig.meaning = draft.meaning
    sig.what_to_do = draft.what_to_do
    sig.expected_effect = draft.expected_effect
    sig.recommended_action_key = draft.recommended_action_key
    sig.priority_level = draft.priority_level
    sig.effect_type = draft.effect_type
    sig.effect_band = draft.effect_band
    sig.confidence = draft.confidence
    sig.evidence_hash = evh
    sig.status = status
    sig.updated_at = now


def _new_signal(*, user_id, listing_id, marketplace, sku, draft, evh, now):
    return PricingSignal(
        user_id=user_id, listing_id=listing_id, marketplace=marketplace, sku=sku,
        signal_key=draft.signal_key, insight_key=draft.insight_key,
        problem_type=draft.problem_type, category=draft.category,
        recommended_action_key=draft.recommended_action_key,
        what=draft.what, why=draft.why, meaning=draft.meaning, what_to_do=draft.what_to_do,
        expected_effect=draft.expected_effect, priority_level=draft.priority_level,
        effect_type=draft.effect_type, effect_band=draft.effect_band,
        confidence=draft.confidence, status=ACTIVE, evidence_hash=evh,
        created_at=now, updated_at=now,
    )


async def reconcile_signals(
    db: AsyncSession, *, user_id: str, listing_id, marketplace, sku, evaluations,
    now: datetime,
) -> ReconcileResult:
    """Reconcile this run's outcomes against existing signals by insight_key, scoped
    to the (user, marketplace, sku) — marketplace-isolated, one row per insight_key."""
    rows = (await db.execute(select(PricingSignal).where(
        PricingSignal.user_id == user_id,
        PricingSignal.marketplace == marketplace,
        PricingSignal.sku == str(sku) if sku is not None else PricingSignal.sku.is_(None),
    ))).scalars().all()
    by_key = {r.insight_key: r for r in rows}
    res = ReconcileResult()

    for e in evaluations:
        ikey = f"pricing_{e.problem_type}:{marketplace or 'unknown'}:{sku or 'unknown'}"
        sig = by_key.get(ikey)

        if e.result == RuleResult.TRIGGERED:
            evh = evidence_hash(e.evidence)
            draft = build_signal(e, marketplace=marketplace, sku=sku)
            if sig is None:
                new = _new_signal(user_id=user_id, listing_id=listing_id,
                                  marketplace=marketplace, sku=sku, draft=draft, evh=evh, now=now)
                db.add(new); by_key[ikey] = new; res.created += 1
            elif sig.status == RESOLVED:
                _apply_draft(sig, draft, evh=evh, now=now, status=REOPENED); res.reopened += 1
            elif sig.status == DISMISSED:
                if sig.evidence_hash != evh:
                    _apply_draft(sig, draft, evh=evh, now=now, status=REOPENED); res.reopened += 1
                else:
                    res.unchanged += 1
            elif sig.status in _LIVE:
                if sig.evidence_hash != evh:
                    _apply_draft(sig, draft, evh=evh, now=now, status=sig.status); res.updated += 1
                else:
                    res.unchanged += 1
            else:  # promoted_to_decision — Decision owns its life
                res.unchanged += 1

        elif e.result == RuleResult.NOT_TRIGGERED:
            if sig is not None and sig.status in _LIVE:
                sig.status = RESOLVED; sig.updated_at = now; res.resolved += 1
            elif sig is not None:
                res.unchanged += 1

        else:  # NOT_EVALUATED — unknown, never resolve/touch
            if sig is not None:
                res.unchanged += 1

    await db.flush()
    return res
