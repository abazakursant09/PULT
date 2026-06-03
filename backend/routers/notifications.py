from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.notification import Notification
from models.user import User

router = APIRouter()


@router.get("/notifications")
async def list_notifications(
    page:     int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * per_page

    total_q = await db.execute(
        select(func.count()).select_from(Notification)
        .where(Notification.user_id == str(current_user.id))
    )
    total = total_q.scalar() or 0

    unread_q = await db.execute(
        select(func.count()).select_from(Notification)
        .where(Notification.user_id == str(current_user.id))
        .where(Notification.is_read == False)
    )
    unread_count = unread_q.scalar() or 0

    items_q = await db.execute(
        select(Notification)
        .where(Notification.user_id == str(current_user.id))
        .order_by(Notification.created_at.desc())
        .offset(offset).limit(per_page)
    )
    items = items_q.scalars().all()

    return {
        "items": [
            {
                "id":         n.id,
                "type":       n.type,
                "title":      n.title,
                "message":    n.message,
                "is_read":    n.is_read,
                "created_at": n.created_at.isoformat(),
            }
            for n in items
        ],
        "total":        total,
        "unread_count": unread_count,
        "page":         page,
        "per_page":     per_page,
    }


@router.get("/notifications/unread-count")
async def unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(func.count()).select_from(Notification)
        .where(Notification.user_id == str(current_user.id))
        .where(Notification.is_read == False)
    )
    return {"count": q.scalar() or 0}


@router.post("/notifications/read-all")
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(Notification)
        .where(Notification.user_id == str(current_user.id))
        .where(Notification.is_read == False)
    )
    for n in q.scalars().all():
        n.is_read = True
    await db.commit()
    return {"ok": True}


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(Notification)
        .where(Notification.id == notification_id)
        .where(Notification.user_id == str(current_user.id))
    )
    n = q.scalar_one_or_none()
    if n:
        n.is_read = True
        await db.commit()
    return {"ok": True}


@router.post("/notifications/seed")
async def seed_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(func.count()).select_from(Notification)
        .where(Notification.user_id == str(current_user.id))
    )
    if (q.scalar() or 0) > 0:
        return {"ok": True, "seeded": False}

    samples = [
        {
            "type":    "new_review",
            "title":   "Новый отзыв на товар",
            "message": "Покупатель оставил отзыв на «Умная колонка XL». Рейтинг: 4⭐. Рекомендуем ответить.",
        },
        {
            "type":    "offer_change",
            "title":   "Изменение условий площадки",
            "message": "Wildberries обновил условия работы с продавцами. Проверьте раздел «Монитор» для подробностей.",
        },
        {
            "type":    "trial_end",
            "title":   "Пробный период заканчивается",
            "message": "Ваш 14-дневный пробный период заканчивается через 3 дня. Оформите подписку, чтобы не потерять доступ.",
        },
        {
            "type":    "limit_reached",
            "title":   "Достигнут лимит товаров",
            "message": "Вы добавили 10 из 10 товаров по тарифу «Мастер». Обновите тариф для добавления новых.",
        },
        {
            "type":    "new_review",
            "title":   "Автоответ опубликован",
            "message": "Автоответ на отзыв по товару «Рюкзак городской» успешно опубликован на Ozon.",
        },
    ]

    for i, s in enumerate(samples):
        db.add(Notification(
            user_id=str(current_user.id),
            type=s["type"],
            title=s["title"],
            message=s["message"],
            created_at=datetime.utcnow() - timedelta(hours=i * 3),
        ))

    await db.commit()
    return {"ok": True, "seeded": True}
