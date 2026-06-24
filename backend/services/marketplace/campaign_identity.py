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


async def resolve_campaign_identity(
    marketplace,
    *,
    listing_id=None,
    sku=None,
    nm_id=None,
    token=None,
    db=None,
) -> Union[CampaignIdentity, CampaignUnavailable]:
    """listing / sku / nmID → campaign identity, or an honest unavailable reason.

    Marketplace-isolated: only the WB branch touches the WB client; ozon / yandex /
    megamarket / unknown return their reason WITHOUT any adapter call (no cross-read,
    no blending)."""
    mp = normalize_marketplace(marketplace)

    if mp == "wb":
        return await _resolve_wb(listing_id=listing_id, sku=sku, nm_id=nm_id, token=token)
    if mp == "ozon":
        # Ozon advertising = Performance API (separate OAuth) — adapter present but unwired.
        return CampaignUnavailable("ozon", NOT_IMPLEMENTED,
                                   "ozon performance campaign read not implemented")
    if mp == "yandex":
        return CampaignUnavailable("yandex", NO_ADAPTER, "no yandex campaign adapter")

    # megamarket is not even a canonical marketplace code, and any unknown input.
    return CampaignUnavailable(
        (marketplace or "").lower() or None, NO_ADAPTER,
        "no campaign adapter for this marketplace")
