"""
Read-only схемы графа Товар → Листинг → Решение (Doctrine §3/§7).

Только для чтения. Никаких *Create / *Update — этот контур граф не мутирует
(мутации остаются за ingest/matcher/decision-engine).
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel


class ListingNode(BaseModel):
    id: str
    marketplace: str
    external_id: str
    title: Optional[str] = None
    match_method: Optional[str] = None      # barcode | sku | name_fuzzy | seed | manual
    match_confidence: Optional[float] = None
    confirmed: bool

    model_config = {"from_attributes": True}


class DecisionNode(BaseModel):
    id: str
    listing_id: Optional[str] = None        # к какому листингу привязано (может быть None)
    problem: str
    severity: str                           # loss | warn | gain
    status: str                             # open | in_progress | resolved | dismissed
    action: Optional[str] = None
    pnl_impact: Optional[float] = None
    pnl_level: Optional[str] = None         # level1 | level2

    model_config = {"from_attributes": True}


class AtomNode(BaseModel):
    """PhysicalProduct + его листинги и решения."""
    id: str
    title: str
    barcode: Optional[str] = None
    seller_sku: Optional[str] = None
    brand: Optional[str] = None
    cogs: Optional[float] = None
    cogs_source: Optional[str] = None
    trademark_status: str

    listings: List[ListingNode] = []
    decisions: List[DecisionNode] = []

    # производные (считаются в сервисе, не в БД)
    listing_count: int = 0
    marketplaces: List[str] = []
    needs_review: bool = False              # есть хоть один неподтверждённый листинг


class GraphSummary(BaseModel):
    atoms: int
    listings: int
    decisions: int
    unconfirmed_listings: int
    marketplaces: List[str]


class ProductGraph(BaseModel):
    summary: GraphSummary
    atoms: List[AtomNode]
