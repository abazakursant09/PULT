"""Read-only store catalog schemas (PULT-LAUNCH-1.4.5B).

Two seller-facing reads: the products of ONE store, and the import history of ONE store.
Nothing here exposes credentials, temp paths, file hashes or another cabinet's data, and no
metric (revenue / profit / stock / rating) is promised — the store-aware API does not produce
them yet, so the schema does not pretend it does.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class StoreRef(BaseModel):
    """The store the page belongs to — enough for the header, nothing technical."""
    id: str
    label: str
    marketplace: str
    status: str

    model_config = {"from_attributes": True}


class StoreProductItem(BaseModel):
    product_id: str
    sku: Optional[str]
    name: str
    # Placement is what binds this product to THIS store; its state is the honest answer to
    # "is the product still here?", so it travels with every row.
    placement_status: str
    placement_source: str
    first_seen_at: datetime
    last_seen_at: datetime


class StoreProductsPage(BaseModel):
    store: StoreRef
    items: List[StoreProductItem]
    page: int
    page_size: int
    total: int
    pages: int


class StoreImportItem(BaseModel):
    import_id: str
    filename: str
    import_type: str
    status: str
    created_at: datetime
    confirmed_at: Optional[datetime]
    total_rows: int
    imported_count: int
    skipped_rows: int
    # conflicts / unassigned are NOT columns on ImportRecord — they are counted from the
    # import's own rows, so they stay true after a seller resolves one.
    conflicts: int
    unassigned: int
    has_unresolved_conflicts: bool
    source: str


class StoreImportsPage(BaseModel):
    store: StoreRef
    items: List[StoreImportItem]
    page: int
    page_size: int
    total: int
    pages: int
