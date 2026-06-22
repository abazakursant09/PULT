"""
Action Payload Builder (Action Catalog Expansion A4) — safe construction of a
valid executor payload for a BOUND signal type. Read-only.

It NEVER calls a marketplace, NEVER executes, NEVER generates text/content, NEVER
guesses a value, NEVER fabricates a payload. It only assembles a payload whose
every field is DERIVABLE from existing PULT data:

  the five advertising "stop auto-promotion" types →
    action_key = stop_auto_promotion
    payload    = {"offer_id": listing.external_id}   (sku → ProductListing → external_id)

If the signal type is not bindable → ok=False, reason from the binding
(no_binding for no_catalog_action, payload_not_derivable otherwise). If a bound
type's listing cannot be resolved → ok=False, reason=payload_not_derivable. No
fictitious offer_id is ever returned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.product_listing import ProductListing
from services.product_resolver import normalize_marketplace, normalize_sku
from services.action_binding.registry import (
    BY_SIGNAL_TYPE, BOUND, NO_CATALOG_ACTION, PAYLOAD_NOT_DERIVABLE,
)

REASON_NO_BINDING = "no_binding"
REASON_PAYLOAD_NOT_DERIVABLE = "payload_not_derivable"

# allowed payload keys per action — the builder will never emit anything else.
_ALLOWED_FIELDS = {
    "stop_auto_promotion": {"offer_id"},
}


@dataclass
class PayloadBuildResult:
    ok: bool
    action_key: Optional[str]
    payload: Optional[Mapping[str, object]] = None
    reason: Optional[str] = None


async def _resolve_external_id(db: AsyncSession, user_id: str, marketplace: str,
                               sku: Optional[str]) -> Optional[str]:
    """sku → ProductListing.external_id (offer_id). None when not resolvable."""
    mp = normalize_marketplace(marketplace)
    sku_n = normalize_sku(sku)
    if not mp or sku_n is None:
        return None
    listing = (await db.execute(select(ProductListing).where(
        ProductListing.user_id == user_id,
        ProductListing.marketplace == mp,
        func.upper(ProductListing.external_id) == sku_n))).scalars().first()
    return listing.external_id if listing is not None else None


async def build_action_payload(
    db: AsyncSession, *, user_id: str, signal_type: str, marketplace: str,
    sku: Optional[str], source_context: Optional[Mapping[str, object]] = None,
) -> PayloadBuildResult:
    """Build a validated executor payload for a bound signal type. Read-only."""
    b = BY_SIGNAL_TYPE.get(signal_type)
    if b is None:
        return PayloadBuildResult(ok=False, action_key=None, reason=REASON_NO_BINDING)

    if not b.bindable or b.binding_status != BOUND or not b.action_key:
        reason = REASON_NO_BINDING if b.binding_status == NO_CATALOG_ACTION else REASON_PAYLOAD_NOT_DERIVABLE
        return PayloadBuildResult(ok=False, action_key=None, reason=reason)

    # ── bound: assemble the payload from derivable sources only ───────────────
    if b.action_key == "stop_auto_promotion":
        offer_id = await _resolve_external_id(db, user_id, marketplace, sku)
        if not offer_id:
            return PayloadBuildResult(ok=False, action_key=b.action_key,
                                      reason=REASON_PAYLOAD_NOT_DERIVABLE)
        payload = {"offer_id": offer_id}
        assert set(payload) <= _ALLOWED_FIELDS[b.action_key]   # only allowed fields
        return PayloadBuildResult(ok=True, action_key=b.action_key, payload=payload)

    # bound to an action with no builder yet → honest payload_not_derivable
    return PayloadBuildResult(ok=False, action_key=b.action_key,
                              reason=REASON_PAYLOAD_NOT_DERIVABLE)
