"""Request/response schemas for keyless cabinets & stores (PULT-LAUNCH-1.4.1).

No credentials, no tokens — this slice only lets a seller declare a cabinet and its
store(s) without any API key.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel


class AccountCreate(BaseModel):
    # FULL canonical identity names only. The short metric/parser codes (wb / ym) are
    # deliberately NOT accepted here — the identity layer never speaks them.
    marketplace: str
    label: str


class StoreCreate(BaseModel):
    label: str


class StoreStatusUpdate(BaseModel):
    status: Literal["active", "archived"]


class StoreOut(BaseModel):
    id: str
    marketplace_account_id: str
    marketplace: str
    label: str
    status: str
    placement_type: Optional[str]
    external_store_id: Optional[str]
    source: str

    model_config = {"from_attributes": True}


class AccountOut(BaseModel):
    id: str
    marketplace: str
    label: Optional[str]
    identity_status: str
    external_account_id: Optional[str]
    has_connection: bool
    stores: Optional[List[StoreOut]] = None

    model_config = {"from_attributes": True}
