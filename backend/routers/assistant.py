from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.product import Product

router = APIRouter()


class AssistantRequest(BaseModel):
    question: str
    product_id: Optional[str] = None


class AssistantResponse(BaseModel):
    message: str
    module: str
    action: Optional[str] = None
    action_label: Optional[str] = None


_RULES = [
    {
        "keywords": ["конкурент", "рынок", "позиц", "выдач", "ранжир", "топ-3", "топ конкурент"],
        "module": "competitors",
        "action": "show_competitors",
        "action_label": "Открыть конкурентов",
        "message": (
            "Анализ конкурентов показывает ценовую картину рынка, позиции в выдаче "
            "и прямых соперников. Нажмите «Обновить», чтобы получить актуальные данные."
        ),
    },
    {
        "keywords": ["отзыв", "ответ на отзыв", "рейтинг", "покупател", "клиент", "коммент", "звёзд", "оценк", "негативн"],
        "module": "reviews",
        "action": "show_reviews",
        "action_label": "Перейти к отзывам",
        "message": (
            "В разделе «Отзывы» ИИ генерирует профессиональные ответы за секунды — "
            "вы только проверяете и одобряете. Нажмите «Сгенерировать», чтобы начать."
        ),
    },
    {
        "keywords": ["цен", "стоимост", "прайс", "скидк", "дешевл", "дорож", "ценообразован", "пересчита", "снизи", "повыс"],
        "module": "pricing",
        "action": "show_pricing",
        "action_label": "Управление ценой",
        "message": (
            "Модуль ценообразования отслеживает рынок и рекомендует оптимальную цену. "
            "Настройте правило один раз — и Пульт будет следить сам, включая авто-режим."
        ),
    },
    {
        "keywords": ["финанс", "прибыл", "выручк", "маржа", "расход", "затрат", "p&l", "комисси", "реклам", "бухгалтер", "убыток", "доход"],
        "module": "finance",
        "action": "show_finance",
        "action_label": "Открыть финансы",
        "message": (
            "Финансовый анализ показывает P&L за каждый период: выручку, себестоимость, "
            "комиссию маркетплейса, рекламу и чистую прибыль. Сформируйте отчёт — данные появятся сразу."
        ),
    },
    {
        "keywords": ["монитор", "событ", "уведомлен", "измен", "алгоритм", "правил маркетплейс", "новост", "обновлен правил"],
        "module": "monitor",
        "action": "go_monitor",
        "action_label": "Открыть монитор событий",
        "message": (
            "Монитор событий отслеживает изменения правил и алгоритмов маркетплейсов "
            "в реальном времени. Проверьте последние события, чтобы не пропустить важное."
        ),
    },
    {
        "keywords": ["юрид", "правов", "нарушен", "блокировк", "аудит карточк", "закон", "штраф", "иск", "претензи", "юрист", "судебн", "претензия"],
        "module": "legal",
        "action": "show_legal",
        "action_label": "Юридический аудит",
        "message": (
            "Юридический модуль анализирует карточку товара и отзывы на правовые риски. "
            "Запустите «Аудит карточки» — ИИ проверит соответствие правилам площадки."
        ),
    },
    {
        "keywords": ["начать", "старт", "новичок", "с чего начать", "первый товар", "запустить бизнес", "стартап", "startup"],
        "module": "general",
        "action": "go_startup",
        "action_label": "Точка старта",
        "message": (
            "«Точка старта» — пошаговый помощник для новых продавцов. "
            "Поможет выбрать нишу, рассчитать бюджет и выйти на маркетплейс с минимальным риском."
        ),
    },
    {
        "keywords": ["добавить товар", "новый товар", "создать товар", "добавить продукт"],
        "module": "general",
        "action": "go_dashboard",
        "action_label": "К списку товаров",
        "message": (
            "Чтобы добавить новый товар, перейдите в «Мои товары» и нажмите «Добавить товар». "
            "Укажите название, маркетплейс и цену — анализ конкурентов запустится автоматически."
        ),
    },
    {
        "keywords": ["тариф", "подписк", "оплат", "план", "цена пульт", "сколько стоит"],
        "module": "general",
        "action": None,
        "action_label": None,
        "message": (
            "Управление тарифом доступно в разделе «Настройки». "
            "У нас три плана: Мастер, Профи и Максимальный. Первые 14 дней бесплатно на любом."
        ),
    },
    {
        "keywords": ["помоги", "помощ", "что ты", "что умеешь", "как работает", "возможност", "привет", "здравствуй"],
        "module": "general",
        "action": None,
        "action_label": None,
        "message": (
            "Я — Пульт-ассистент. Помогу разобраться с любым модулем платформы: "
            "конкуренты, отзывы, ценообразование, финансы, мониторинг событий, юридическая защита. "
            "Задайте вопрос — отвечу и покажу, куда перейти."
        ),
    },
]

_DEFAULT = {
    "module": "general",
    "action": None,
    "action_label": None,
    "message": (
        "Я могу помочь с анализом конкурентов, ответами на отзывы, управлением ценой, "
        "финансами, мониторингом событий и юридическими вопросами. Уточните, что вас интересует?"
    ),
}


def _classify(question: str) -> dict:
    q = question.lower()
    for rule in _RULES:
        if any(kw in q for kw in rule["keywords"]):
            return rule
    return _DEFAULT


@router.post("/assistant/ask", response_model=AssistantResponse)
async def ask(
    body: AssistantRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.question.strip():
        raise HTTPException(status_code=422, detail="Вопрос не может быть пустым")

    product_name: Optional[str] = None
    if body.product_id:
        result = await db.execute(
            select(Product).where(
                Product.id == body.product_id,
                Product.user_id == current_user.id,
            )
        )
        product = result.scalar_one_or_none()
        if product:
            product_name = product.name

    rule = _classify(body.question)
    message = rule["message"]
    if product_name:
        message = f"Для товара «{product_name}»: {message[0].lower()}{message[1:]}"

    return AssistantResponse(
        message=message,
        module=rule["module"],
        action=rule.get("action"),
        action_label=rule.get("action_label"),
    )
