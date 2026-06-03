import uuid
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.success_story import SuccessStory
from schemas.success_story import SuccessStoryCreate, SuccessStoryOut

router = APIRouter()


@router.get("/success-stories", response_model=list[SuccessStoryOut])
async def list_stories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SuccessStory)
        .order_by(SuccessStory.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()


@router.post("/success-stories", response_model=SuccessStoryOut, status_code=201)
async def create_story(
    data: SuccessStoryCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    story = SuccessStory(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        title=data.title,
        text=data.text,
        author_name=data.author_name.strip() if data.author_name else None,
        created_at=datetime.utcnow(),
    )
    db.add(story)
    await db.commit()
    await db.refresh(story)
    return story
