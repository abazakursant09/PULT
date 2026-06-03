"""
SEO card project CRUD router.

Endpoints:
  POST   /api/seo-projects                       — create project
  GET    /api/seo-projects                       — list user's projects (last 20)
  DELETE /api/seo-projects/{project_id}          — delete (owner only)
  POST   /api/seo-projects/{project_id}/duplicate — copy with prefixed name
"""

import json
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.seo_project import SeoProject
from models.user import User

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class SeoProjectCreate(BaseModel):
    name:              Optional[str]       = ""
    product_name:      str
    marketplace:       Optional[str]       = "all"
    preset:            Optional[str]       = "premium"
    category:          Optional[str]       = "auto"
    typography_preset: Optional[str]       = "wb-aggressive"
    current_price:     Optional[str]       = ""
    old_price:         Optional[str]       = ""
    advantages:        Optional[List[str]] = []
    template_set:      Optional[str]       = "default"
    image_urls:        Optional[List[str]] = []


class SeoProjectResponse(BaseModel):
    id:                str
    user_id:           str
    name:              str
    product_name:      str
    marketplace:       str
    preset:            str
    category:          str
    typography_preset: Optional[str]
    current_price:     Optional[str]
    old_price:         Optional[str]
    advantages:        List[str]
    template_set:      Optional[str]
    image_urls:        List[str]
    created_at:        str  # "DD.MM.YYYY HH:MM"

    class Config:
        from_attributes = True


def _to_response(project: SeoProject) -> SeoProjectResponse:
    created = project.created_at
    if isinstance(created, datetime):
        created_str = created.strftime("%d.%m.%Y %H:%M")
    else:
        created_str = str(created)

    try:
        advantages = json.loads(project.advantages_json or "[]")
    except (json.JSONDecodeError, TypeError):
        advantages = []

    try:
        image_urls = json.loads(project.image_urls_json or "[]")
    except (json.JSONDecodeError, TypeError):
        image_urls = []

    return SeoProjectResponse(
        id=project.id,
        user_id=project.user_id,
        name=project.name or "",
        product_name=project.product_name or "",
        marketplace=project.marketplace or "all",
        preset=project.preset or "premium",
        category=project.category or "auto",
        typography_preset=project.typography_preset,
        current_price=project.current_price,
        old_price=project.old_price,
        advantages=advantages,
        template_set=project.template_set,
        image_urls=image_urls,
        created_at=created_str,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/seo-projects", response_model=SeoProjectResponse, status_code=201)
async def create_seo_project(
    body: SeoProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = SeoProject(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        name=body.name or "",
        product_name=body.product_name.strip(),
        marketplace=body.marketplace or "all",
        preset=body.preset or "premium",
        category=body.category or "auto",
        typography_preset=body.typography_preset or "wb-aggressive",
        current_price=body.current_price or "",
        old_price=body.old_price or "",
        advantages_json=json.dumps(body.advantages or [], ensure_ascii=False),
        template_set=body.template_set or "default",
        image_urls_json=json.dumps(body.image_urls or [], ensure_ascii=False),
        created_at=datetime.utcnow(),
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return _to_response(project)


@router.get("/seo-projects", response_model=List[SeoProjectResponse])
async def list_seo_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SeoProject)
        .where(SeoProject.user_id == current_user.id)
        .order_by(SeoProject.created_at.desc())
        .limit(20)
    )
    projects = result.scalars().all()
    return [_to_response(p) for p in projects]


@router.delete("/seo-projects/{project_id}")
async def delete_seo_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SeoProject).where(SeoProject.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    if project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет доступа")
    await db.delete(project)
    await db.commit()
    return {"ok": True}


@router.post("/seo-projects/{project_id}/duplicate", response_model=SeoProjectResponse, status_code=201)
async def duplicate_seo_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SeoProject).where(SeoProject.id == project_id)
    )
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Проект не найден")
    if original.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет доступа")

    copy = SeoProject(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        name=f"Копия: {original.name or original.product_name}",
        product_name=original.product_name,
        marketplace=original.marketplace,
        preset=original.preset,
        category=original.category,
        typography_preset=original.typography_preset,
        current_price=original.current_price,
        old_price=original.old_price,
        advantages_json=original.advantages_json,
        template_set=original.template_set,
        image_urls_json=original.image_urls_json,
        created_at=datetime.utcnow(),
    )
    db.add(copy)
    await db.commit()
    await db.refresh(copy)
    return _to_response(copy)
