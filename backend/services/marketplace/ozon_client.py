"""
Ozon client. Ozon Seller API uses headers Api-Key + Client-Id. Prices/Content
are on api-seller.ozon.ru; Reviews and Advertising (Performance API) use a
separate base + OAuth (Client-Id/Client-Secret) — deferred. Unimplemented
methods raise (never a silent local no-op).
"""
from __future__ import annotations

from config import settings
from .base_client import BaseMarketplaceClient
from .errors import ExecutionError

MARKETPLACE = "ozon"


class OzonClient:
    def __init__(self):
        self._seller = BaseMarketplaceClient(settings.ozon_seller_base)

    def _headers(self, client_id: str | None) -> dict:
        if not client_id:
            raise ExecutionError(ExecutionError.AUTH, "Ozon requires ozon_client_id on the connection")
        return {"Client-Id": str(client_id)}

    # ── Prices (ME-3) ─────────────────────────────────────────────────────────
    async def set_price(self, *, token: str, client_id: str | None,
                        offer_id: str, price: float) -> dict:
        """
        Ozon: POST /v1/product/import/prices. Api-Key passed via Authorization
        (base_client sets it); Client-Id is an extra header. Price is a string.
        """
        return await self._seller.request(
            "POST", "/v1/product/import/prices", token=token,
            auth_header="Api-Key", extra_headers=self._headers(client_id),
            json={"prices": [{"offer_id": str(offer_id), "price": str(int(round(price)))}]},
        )

    async def set_auto_promotion(self, *, token: str, client_id: str | None,
                                 offer_id: str, enabled: bool) -> dict:
        """
        Ozon promotions participation (A3 stop_auto_promotion). enabled=False
        deactivates the product in actions.
            POST /v1/actions/products/{activate|deactivate}
        """
        path = "/v1/actions/products/activate" if enabled else "/v1/actions/products/deactivate"
        return await self._seller.request(
            "POST", path, token=token, auth_header="Api-Key",
            extra_headers=self._headers(client_id),
            json={"product_ids": [str(offer_id)]},
        )

    # ── Advertising / Performance (campaign state control, A2.2-pre-b.4) ───────
    async def set_campaign_state(self, *, token: str, campaign_id, action: str) -> dict:
        """Ozon Performance API campaign state. `token` is a Performance OAuth bearer
        (acquired by ozon_performance_auth; never logged). `action` ∈ activate|deactivate.
            POST /api/client/campaign/{id}/activate|deactivate
        """
        if action not in ("activate", "deactivate"):
            raise ExecutionError(ExecutionError.VALIDATION,
                                 "ozon campaign action must be 'activate' or 'deactivate'")
        path = f"/api/client/campaign/{int(campaign_id)}/{action}"
        return await self._performance().request(
            "POST", path, token=f"Bearer {token}", auth_header="Authorization")

    async def list_campaigns_for_sku(self, *, token: str, sku=None) -> list[dict]:
        """Ozon Performance API campaign read (campaign identity, A2.2-pre-a.2).

        `token` is a Performance OAuth bearer (acquired by ozon_performance_auth;
        never logged). Returns ONLY the campaigns observed from the API, normalized;
        never a fabricated campaign and never a guessed listing relation.

        Endpoint:
            GET /api/client/campaign   ->  {"list": [ {id|campaignId, advObjectType|type,
                                                        state|status, ... } ]}

        TODO(ozon-perf-docs): the exact campaigns-list path, the campaign→product
        (sku) membership field, and per-campaign objects endpoint are NOT yet
        confirmed against Ozon Performance docs. This seam normalizes defensively
        and surfaces the sku relation ONLY when the response actually carries it;
        the resolver treats a missing relation as honest-unavailable (never a guess).
        `sku` is accepted for a future server-side filter; today filtering is the
        resolver's job over the observed relation.
        """
        data = await self._performance().request(
            "GET", "/api/client/campaign", token=f"Bearer {token}", auth_header="Authorization")
        rows = data.get("list") if isinstance(data, dict) else data
        rows = rows if isinstance(rows, list) else []
        out: list[dict] = []
        for c in rows:
            if not isinstance(c, dict):
                continue
            out.append({
                "campaign_id": c.get("id", c.get("campaignId")),
                "campaign_type": c.get("advObjectType", c.get("type")),
                "campaign_state": c.get("state", c.get("status")),
                # relation surfaced ONLY if present; otherwise None (never inferred)
                "sku": c.get("sku", c.get("offer_id")),
                "relation_present": ("sku" in c or "offer_id" in c),
            })
        return out

    def _performance(self) -> "BaseMarketplaceClient":
        # lazy: Performance API base (separate from Seller API)
        if not hasattr(self, "_performance_client"):
            self._performance_client = BaseMarketplaceClient(settings.ozon_performance_base)
        return self._performance_client

    # ── deferred ──────────────────────────────────────────────────────────────
    async def publish_feedback_answer(self, **_):  # premium Reviews API
        raise ExecutionError(
            ExecutionError.UNKNOWN_ACTION,
            "ozon.publish_feedback_answer not implemented (premium Reviews API)",
        )

    async def set_bid(self, **_):  # Performance API, separate OAuth (ME-4b)
        raise ExecutionError(
            ExecutionError.UNKNOWN_ACTION,
            "ozon.set_bid not implemented (Performance API needs separate OAuth — ME-4b)",
        )

    async def update_card(self, **_):  # ME-5
        raise ExecutionError(ExecutionError.UNKNOWN_ACTION, "ozon.update_card not implemented (ME-5)")


ozon_client = OzonClient()
