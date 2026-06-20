"""
Execution → Measurement bridge (Slice 3: OPEN only).

After a real executor success, open a still_open DecisionOutcome for an honestly
measurable action. Opening only — no close, no attribution, no effect calc.

Honest-entity gate: only actions whose payload carries a listing-grain offer_id
(set_price, update_card). ad_set_bid / ad_set_state are blocked because
campaign_id is not a listing entity — there is no honest entity_id for them.

Baseline honesty is owned downstream by decision_measurement + metric_reader:
when the metric is unreadable (no adapter / capability gap), measurement opens
still_open with a NULL baseline — never a fabricated value.

Token is resolved server-side from existing credentials (MarketplaceConnection +
ApiCredential + credential_vault). It is never accepted over HTTP and never
returned. Best-effort: the normal skip paths return None (the caller swallows
exceptions); a successful open returns the DecisionOutcome.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.api_credential import ApiCredential
from models.decision import Decision
from models.decision_outcome import DecisionOutcome
from models.marketplace_connection import MarketplaceConnection
from services import decision_measurement
from services.marketplace import credential_vault

log = logging.getLogger(__name__)

# Honest listing-grain actions only. ad_set_bid / ad_set_state excluded:
# campaign_id is not a listing entity → no honest entity_id. The metric a
# Decision is measured on comes from its action_key (action_metric_binding).
_MEASURABLE_ACTIONS = frozenset({"set_price", "update_card", "reduce_discount", "stop_auto_promotion"})

# Required API scope per measurable action (mirrors
# services/marketplace/action_catalog.py ActionSpec.required_scope). Kept local
# to avoid importing the marketplace client modules into this service.
_ACTION_SCOPE = {"set_price": "prices", "update_card": "content", "reduce_discount": "prices",
                 "stop_auto_promotion": "promotions"}

DEFAULT_WINDOW_DAYS = 7


async def _resolve_token(
    db: AsyncSession, user_id: str, marketplace: str, scope: str
) -> Optional[str]:
    """Server-side token resolution. None when no connected credential exists."""
    conn = (
        await db.execute(
            select(MarketplaceConnection).where(
                MarketplaceConnection.user_id == user_id,
                MarketplaceConnection.marketplace == marketplace,
            )
        )
    ).scalars().first()
    if conn is None or conn.status != "connected":
        return None
    cred = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.connection_id == conn.id,
                ApiCredential.scope == scope,
            )
        )
    ).scalars().first()
    if cred is None:
        return None
    return credential_vault.decrypt(cred.secret_enc)


async def open_measurement_for_execution(
    db: AsyncSession,
    *,
    user_id: str,
    decision_id: Optional[str],
    action_key: Optional[str],
    marketplace: Optional[str],
    entity_id: Optional[str],
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Optional[DecisionOutcome]:
    """
    Open measurement for a just-executed Decision. Returns the opened (or
    existing) still_open DecisionOutcome, or None for any honest skip:
    non-measurable action, missing decision_id/entity_id/marketplace, decision
    not found, or no resolvable credential. Idempotent via open_measurement.
    """
    # honest-entity / measurable gate
    if not decision_id or action_key not in _MEASURABLE_ACTIONS:
        return None
    if not entity_id:          # no offer_id → never fabricate a listing entity
        return None
    if not marketplace:
        return None

    decision = (
        await db.execute(
            select(Decision).where(
                Decision.id == decision_id, Decision.user_id == user_id
            )
        )
    ).scalars().first()
    if decision is None:
        return None

    token = await _resolve_token(db, user_id, marketplace, _ACTION_SCOPE[action_key])
    if token is None:          # no credential → skip; never fake a read
        return None

    # Metric is derived from decision.action_key inside open_measurement; baseline
    # honesty (real vs null) is owned there + by metric_reader. Idempotent.
    return await decision_measurement.open_measurement(
        db,
        decision=decision,
        entity_id=entity_id,
        marketplace=marketplace,
        window_days=window_days,
        token=token,
    )
