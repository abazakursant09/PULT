from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.telegram_settings import TelegramSettings
from schemas.telegram_settings import TelegramSettingsOut, TelegramSettingsUpdate, UpdateTelegramChatId
from services.telegram import send_message

router = APIRouter()


async def _get_or_create_settings(user_id: str, db: AsyncSession) -> TelegramSettings:
    q = await db.execute(select(TelegramSettings).where(TelegramSettings.user_id == user_id))
    s = q.scalar_one_or_none()
    if not s:
        s = TelegramSettings(user_id=user_id)
        db.add(s)
        await db.commit()
        await db.refresh(s)
    return s


@router.get("/telegram/settings", response_model=TelegramSettingsOut)
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_or_create_settings(str(current_user.id), db)


@router.put("/telegram/settings", response_model=TelegramSettingsOut)
async def update_settings(
    data: TelegramSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = await _get_or_create_settings(str(current_user.id), db)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(s, field, value)
    await db.commit()
    await db.refresh(s)
    return s


@router.put("/profile/telegram")
async def update_telegram_chat_id(
    data: UpdateTelegramChatId,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chat_id = (data.telegram_chat_id or "").strip() or None
    current_user.telegram_chat_id = chat_id
    await db.commit()
    return {"ok": True, "telegram_chat_id": current_user.telegram_chat_id}


@router.get("/profile/telegram")
async def get_telegram_chat_id(
    current_user: User = Depends(get_current_user),
):
    return {"telegram_chat_id": current_user.telegram_chat_id}


@router.post("/telegram/test")
async def send_test_message(
    current_user: User = Depends(get_current_user),
):
    chat_id = current_user.telegram_chat_id
    if not chat_id:
        raise HTTPException(
            status_code=400,
            detail="Telegram Chat ID не указан. Сохраните его в настройках.",
        )
    ok = await send_message(
        chat_id,
        "✅ <b>Тест уведомлений — Бизнес-Пульт</b>\n\n"
        "Если вы видите это сообщение — уведомления настроены правильно!\n\n"
        f"Аккаунт: <b>{current_user.name}</b> ({current_user.email})",
    )
    if not ok:
        raise HTTPException(
            status_code=502,
            detail="Не удалось отправить сообщение. Проверьте Chat ID и настройте бота.",
        )
    return {"ok": True}


@router.post("/telegram/trigger-insights")
async def trigger_insights_now(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger the Intelligence Loop for the current user (dev/test)."""
    from tasks.intelligence_loop import _process_user

    chat_id = current_user.telegram_chat_id
    if not chat_id:
        raise HTTPException(
            status_code=400,
            detail="Telegram Chat ID не указан.",
        )

    tg_q = await db.execute(
        select(TelegramSettings).where(TelegramSettings.user_id == str(current_user.id))
    )
    tg_settings = tg_q.scalar_one_or_none()
    if not tg_settings:
        tg_settings = TelegramSettings(user_id=str(current_user.id))
        db.add(tg_settings)
        await db.commit()
        await db.refresh(tg_settings)

    sent = await _process_user(current_user, tg_settings, db)
    return {"ok": True, "notifications_sent": sent}
