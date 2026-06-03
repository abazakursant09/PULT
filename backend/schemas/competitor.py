import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class CompetitorResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    competitor_name: str
    competitor_url: Optional[str]
    marketplace: str
    price: float
    rating: Optional[float]
    reviews_count: Optional[int]
    sales_estimate: Optional[int]
    significance: str
    rank: Optional[int]
    collected_at: datetime

    model_config = {"from_attributes": True}


class CompetitorReport(BaseModel):
    product_id: uuid.UUID
    total_competitors: int
    direct: List[CompetitorResponse]
    significant: List[CompetitorResponse]
    minor: List[CompetitorResponse]
    generated_at: datetime
