from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.chat_message import ChatMessage

router = APIRouter()

CHAT_PLANS = {"profi", "maximum"}

# ── Moderation stub ────────────────────────────────────────────────────────────
_BAD_WORDS = [
    "блять", "бля", "сука", "пиздец", "пизд", "хуй", "хуе", "ебать",
    "ебл", "ёбан", "мудак", "мудил", "долбо", "урод", "тупой придурок",
    "идиот", "кретин", "придурок", "засранец",
]
_SPAM_TRIGGERS = ["купи сейчас", "только сегодня", "переходи по ссылке", "телеграм"]
_FRAUD_TRIGGERS = ["дай мне деньги", "переведи средства", "обману"]

def _moderate(text: str) -> tuple[bool, str]:
    """Returns (is_clean, reason). reason: '' | 'profanity' | 'spam' | 'fraud'"""
    t = text.lower()
    for word in _FRAUD_TRIGGERS:
        if word in t:
            return False, "fraud"
    for word in _BAD_WORDS:
        if word in t:
            return False, "profanity"
    spam_hits = sum(1 for p in _SPAM_TRIGGERS if p in t)
    if spam_hits >= 2:
        return False, "spam"
    return True, ""

_VIOLATION_MESSAGES = {
    1: "⚠️ Предупреждение: ваше сообщение нарушает правила сообщества и было удалено.",
    2: "⚠️ Второе нарушение: следующая подписка будет стоить на 5% дороже.",
    3: "⚠️ Третье нарушение: ещё одно нарушение заблокирует доступ к Бирже (+5% к подписке).",
    4: "🚫 Доступ к Бирже заблокирован за систематические нарушения правил.",
}


def require_chat_access(user: User) -> None:
    if user.plan not in CHAT_PLANS:
        raise HTTPException(
            status_code=403,
            detail="chat_access_denied",
        )
    if user.chat_blocked:
        raise HTTPException(
            status_code=403,
            detail="chat_blocked",
        )


# ── Schemas ────────────────────────────────────────────────────────────────────
class MessageOut(BaseModel):
    id:         str
    user_id:    str
    user_name:  str
    message:    str
    created_at: datetime

    model_config = {"from_attributes": True}


class SendMessageIn(BaseModel):
    message: str


class SendMessageOut(BaseModel):
    ok:      bool
    message: Optional[MessageOut] = None
    warning: Optional[str]        = None


# ── Routes ─────────────────────────────────────────────────────────────────────
@router.get("/chat/messages", response_model=list[MessageOut])
async def list_messages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_chat_access(current_user)

    result = await db.execute(
        select(ChatMessage)
        .order_by(ChatMessage.created_at.desc())
        .limit(50)
    )
    rows = result.scalars().all()
    rows.reverse()  # chronological order

    out = []
    for row in rows:
        user_res = await db.execute(select(User).where(User.id == row.user_id))
        u = user_res.scalar_one_or_none()
        out.append(MessageOut(
            id=row.id,
            user_id=row.user_id,
            user_name=u.name if u else "Участник",
            message=row.message,
            created_at=row.created_at,
        ))
    return out


@router.post("/chat/messages", response_model=SendMessageOut)
async def send_message(
    body: SendMessageIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_chat_access(current_user)

    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Сообщение не может быть пустым")
    if len(text) > 1000:
        raise HTTPException(status_code=422, detail="Сообщение слишком длинное (макс. 1000 символов)")

    is_clean, reason = _moderate(text)

    if not is_clean:
        # Fraud → immediate block
        if reason == "fraud":
            current_user.chat_blocked = True
            current_user.chat_violations = max(current_user.chat_violations, 4)
            await db.commit()
            return SendMessageOut(
                ok=False,
                warning="🚫 Сообщение заблокировано: обнаружены признаки мошенничества. "
                        "Доступ к Бирже заблокирован немедленно.",
            )

        current_user.chat_violations = (current_user.chat_violations or 0) + 1
        v = current_user.chat_violations

        if v >= 4:
            current_user.chat_blocked = True

        await db.commit()

        warning = _VIOLATION_MESSAGES.get(min(v, 4), _VIOLATION_MESSAGES[4])
        return SendMessageOut(ok=False, warning=warning)

    # Clean message — save
    msg = ChatMessage(user_id=str(current_user.id), message=text)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    return SendMessageOut(
        ok=True,
        message=MessageOut(
            id=msg.id,
            user_id=msg.user_id,
            user_name=current_user.name,
            message=msg.message,
            created_at=msg.created_at,
        ),
    )


@router.post("/chat/set-plan")
async def set_plan(
    plan: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dev-only: switch plan for the current user."""
    if plan not in ("master", "profi", "maximum"):
        raise HTTPException(status_code=422, detail="Неверный план")
    current_user.plan = plan
    await db.commit()
    return {"ok": True, "plan": plan}
