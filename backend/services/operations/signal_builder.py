"""
Operations signal producer (Slice 1) — auto-promotion margin drain.

OBSERVED-ONLY, deterministic. Creates one `operations_signal` row ONLY when ALL of
these observed conditions hold:

  1. marketplace is Ozon (canonical "ozon");
  2. the listing participates in an auto-promotion — an OBSERVED fact passed in by
     the caller (Ozon `auto_promotions` read; the marketplace read itself is wired in
     a later slice — this builder never fetches, never guesses);
  3. observed net_profit < 0 — a point value, NOT a trend / forecast;
  4. a listing identity exists (sku) so a later offer_id payload is derivable.

If any condition fails → NO signal (returns None). Other marketplaces never produce
a signal here:
  * WB        — auto-promotion participation is not observable (capability impossible);
  * Yandex    — promotions are not available (honest unavailable);
  * Megamarket — out of scope.

No forecast, no AI, no competitor data, no compute_recommendation, no fabricated
number. The five doctrine text fields are deterministic and carry no numbers.

Slice 1 scope: produce the observed signal only. Promotion to Candidate / Decision
is handled by the generic Decision-Outcome path (snapshot → candidate → bridge).
No Apply, no Effect, no Learning wiring here.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.operations_signal import OperationsSignal
from services.product_resolver import normalize_marketplace

SIGNAL_KEY = "operations_auto_promo_margin_drain"
PROBLEM_TYPE = "auto_promo_margin_drain"
_CANON_OZON = "ozon"

# deterministic doctrine text — no numbers, no fabrication
_WHAT = "Товар участвует в авто-акции при отрицательной марже."
_WHY = "Авто-акция применяется к позиции, которая по наблюдаемым финансам убыточна."
_MEANING = "Участие в авто-акции усиливает убыток вместо прибыли."
_WHAT_TO_DO = "Остановить участие товара в авто-акции."
_EXPECTED = "После остановки авто-акции наблюдаемая маржа по товару может перестать ухудшаться."


def _evidence_hash(user_id: str, marketplace: str, sku: str) -> str:
    """Deterministic change-detection hash over the problem identity (not the
    fluctuating net_profit value)."""
    raw = f"{user_id}|{marketplace}|{sku}|{SIGNAL_KEY}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def build_operations_signal(
    db: AsyncSession, *, user_id: str, marketplace: Optional[str], sku: Optional[str],
    net_profit: Optional[float], in_auto_promotion: bool,
    listing_id: Optional[str] = None, now: Optional[datetime] = None,
) -> Optional[OperationsSignal]:
    """Create (or return the existing active) operations auto-promo margin-drain
    signal when the observed conditions hold; else None. Idempotent per
    (user_id, insight_key)."""
    # (1) Ozon only — every other marketplace produces no signal (honest unavailable)
    if not marketplace or normalize_marketplace(marketplace) != _CANON_OZON:
        return None
    # (2) observed auto-promotion participation
    if not in_auto_promotion:
        return None
    # (3) observed point-value loss — not a trend, not a forecast
    if net_profit is None or net_profit >= 0:
        return None
    # (4) identity for a later offer_id payload
    if not sku:
        return None

    insight_key = f"{SIGNAL_KEY}:{_CANON_OZON}:{sku}"

    # idempotent: do not duplicate a live signal for this instance — a row that is
    # already active / promoted / reopened counts (resolved & dismissed do not).
    existing = (await db.execute(select(OperationsSignal).where(
        OperationsSignal.user_id == user_id,
        OperationsSignal.insight_key == insight_key,
        OperationsSignal.status.in_(("active", "promoted_to_decision", "reopened")),
    ))).scalars().first()
    if existing is not None:
        return existing

    row = OperationsSignal(
        user_id=user_id, listing_id=listing_id, marketplace=_CANON_OZON, sku=sku,
        signal_key=SIGNAL_KEY, insight_key=insight_key, problem_type=PROBLEM_TYPE,
        category="operations",
        what=_WHAT, why=_WHY, meaning=_MEANING, what_to_do=_WHAT_TO_DO, expected_effect=_EXPECTED,
        status="active", evidence_hash=_evidence_hash(user_id, _CANON_OZON, sku),
        created_at=now or datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    return row
