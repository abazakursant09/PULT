from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_CHECKLIST = [
    {
        "step": 1,
        "title": "Зарегистрируйте ИП или оформите самозанятость",
        "short": "ИП или самозанятость",
        "icon": "user-check",
        "description": "Выберите правовую форму: самозанятость подходит для старта с минимальными расходами, "
                       "ИП — если планируете масштабироваться или нанимать сотрудников. "
                       "Регистрация через Госуслуги занимает 1–3 дня и стоит от 0 ₽.",
        "time_estimate": "1–5 дней",
        "cost_estimate": "0 – 800 ₽",
        "tips": [
            "Самозанятость: налог 4–6%, без отчётности, до 2.4 млн ₽/год",
            "ИП на УСН 6%: просто, но нужна ежегодная декларация",
            "Открыть через приложение Мой налог (самозанятость) или Госуслуги (ИП)",
        ],
        "links": [
            {"label": "Мой налог (самозанятость)", "url": "https://npd.nalog.ru"},
            {"label": "Регистрация ИП на Госуслугах", "url": "https://gosuslugi.ru"},
        ],
    },
    {
        "step": 2,
        "title": "Выберите торговую площадку",
        "short": "Выбор маркетплейса",
        "icon": "shopping-bag",
        "description": "Wildberries, Ozon и Яндекс Маркет — три крупнейших маркетплейса России. "
                       "Каждый имеет свои особенности: комиссии, аудиторию и требования к поставщикам. "
                       "Начните с одного, изучите правила и только потом выходите на второй.",
        "time_estimate": "1–2 дня",
        "cost_estimate": "0 ₽",
        "tips": [
            "Wildberries: огромная аудитория, высокая конкуренция, комиссия 5–25%",
            "Ozon: удобный кабинет, лояльнее к новым продавцам, комиссия 4–15%",
            "Яндекс Маркет: сильный в электронике и бытовой технике, комиссия 3–9%",
            "Зарегистрируйтесь как поставщик — это бесплатно",
        ],
        "links": [
            {"label": "Стать продавцом на WB", "url": "https://seller.wildberries.ru"},
            {"label": "Seller Ozon", "url": "https://seller.ozon.ru"},
            {"label": "Яндекс Маркет для продавцов", "url": "https://partner.market.yandex.ru"},
        ],
    },
    {
        "step": 3,
        "title": "Подготовьте первую поставку",
        "short": "Первая поставка",
        "icon": "package",
        "description": "Закупите товар у поставщика или производителя. Убедитесь в наличии необходимых документов: "
                       "сертификатов соответствия, деклараций ТР ТС. Промаркируйте товар согласно требованиям площадки. "
                       "Отправьте поставку на склад маркетплейса.",
        "time_estimate": "7–30 дней",
        "cost_estimate": "15 000 – 200 000 ₽",
        "tips": [
            "Начните с небольшой тестовой партии (20–50 единиц)",
            "Проверьте наличие сертификата / декларации соответствия у поставщика",
            "Разберитесь в системе маркировки «Честный знак» — некоторые категории обязательны",
            "Используйте FBO (склад МП) для начала — меньше логистических хлопот",
        ],
        "links": [],
    },
    {
        "step": 4,
        "title": "Создайте продающую карточку товара",
        "short": "Первая карточка",
        "icon": "image",
        "description": "Качественная карточка — ключ к продажам. Сделайте профессиональные фотографии на белом фоне, "
                       "напишите подробное описание с ключевыми словами, заполните все характеристики. "
                       "SEO-оптимизированный заголовок повышает видимость в поиске.",
        "time_estimate": "2–5 дней",
        "cost_estimate": "3 000 – 15 000 ₽",
        "tips": [
            "Главное фото: белый фон, чёткое изображение, товар на 70% кадра",
            "Добавьте инфографику с ключевыми преимуществами (2–3 фото)",
            "Заголовок: категория + бренд + ключевые характеристики (до 100 символов)",
            "Заполните ВСЕ характеристики — карточка ранжируется выше",
        ],
        "links": [],
    },
    {
        "step": 5,
        "title": "Получите первые продажи",
        "short": "Первые продажи",
        "icon": "trending-up",
        "description": "Запустите внутреннюю рекламу маркетплейса для быстрого старта. "
                       "Соберите первые отзывы через программу «Отзывы за баллы» или самовыкупы (осторожно). "
                       "Анализируйте конкурентов и корректируйте цену. "
                       "Отслеживайте метрики в личном кабинете.",
        "time_estimate": "Первые 30 дней",
        "cost_estimate": "3 000 – 20 000 ₽ на рекламу",
        "tips": [
            "Бюджет на рекламу: 10–15% от выручки на старте",
            "Цель на первый месяц: 10–20 отзывов с оценкой 4.5+",
            "Цена ниже конкурентов на 5–10% при запуске — для получения органики",
            "Анализируйте отчёты воронки: показы → клики → корзина → заказ",
        ],
        "links": [],
    },
]

_CATEGORY_COMMISSIONS = {
    "electronics":  {"wb": 0.12, "ozon": 0.08, "ym": 0.05},
    "clothing":     {"wb": 0.20, "ozon": 0.15, "ym": 0.12},
    "food":         {"wb": 0.10, "ozon": 0.08, "ym": 0.07},
    "cosmetics":    {"wb": 0.15, "ozon": 0.12, "ym": 0.09},
    "home":         {"wb": 0.14, "ozon": 0.10, "ym": 0.08},
    "sports":       {"wb": 0.13, "ozon": 0.11, "ym": 0.09},
    "toys":         {"wb": 0.15, "ozon": 0.12, "ym": 0.10},
    "auto":         {"wb": 0.12, "ozon": 0.09, "ym": 0.07},
}

_CATEGORY_COGS = {
    "electronics": 0.45,
    "clothing":    0.30,
    "food":        0.55,
    "cosmetics":   0.35,
    "home":        0.40,
    "sports":      0.38,
    "toys":        0.35,
    "auto":        0.42,
}


class CalculatorInput(BaseModel):
    category:    str   # electronics | clothing | food | cosmetics | home | sports | toys | auto
    budget:      float # total budget in RUB
    marketplace: str   # wb | ozon | ym


@router.get("/startup/checklist")
def get_checklist():
    return [
        {k: v for k, v in step.items() if k != "tips" and k != "links"}
        for step in _CHECKLIST
    ]


@router.get("/startup/checklist/{step_number}")
def get_step(step_number: int):
    step = next((s for s in _CHECKLIST if s["step"] == step_number), None)
    if not step:
        raise HTTPException(status_code=404, detail="Шаг не найден")
    return step


@router.post("/startup/calculate")
def calculate(body: CalculatorInput):
    cat = body.category.lower()
    mp  = body.marketplace.lower()

    commissions = _CATEGORY_COMMISSIONS.get(cat, _CATEGORY_COMMISSIONS["home"])
    commission_rate = commissions.get(mp, 0.12)
    cogs_rate = _CATEGORY_COGS.get(cat, 0.40)

    budget = body.budget

    # Fixed and variable cost allocations
    cogs             = round(budget * cogs_rate)
    marketplace_fee  = round(budget * commission_rate)
    logistics        = round(budget * 0.07)
    advertising      = round(budget * 0.12)
    packaging        = round(budget * 0.03)
    photo_content    = min(round(budget * 0.04), 15000)
    legal_docs       = min(round(budget * 0.02), 5000)

    total_costs      = cogs + marketplace_fee + logistics + advertising + packaging + photo_content + legal_docs
    estimated_profit = budget - total_costs
    margin_percent   = round((estimated_profit / budget) * 100, 1) if budget > 0 else 0

    mp_labels = {"wb": "Wildberries", "ozon": "Ozon", "ym": "Яндекс Маркет"}

    return {
        "budget":           round(budget),
        "category":         cat,
        "marketplace":      mp_labels.get(mp, mp),
        "commission_rate":  round(commission_rate * 100, 1),
        "breakdown": [
            {"label": "Закупка товара",         "amount": cogs,            "percent": round(cogs / budget * 100, 1)},
            {"label": "Комиссия маркетплейса",  "amount": marketplace_fee, "percent": round(marketplace_fee / budget * 100, 1)},
            {"label": "Реклама",                "amount": advertising,     "percent": round(advertising / budget * 100, 1)},
            {"label": "Логистика / хранение",   "amount": logistics,       "percent": round(logistics / budget * 100, 1)},
            {"label": "Упаковка",               "amount": packaging,       "percent": round(packaging / budget * 100, 1)},
            {"label": "Фото и контент",         "amount": photo_content,   "percent": round(photo_content / budget * 100, 1)},
            {"label": "Документы / сертифик.",  "amount": legal_docs,      "percent": round(legal_docs / budget * 100, 1)},
        ],
        "total_costs":        total_costs,
        "estimated_profit":   estimated_profit,
        "margin_percent":     margin_percent,
        "recommendation": (
            "Хороший старт — бюджет позволяет сформировать тестовую партию и запустить рекламу."
            if budget >= 50000 else
            "Минимальный старт. Рекомендуем начать с 1–2 SKU и фокусироваться на органическом трафике."
            if budget >= 20000 else
            "Очень ограниченный бюджет. Рассмотрите дропшиппинг или самозанятость с услугами."
        ),
    }
