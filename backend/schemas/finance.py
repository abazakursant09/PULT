from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class RevenueConflictCandidates(BaseModel):
    """The two candidate REVENUE totals when API and CSV disagree (PULT-LAUNCH-1.4.5I-QA2). A typed
    shape, not an arbitrary internal dict — either side may be None if that source has no value."""
    api: Optional[float] = None
    csv: Optional[float] = None


class StoreFinanceSummaryOut(BaseModel):
    """One store's resolved financial total (PULT-LAUNCH-1.4.5I-QA2). Reuses the values
    finance_aggregator.store_financial_totals already computes — no new calculation. `source`,
    `completeness`, `conflict` describe the store's REVENUE resolution (API vs CSV); `conflict` is an
    API-vs-CSV revenue disagreement, never a claim about every metric. net_profit is null (not 0)
    when it cannot be formed (an API-sourced store has no cost of goods)."""
    store_id:           str
    revenue:            float
    net_profit:         Optional[float] = None
    unassigned_revenue: float
    source:             str                    # api | csv — the source of the store's money total
    completeness:       str                    # complete | incomplete
    missing_fields:     list[str] = []
    conflict:           bool = False
    conflict_candidates: Optional[RevenueConflictCandidates] = None


class FinancialSnapshotOut(BaseModel):
    id:              str
    product_id:      str
    period:          str
    revenue:         float
    marketplace_fee: float
    ad_spend:        float
    cogs:            float
    net_profit:      float
    margin_percent:  float
    created_at:      datetime

    model_config = {"from_attributes": True}


class FinanceSummaryItem(BaseModel):
    product_id:         str
    product_name:       str
    total_revenue:      float
    total_net_profit:   float
    avg_margin_percent: float
    snapshots_count:    int
