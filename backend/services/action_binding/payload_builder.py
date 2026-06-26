"""
Action Payload Builder (Action Catalog Expansion A4) — safe construction of a
valid executor payload for a BOUND signal type. Read-only.

It NEVER executes, NEVER generates text/content, NEVER guesses a value, NEVER
fabricates a payload. It only assembles a payload whose every field is DERIVABLE
from existing PULT data or a READ-ONLY marketplace lookup (campaign identity):

  the indirect stock/listing advertising types →
    action_key = stop_auto_promotion
    payload    = {"offer_id": listing.external_id}   (sku → ProductListing → external_id)

  the direct-overspend advertising types →
    action_key = ad_set_state
    payload    = {"campaign_id": <resolver single match>, "action": "pause"}
    (campaign_id ONLY from resolve_campaign_identity, single unambiguous match;
     a read-only call, never an execution and never a guessed id)

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
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.product import Product
from models.pricing_rule import PricingRule
from services.product_resolver import normalize_marketplace, normalize_sku
from services.marketplace import credential_vault
from services.marketplace.campaign_identity import (
    resolve_campaign_identity, CampaignIdentity,
)
from services.action_binding.registry import (
    BY_SIGNAL_TYPE, BOUND, NO_CATALOG_ACTION, PAYLOAD_NOT_DERIVABLE,
)

REASON_NO_BINDING = "no_binding"
REASON_PAYLOAD_NOT_DERIVABLE = "payload_not_derivable"

# canonical code → MarketplaceConnection.marketplace label.
_CONNECTION_MP = {"wb": "wildberries", "ozon": "ozon", "yandex": "yandex"}

# allowed payload keys per action — the builder will never emit anything else.
_ALLOWED_FIELDS = {
    "stop_auto_promotion": {"offer_id"},
    "ad_set_state": {"campaign_id", "action"},
    "set_price": {"offer_id", "price", "old_price"},
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

    if b.action_key == "ad_set_state":
        return await _build_ad_set_state(db, user_id=user_id, marketplace=marketplace, sku=sku)

    if b.action_key == "set_price":
        return await _build_set_price(db, user_id=user_id, marketplace=marketplace, sku=sku)

    # bound to an action with no builder yet → honest payload_not_derivable
    return PayloadBuildResult(ok=False, action_key=b.action_key,
                              reason=REASON_PAYLOAD_NOT_DERIVABLE)


async def _build_set_price(db: AsyncSession, *, user_id: str, marketplace: str,
                           sku: Optional[str]) -> PayloadBuildResult:
    """set_price floor-restore payload (A3-bind, pricing_price_below_floor).

    price comes ONLY from the seller's PricingRule.min_price — observed, rule-defined,
    deterministic. NEVER compute_recommendation, NEVER competitor prices, NEVER a
    forecast/cost-plus/AI/guess. payload_not_derivable when the listing, the pricing
    rule, or a positive min_price is missing."""
    offer_id = await _resolve_external_id(db, user_id, marketplace, sku)
    if not offer_id:
        return PayloadBuildResult(ok=False, action_key="set_price",
                                  reason=REASON_PAYLOAD_NOT_DERIVABLE)
    sku_n = normalize_sku(sku)
    # min_price (the floor) + current price, both from the seller's own stored data
    row = (await db.execute(
        select(PricingRule.min_price, Product.price).join(
            Product, Product.id == PricingRule.product_id).where(
            Product.user_id == user_id, func.upper(Product.sku) == sku_n))).first()
    if row is None:
        return PayloadBuildResult(ok=False, action_key="set_price",
                                  reason=REASON_PAYLOAD_NOT_DERIVABLE)
    min_price, current_price = row
    try:
        if min_price is None or float(min_price) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return PayloadBuildResult(ok=False, action_key="set_price",
                                  reason=REASON_PAYLOAD_NOT_DERIVABLE)
    payload = {"offer_id": offer_id, "price": float(min_price)}
    if current_price is not None:
        payload["old_price"] = float(current_price)   # for revert only
    assert set(payload) <= _ALLOWED_FIELDS["set_price"]   # only allowed fields
    return PayloadBuildResult(ok=True, action_key="set_price", payload=payload)


async def _resolve_connection(db: AsyncSession, user_id: str, marketplace: str):
    """user + marketplace → MarketplaceConnection (or None)."""
    mp = normalize_marketplace(marketplace)
    label = _CONNECTION_MP.get(mp, mp)
    return (await db.execute(select(MarketplaceConnection).where(
        MarketplaceConnection.user_id == user_id,
        MarketplaceConnection.marketplace == label))).scalars().first()


async def _wb_advert_token(db: AsyncSession, connection_id: str) -> Optional[str]:
    """WB advert credential (scope='advert') → decrypted token, else None."""
    cred = (await db.execute(select(ApiCredential).where(
        ApiCredential.connection_id == connection_id,
        ApiCredential.scope == "advert"))).scalars().first()
    return credential_vault.decrypt(cred.secret_enc) if cred is not None else None


async def _build_ad_set_state(db: AsyncSession, *, user_id: str, marketplace: str,
                              sku: Optional[str]) -> PayloadBuildResult:
    """ad_set_state pause payload: {campaign_id (resolver, single match), action:'pause'}.

    campaign_id comes ONLY from resolve_campaign_identity, ONLY on a single unambiguous
    match. Zero / multiple / no-relation / no-credential / unsupported marketplace →
    ok=False with the resolver's EXACT reason. Never guesses, never auto-picks, never
    uses seller text or AI."""
    mp = normalize_marketplace(marketplace)
    if mp not in ("wb", "ozon"):
        # Yandex / Megamarket / unknown — honest unsupported (resolver would say no_adapter)
        return PayloadBuildResult(ok=False, action_key="ad_set_state", reason="no_adapter")

    conn = await _resolve_connection(db, user_id, marketplace)
    connection_id = conn.id if conn is not None else None
    token = await _wb_advert_token(db, connection_id) if (mp == "wb" and connection_id) else None

    identity = await resolve_campaign_identity(
        marketplace, sku=sku, token=token, db=db, connection_id=connection_id)
    if isinstance(identity, CampaignIdentity):
        payload = {"campaign_id": identity.campaign_id, "action": "pause"}
        assert set(payload) <= _ALLOWED_FIELDS["ad_set_state"]   # only allowed fields
        return PayloadBuildResult(ok=True, action_key="ad_set_state", payload=payload)
    # CampaignUnavailable → not derivable; surface the exact reason
    return PayloadBuildResult(ok=False, action_key="ad_set_state", reason=identity.reason)
