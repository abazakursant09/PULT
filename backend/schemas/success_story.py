from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator


class SuccessStoryCreate(BaseModel):
    title:       str
    text:        str
    author_name: Optional[str] = None

    @field_validator('title', 'text')
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('Не может быть пустым')
        return v.strip()


class SuccessStoryOut(BaseModel):
    id:          str
    title:       str
    text:        str
    author_name: Optional[str]
    created_at:  datetime

    model_config = {"from_attributes": True}
