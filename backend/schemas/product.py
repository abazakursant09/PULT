import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ProductCreate(BaseModel):
    name: str
    marketplace: str
    category: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None


class ProductResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    marketplace: str
    category: Optional[str]
    sku: Optional[str]
    price: Optional[float]
    created_at: datetime

    model_config = {"from_attributes": True}
