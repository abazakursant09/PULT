import random
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.monitor_event import MonitorEvent

_EVENT_POOL = [
    {
        "title": "Wildberries повышает комиссию на 2% с 1 июня",
        "description": (
            "WB объявил об изменении размера комиссии для категорий «Одежда» и «Обувь». "
            "Изменения затронут более 50 000 продавцов и вступят в силу через 30 дней."
        ),
        "source": "wildberries",
        "severity": "critical",
        "affected_module": "pricing",
        "action_required": (
            "Пересчитайте юнит-экономику для товаров из этих категорий. "
            "Рассмотрите повышение цен на 2–3% или оптимизацию себестоимости. "
            "Воспользуйтесь модулем «Ценообразование» для автоматического обновления цен."
        ),
    },
    {
        "title": "Ozon обновляет алгоритм ранжирования карточек",
        "description": (
            "Новый алгоритм поиска Ozon повышает вес показателя «Скорость ответа на отзывы». "
            "Карточки с временем ответа более 48 часов будут понижены в выдаче."
        ),
        "source": "ozon",
        "severity": "important",
        "affected_module": "reviews",
        "action_required": (
            "Настройте автоответы на отзывы в модуле «Автоответы» — целевое время реакции до 24 ч. "
            "Проверьте и закройте все необработанные отзывы. "
            "Рекомендуется включить авто-режим публикации для положительных отзывов."
        ),
    },
    {
        "title": "Яндекс Маркет меняет требования к контенту карточек",
        "description": (
            "С 15 июля вводятся обязательные поля: состав материала, страна производства, "
            "сертификаты соответствия. Карточки без этих данных будут скрыты из выдачи."
        ),
        "source": "yandex_market",
        "severity": "critical",
        "affected_module": "general",
        "action_required": (
            "Проверьте все активные карточки на Яндекс Маркете на наличие обязательных полей. "
            "Добавьте состав, страну производства и прикрепите актуальные сертификаты. "
            "Обновите карточки до 14 июля включительно."
        ),
    },
    {
        "title": "Законопроект об обязательной маркировке текстиля принят в 1-м чтении",
        "description": (
            "Государственная дума приняла законопроект, обязывающий маркировать все текстильные "
            "товары через систему «Честный ЗНАК». Дата вступления в силу — 1 сентября 2025."
        ),
        "source": "legislation",
        "severity": "critical",
        "affected_module": "legal",
        "action_required": (
            "Зарегистрируйтесь в системе «Честный ЗНАК», если ещё не сделали это. "
            "Заключите договор с оператором ЭДО. "
            "Запросите коды маркировки для остатков склада до 31 августа."
        ),
    },
    {
        "title": "Wildberries вводит штраф за отмену заказов со склада продавца",
        "description": (
            "С 10 июля продавцы, работающие по схеме FBS, будут штрафоваться за отмену заказов "
            "на сумму 5% от стоимости отменённого товара. Лимит бесплатных отмен — 1% в месяц."
        ),
        "source": "wildberries",
        "severity": "important",
        "affected_module": "general",
        "action_required": (
            "Поддерживайте актуальные остатки в системе WB, чтобы не принимать заказы на отсутствующий товар. "
            "Настройте автоматическую синхронизацию остатков. "
            "Проверьте текущие заказы FBS и обработайте все необработанные вовремя."
        ),
    },
    {
        "title": "Ozon запускает программу субсидирования скидок для продавцов",
        "description": (
            "Ozon компенсирует продавцам до 50% скидки при участии в акциях платформы "
            "в период с 1 по 31 июля. Минимальный размер скидки для участия — 10%."
        ),
        "source": "ozon",
        "severity": "info",
        "affected_module": "pricing",
        "action_required": (
            "Проверьте условия акции в личном кабинете Ozon в разделе «Акции и скидки». "
            "Подайте заявку на участие до 28 июня. "
            "Проанализируйте, какие товары выгодно включить в акцию с учётом компенсации."
        ),
    },
    {
        "title": "Яндекс Маркет снижает порог бесплатной доставки для покупателей",
        "description": (
            "Порог бесплатной доставки для покупателей снижается с 699 до 499 рублей. "
            "Это увеличит конверсию на недорогих товарах, но повысит расходы на логистику."
        ),
        "source": "yandex_market",
        "severity": "info",
        "affected_module": "pricing",
        "action_required": (
            "Пересмотрите минимальные цены на бюджетные товары с учётом возросшей нагрузки на логистику. "
            "Оцените, не уйдут ли маржинальные товары в минус. "
            "Рассмотрите переход на FBY (хранение на складе Яндекса) для снижения операционных затрат."
        ),
    },
    {
        "title": "Новые требования ФНС к онлайн-кассам при работе на маркетплейсах",
        "description": (
            "ФНС уточнила порядок применения ККТ для самозанятых и ИП на маркетплейсах. "
            "Ряд операций, ранее не требовавших пробития чека, теперь подпадает под контроль."
        ),
        "source": "legislation",
        "severity": "important",
        "affected_module": "legal",
        "action_required": (
            "Проконсультируйтесь с бухгалтером или налоговым консультантом по новым требованиям. "
            "Проверьте, нужна ли вам ККТ исходя из вашего налогового режима и схемы работы. "
            "Изучите разъяснения ФНС на официальном сайте nalog.gov.ru."
        ),
    },
    {
        "title": "Wildberries обновляет политику работы с браком и возвратами",
        "description": (
            "Новые правила обработки возвратов: продавец обязан принять решение по возврату "
            "в течение 72 часов. При просрочке WB автоматически вернёт средства покупателю."
        ),
        "source": "wildberries",
        "severity": "important",
        "affected_module": "general",
        "action_required": (
            "Настройте уведомления о новых возвратах в личном кабинете WB. "
            "Назначьте ответственного сотрудника за обработку возвратов ежедневно. "
            "Ознакомьтесь с обновлённой инструкцией по работе с браком в справке WB."
        ),
    },
]


async def run_check(db: AsyncSession) -> list[MonitorEvent]:
    # Fetch titles that are already stored to avoid duplicates
    existing_result = await db.execute(select(MonitorEvent.title))
    existing_titles = {row[0] for row in existing_result.all()}

    # Only consider events that haven't been stored yet
    fresh_pool = [e for e in _EVENT_POOL if e["title"] not in existing_titles]

    if not fresh_pool:
        # All events already exist — return the most recent ones without inserting
        result = await db.execute(
            select(MonitorEvent).order_by(MonitorEvent.created_at.desc()).limit(5)
        )
        return list(result.scalars().all())

    count  = random.randint(1, min(3, len(fresh_pool)))
    chosen = random.sample(fresh_pool, count)

    saved: list[MonitorEvent] = []
    for data in chosen:
        ev = MonitorEvent(**data)
        db.add(ev)
        try:
            await db.commit()
            await db.refresh(ev)
            saved.append(ev)
        except IntegrityError:
            await db.rollback()
    return saved
