from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class PricingRuleUpsert(BaseModel):
    min_price:          float
    max_price:          float
    target_position:    Literal["below_top_3", "equal_top_1", "custom"] = "below_top_3"
    target_percent:     float = 5.0
    reaction_threshold: float = 3.0
    frequency:          Literal["once_per_day", "once_per_12h", "manual"] = "once_per_day"
    auto_mode:          bool  = False


class PricingRuleOut(BaseModel):
    id:                 str
    product_id:         str
    min_price:          float
    max_price:          float
    target_position:    str
    target_percent:     float
    reaction_threshold: float
    frequency:          str
    auto_mode:          bool
    created_at:         datetime
    updated_at:         datetime

    model_config = {"from_attributes": True}


class PriceChangeLogOut(BaseModel):
    id:         str
    product_id: str
    old_price:  float
    new_price:  float
    reason:     str
    source:     str
    created_at: datetime

    model_config = {"from_attributes": True}


class PriceCheckResult(BaseModel):
    market_price:       float
    recommended_price:  float
    reason:             str
    deviation_percent:  float
    should_change:      bool
    auto_applied:       bool = False
