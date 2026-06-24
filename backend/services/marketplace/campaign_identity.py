"""
Campaign identity resolver (Action Coverage Expansion A2.2-pre-a).

Resolves a listing / sku / nmID to its advertising campaign identity through the
Marketplace Adapter Spine, marketplace-isolated, with an HONEST unavailable state.
Advertising actions (ad_set_state, ad_set_bid) need a real ``campaign_id`` +
campaign state; today only the WB adapter can read campaigns. The other
marketplaces return an explicit unavailable reason — never a guessed or inferred
campaign.

Doctrine: no guessed campaign_id, no inferred campaign_id, no fake mappings, no
WB-only binding (every marketplace is a first-class branch returning either a real
observed identity or an honest reason). Pure read seam — no DB write, no schema, no
executor, no binding, no auto-pick.

This module ONLY creates the read seam. Nothing wires it into action_binding,
executor, Decision, Apply, measurement, or Learning OS yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from .metric_catalog import normalize_marketplace
from .wb_client import wb_client
from .ozon_client import ozon_client
from . import ozon_performance_auth
from .errors import ExecutionError

# ── unavailable reasons ─────────────────────────────────────────────────────
NO_ADAPTER = "no_adapter"                       # marketplace has no campaign adapter at all
NOT_IMPLEMENTED = "not_implemented"             # adapter exists but campaign read unwired
NO_SCOPE = "no_scope"                           # no advert credential / token
NO_CAMPAIGN_FOR_LISTING = "no_campaign_for_listing"   # adapter read returned zero campaigns
AMBIGUOUS_MULTIPLE = "ambiguous_multiple"       # >1 campaign — never auto-pick
INVALID_LISTING_IDENTITY = "invalid_listing_identity"  # no usable nm identity to read with


@dataclass(frozen=True)
class CampaignIdentity:
    marketplace: str
    campaign_id: object             # observed from adapter; type as the API returns it
    campaign_type: Optional[object]
    campaign_state: Optional[object]
    source: str                     # adapter that produced it (e.g. "wb_advert_api")


@dataclass(frozen=True)
class CampaignUnavailable:
    marketplace: Optional[str]
    reason: str
    detail: Optional[str] = None


def _nm_identity(nm_id, sku) -> Optional[int]:
    """A WB campaign read needs a numeric nmID. Accept an explicit nm_id, else a
    numeric sku (WB listing external_id IS the nmID). Never invent one."""
    for candidate in (nm_id, sku):
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


async def _resolve_wb(*, listing_id, sku, nm_id, token) -> Union[CampaignIdentity, CampaignUnavailable]:
    if not token:
        return CampaignUnavailable("wb", NO_SCOPE, "no advert token")
    nm = _nm_identity(nm_id, sku)
    if nm is None:
        return CampaignUnavailable("wb", INVALID_LISTING_IDENTITY,
                                   "no numeric nmID from nm_id/sku")
    adverts = await wb_client.list_adverts_for_nm(token=token, nm_id=nm)
    if not adverts:
        return CampaignUnavailable("wb", NO_CAMPAIGN_FOR_LISTING,
                                   f"no WB advert targets nm {nm}")
    if len(adverts) > 1:
        return CampaignUnavailable("wb", AMBIGUOUS_MULTIPLE,
                                   f"{len(adverts)} WB adverts target nm {nm}; will not auto-pick")
    adv = adverts[0]
    return CampaignIdentity(
        marketplace="wb",
        campaign_id=adv.get("campaign_id"),
        campaign_type=adv.get("campaign_type"),
        campaign_state=adv.get("campaign_state"),
        source="wb_advert_api",
    )


async def _resolve_ozon(*, listing_id, sku, connection_id, db) -> Union[CampaignIdentity, CampaignUnavailable]:
    """Ozon campaign identity via Performance API + OAuth bearer.

    Bearer is resolved from the advert_performance credential (acquire_bearer); a
    missing credential is honest NO_SCOPE. The campaign→listing relation is used
    ONLY when the API actually carries it — if no observed campaign exposes the
    relation, we return NOT_IMPLEMENTED rather than guess which campaign owns the
    sku. Never auto-picks among multiple matches."""
    if db is None or not connection_id:
        return CampaignUnavailable("ozon", NO_SCOPE, "no db/connection_id for performance credential")
    if sku is None:
        return CampaignUnavailable("ozon", INVALID_LISTING_IDENTITY, "no sku to relate campaigns")
    try:
        bearer = await ozon_performance_auth.acquire_bearer(db, connection_id=connection_id)
    except ExecutionError:
        # controlled, secret-free: missing/partial advert_performance credential
        return CampaignUnavailable("ozon", NO_SCOPE, "no advert_performance credential")

    campaigns = await ozon_client.list_campaigns_for_sku(token=bearer, sku=sku)
    if campaigns and not any(c.get("relation_present") for c in campaigns):
        # API returned campaigns but none carry a listing relation → cannot relate honestly
        return CampaignUnavailable("ozon", NOT_IMPLEMENTED,
                                   "ozon campaigns carry no listing relation; cannot map sku")
    matched = [c for c in campaigns
               if c.get("relation_present") and str(c.get("sku")) == str(sku)]
    if not matched:
        return CampaignUnavailable("ozon", NO_CAMPAIGN_FOR_LISTING,
                                   f"no ozon campaign relates to sku {sku}")
    if len(matched) > 1:
        return CampaignUnavailable("ozon", AMBIGUOUS_MULTIPLE,
                                   f"{len(matched)} ozon campaigns relate to sku {sku}; will not auto-pick")
    c = matched[0]
    return CampaignIdentity(
        marketplace="ozon",
        campaign_id=c.get("campaign_id"),
        campaign_type=c.get("campaign_type"),
        campaign_state=c.get("campaign_state"),
        source="ozon_performance_api",
    )


async def resolve_campaign_identity(
    marketplace,
    *,
    listing_id=None,
    sku=None,
    nm_id=None,
    token=None,
    db=None,
    connection_id=None,
) -> Union[CampaignIdentity, CampaignUnavailable]:
    """listing / sku / nmID → campaign identity, or an honest unavailable reason.

    Marketplace-isolated: the WB branch touches ONLY the WB client; the Ozon branch
    touches ONLY the Ozon client (via the Performance bearer); yandex / megamarket /
    unknown return their reason WITHOUT any adapter call (no cross-read, no blending)."""
    mp = normalize_marketplace(marketplace)

    if mp == "wb":
        return await _resolve_wb(listing_id=listing_id, sku=sku, nm_id=nm_id, token=token)
    if mp == "ozon":
        return await _resolve_ozon(listing_id=listing_id, sku=sku,
                                   connection_id=connection_id, db=db)
    if mp == "yandex":
        return CampaignUnavailable("yandex", NO_ADAPTER, "no yandex campaign adapter")

    # megamarket is not even a canonical marketplace code, and any unknown input.
    return CampaignUnavailable(
        (marketplace or "").lower() or None, NO_ADAPTER,
        "no campaign adapter for this marketplace")
