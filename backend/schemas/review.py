from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

from services.review.state import derive_state


class ReviewResponseOut(BaseModel):
    id: str
    product_id: str
    review_text: Optional[str]
    author: Optional[str]
    rating: Optional[int]
    response_text: Optional[str]
    status: str
    # AR0/AR2 lifecycle fields, surfaced for the seller workspace (AR3).
    marketplace: Optional[str] = None
    external_review_id: Optional[str] = None
    review_created_at: Optional[datetime] = None
    safety_category: Optional[str] = None
    manual_required_reason: Optional[str] = None
    published_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    publication_attempts: int = 0
    created_at: datetime
    updated_at: datetime
    # Derived, not stored — computed from status + safety_category + failure.
    state: str = ""

    model_config = {"from_attributes": True}

    @classmethod
    def of(cls, row) -> "ReviewResponseOut":
        out = cls.model_validate(row)
        out.state = derive_state(row.status, row.safety_category, row.failure_reason)
        return out


class ReviewResponseUpdate(BaseModel):
    response_text: Optional[str] = None
    status: Optional[str] = None


class ReviewQueueResponse(BaseModel):
    items: List[ReviewResponseOut]
    total: int
    limit: int
    offset: int


class ReviewHistoryEntry(BaseModel):
    timestamp: Optional[datetime]
    mode: str
    status: str
    error_code: Optional[str] = None


class ReviewHistoryResponse(BaseModel):
    review_id: str
    entries: List[ReviewHistoryEntry]
