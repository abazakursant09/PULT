from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional
from database import get_db
from models.idea import Idea
import uuid
from datetime import datetime

router = APIRouter(tags=["ideas"])


class IdeaCreate(BaseModel):
    topic:       str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    importance:  str = Field("хотелка", pattern="^(критично|важно|хотелка)$")
    author_name: Optional[str] = Field("Аноним", max_length=80)


class IdeaOut(BaseModel):
    id:          str
    author_name: str
    topic:       str
    description: Optional[str]
    importance:  str
    status:      str
    votes:       int
    created_at:  datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[IdeaOut])
async def list_ideas(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Idea).order_by(Idea.votes.desc(), Idea.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=IdeaOut)
async def create_idea(body: IdeaCreate, db: AsyncSession = Depends(get_db)):
    idea = Idea(
        id=str(uuid.uuid4()),
        author_name=body.author_name or "Аноним",
        topic=body.topic,
        description=body.description,
        importance=body.importance,
        status="на рассмотрении",
        votes=1,
        created_at=datetime.utcnow(),
    )
    db.add(idea)
    await db.commit()
    await db.refresh(idea)
    return idea


@router.post("/{idea_id}/vote", response_model=IdeaOut)
async def vote_idea(idea_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Idea).where(Idea.id == idea_id))
    idea = result.scalar_one_or_none()
    if not idea:
        raise HTTPException(404, "Идея не найдена")
    idea.votes += 1
    await db.commit()
    await db.refresh(idea)
    return idea
