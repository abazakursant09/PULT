from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ReviewResponseOut(BaseModel):
    id: str
    product_id: str
    review_text: Optional[str]
    author: Optional[str]
    rating: Optional[int]
    response_text: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReviewResponseUpdate(BaseModel):
    response_text: Optional[str] = None
    status: Optional[str] = None
