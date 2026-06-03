from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel


# ── Connections ────────────────────────────────────────────────────────────────
class ConnectionCreate(BaseModel):
    marketplace: str                      # wildberries | ozon
    label: Optional[str] = None
    token: str                            # raw API token — encrypted server-side, never returned
    scope: str                            # feedbacks | prices | advert | content | stocks | promotions
    ozon_client_id: Optional[str] = None


class ConnectionOut(BaseModel):
    id: str
    marketplace: str
    label: Optional[str]
    status: str
    scopes: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}


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


# ── Automation rules (L4) ────────────────────────────────────────────────────
class AutomationRuleCreate(BaseModel):
    contour: str
    action_type: str
    trigger: dict = {}
    guard: dict = {}
    mode: str = "confirm"                 # confirm (L3) | auto (L4)
    enabled: bool = False


class AutomationRuleOut(BaseModel):
    id: str
    contour: str
    action_type: str
    trigger: dict
    guard: dict
    mode: str
    enabled: bool

    model_config = {"from_attributes": True}
