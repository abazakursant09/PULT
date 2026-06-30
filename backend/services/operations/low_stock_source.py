"""
Low Stock source (Operations contour, NARROW producer) — observed-only, deterministic.

The FIRST canonical producer for a legacy `_compute_insights` signal (low_stock,
action_engine Rule 5 / finance-less path). It runs ALONGSIDE the legacy on-read logic
— it does NOT replace it and does NOT touch _compute_insights.

Creates one `operations_low_stock` signal row when the OBSERVED stock is critically
low — the SAME definition the legacy path uses (0 <= stock <= 5) and the SAME unit
threshold already used across the project (services/growth/threshold_source
_LOW_STOCK_UNITS, advertising/growth low_stock_units). `stock` is a point value read
from already-imported PULT data (ImportedProductRow) — never a marketplace fetch,
never a forecast, never a fabricated number.

Deliberately NARROW (`operations_low_stock`, not a generic `operations` bucket) so
future operations producers — ready_to_ship, supply_risk, warehouse, orders — are
each their own producer, not dumped into one.

Advisory-only: writes operations_signal rows only, with recommended_action_key=None.
The signal_key `operations_low_stock` is NOT registered in the Decision-Outcome
canonical set and binds to no executor, so it can never promote to a Decision, an
Apply, an executor call, or a marketplace write. SELECT + flush only — never commits.
Idempotent per (user_id, insight_key).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.operations_signal import OperationsSignal
from services.product_resolver import normalize_marketplace

SIGNAL_KEY = "operations_low_stock"
PROBLEM_TYPE = "low_stock"

# Fixed DEFINITION of "low stock" (units), NOT a finance-derived number. Mirrors the
# legacy `0 <= stock <= 5` (action_engine) and the project-wide low_stock unit (5).
LOW_STOCK_UNITS = 5

# deterministic doctrine text — no numbers, no fabrication
_WHAT = "Остаток товара на складе критически низкий."
_WHY = "Наблюдаемый остаток находится на уровне, при котором товар скоро закончится."
_MEANING = "При выходе товара из наличия теряются позиции в поиске и продажи."
_WHAT_TO_DO = "Пополнить остаток товара на складе."
_EXPECTED = "После пополнения остатка риск потери позиций из-за out-of-stock снижается."


def _evidence_hash(user_id: str, marketplace: str, sku: str) -> str:
    """Deterministic change-detection hash over the problem IDENTITY (not the
    fluctuating stock value)."""
    raw = f"{user_id}|{marketplace}|{sku}|{SIGNAL_KEY}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def build_low_stock_signal(
    db: AsyncSession, *, user_id: str, marketplace: Optional[str], sku: Optional[str],
    stock: Optional[int], listing_id: Optional[str] = None, now: Optional[datetime] = None,
) -> Optional[OperationsSignal]:
    """Create (or return the existing live) low-stock signal when the observed stock
    is critically low (0 <= stock <= LOW_STOCK_UNITS); else None. Idempotent per
    (user_id, insight_key)."""
    # identity required for the canonical insight_key
    if not marketplace or not sku:
        return None
    # observed point-value low stock — same definition as the legacy path
    if stock is None or not (0 <= stock <= LOW_STOCK_UNITS):
        return None

    mp = normalize_marketplace(marketplace)
    insight_key = f"{SIGNAL_KEY}:{mp}:{sku}"

    # idempotent: a row already active / promoted / reopened counts as the live signal
    # (resolved & dismissed do not) → never a duplicate live signal per insight_key.
    existing = (await db.execute(select(OperationsSignal).where(
        OperationsSignal.user_id == user_id,
        OperationsSignal.insight_key == insight_key,
        OperationsSignal.status.in_(("active", "promoted_to_decision", "reopened")),
    ))).scalars().first()
    if existing is not None:
        return existing

    row = OperationsSignal(
        user_id=user_id, listing_id=listing_id, marketplace=mp, sku=sku,
        signal_key=SIGNAL_KEY, insight_key=insight_key, problem_type=PROBLEM_TYPE,
        category="operations",
        recommended_action_key=None,    # advisory-only — binds to no executor
        what=_WHAT, why=_WHY, meaning=_MEANING, what_to_do=_WHAT_TO_DO, expected_effect=_EXPECTED,
        priority_level="critical", effect_type="availability_risk", confidence=95.0,
        status="active", evidence_hash=_evidence_hash(user_id, mp, sku),
        created_at=now or datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    return row
