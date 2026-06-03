from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class LegalCaseOut(BaseModel):
    id:                str
    product_id:        str
    case_type:         str
    status:            str
    title:             str
    description:       str
    risk_level:        str
    ai_recommendation: str
    user_response:     Optional[str]
    review_id:         Optional[str] = None
    created_at:        datetime
    updated_at:        datetime

    model_config = {"from_attributes": True}


class LegalCaseUpdate(BaseModel):
    status:        Optional[str] = None
    user_response: Optional[str] = None


class ReviewAnalyzeRequest(BaseModel):
    review_text: str
