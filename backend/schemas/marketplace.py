from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ── Connections ────────────────────────────────────────────────────────────────
class ConnectionCreate(BaseModel):
    marketplace: str                      # wildberries | ozon
    label: Optional[str] = None
    token: str                            # raw API token — encrypted server-side, never returned
    scope: str                            # feedbacks | prices | advert | content | stocks | promotions
    ozon_client_id: Optional[str] = None
    # PULT-LAUNCH-1.4.5D: bind this connection to a cabinet the seller already created (e.g. a
    # CSV-only one). When given, the route attaches to THAT MarketplaceAccount and never mints a
    # new one. Omitted = the legacy Settings path, which keeps its old find-or-create behaviour.
    marketplace_account_id: Optional[str] = None


class ScopeVerificationOut(BaseModel):
    """Verification of ONE stored credential. Per-scope because WB tokens are category-scoped."""
    scope: str
    verification_status: str
    verified_at: Optional[datetime]

    model_config = {"from_attributes": True}


class VerifyRequest(BaseModel):
    """Verify ONE stored scope. The token is never sent — it is read from storage."""
    scope: str


class VerifyOut(BaseModel):
    """Marketplace-neutral. No marketplace ever adds a field here."""
    connection_id: str
    marketplace: str
    scope: str
    outcome: str                                    # VerificationOutcome
    verification_status: str                        # this scope, after the attempt
    verified_at: Optional[datetime]
    connection_verification_status: str             # rollup across all stored scopes
    connection_verified_at: Optional[datetime]
    retry_after_seconds: Optional[int] = None       # only when the marketplace gave one


class ConnectionOut(BaseModel):
    id: str
    marketplace: str
    label: Optional[str]
    status: str                           # lifecycle / execution gate: connected|invalid|revoked
    verification_status: str              # rollup of the per-scope states below
    verified_at: Optional[datetime]       # NULL unless every stored scope is verified
    scopes: list[str]
    scopes_verification: list[ScopeVerificationOut] = []
    # Ozon's Client-Id — the PUBLIC half of its credential pair, not a secret: the connect form
    # takes it in a plain text field. Returned so replacing an Ozon key does not make the seller
    # retype an identifier they never changed. The API key itself is never returned, here or
    # anywhere else.
    ozon_client_id: Optional[str] = None
    created_at: datetime
    # AR-VIS-1: review-sync cadence, read-only. Written only by the scheduler
    # (tasks/auto_review_pipeline.py) and surfaced so the seller can see that review fetching is alive
    # and when it looks again. Both are plain columns of this already owner-scoped row, so
    # `from_attributes` fills them and the router needs no change. NAIVE UTC (datetime.utcnow) — the
    # JSON carries no timezone suffix, so a client MUST read it as UTC, not local. The internal
    # keyset `review_sync_cursor` is deliberately NOT exposed: it means nothing to a seller.
    # Defaults keep rows written before migration arf1a2b3c4d01 valid.
    review_sync_next_at: Optional[datetime] = None   # when the next batch may run; NULL = never ran
    review_sync_fail_count: int = 0                  # consecutive connection-level failures; 0 = healthy

    model_config = {"from_attributes": True}


# ── Yandex campaign mapping (PULT-LAUNCH-1.4.5G) ─────────────────────────────────
class CampaignOut(BaseModel):
    """A Yandex campaign (store) the connection's key can reach. SAFE projection only — ids and a
    display label, never the raw payload. `linked_store_id` is the MarketplaceStore already bound to
    this campaignId (via Store.external_store_id), or None; the name is NEVER used to auto-link."""
    campaign_id: str
    business_id: Optional[str] = None
    label: Optional[str] = None
    placement_type: Optional[str] = None
    linked_store_id: Optional[str] = None
    link_state: str                       # linked | unlinked


class CampaignLinkRequest(BaseModel):
    """Bind one campaignId to a store of this connection's cabinet. Exactly one of:
      * store_id  — link the campaign to an EXISTING store of the cabinet, or
      * new_store_label — CREATE a new store and link it.
    A store is never created silently; the caller states which shape it wants."""
    campaign_id: str
    store_id: Optional[str] = None
    new_store_label: Optional[str] = None


class CampaignLinkOut(BaseModel):
    campaign_id: str
    linked_store_id: str
    link_state: str = "linked"
    created_store: bool = False


# ── Execution ──────────────────────────────────────────────────────────────────
class ExecuteRequest(BaseModel):
    action_type: str
    payload: dict
    connection_id: Optional[str] = None
    insight_key: Optional[str] = None
    idempotency_key: Optional[str] = None
    dry_run: bool = False


class ExecuteResponse(BaseModel):
    log_id: Optional[str]
    status: str
    action_type: str
    marketplace: str
    api_request_id: Optional[str] = None
    result: dict = {}
    error: Optional[dict] = None
    reversible: bool = False


class ExecutionLogOut(BaseModel):
    id: str
    action_type: str
    marketplace: Optional[str]
    mode: str
    status: str
    insight_key: Optional[str]
    error_code: Optional[str]
    created_at: datetime
    finished_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ExecutionLogDetailOut(BaseModel):
    """Full record for the execution-details drawer (ME-6.1)."""
    id: str
    user_id: str
    connection_id: Optional[str]
    insight_key: Optional[str]
    action_type: str
    marketplace: Optional[str]
    mode: str
    status: str
    payload: dict = {}
    api_request_id: Optional[str]
    result: Optional[dict] = None
    error_code: Optional[str]
    reverted_from: Optional[str]
    idempotency_key: Optional[str]
    created_at: datetime
    finished_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ── Automation rules (L4) ────────────────────────────────────────────────────
class AutomationRuleCreate(BaseModel):
    contour: str
    action_type: str
    trigger: dict = {}
    guard: dict = {}
    mode: str = "confirm"                 # confirm (L3) | auto (L4)
    enabled: bool = False
    connection_id: Optional[str] = None   # required for publish_review_response (per-connection)


class AutomationRuleModeUpdate(BaseModel):
    mode: str                             # confirm | auto


class AutomationRuleOut(BaseModel):
    id: str
    contour: str
    action_type: str
    trigger: dict
    guard: dict
    mode: str
    enabled: bool
    connection_id: Optional[str] = None
    consent_at: Optional[datetime] = None
    consent_version: Optional[str] = None
    consent_revoked_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
