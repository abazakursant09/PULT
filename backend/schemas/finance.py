from datetime import datetime
from pydantic import BaseModel


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
