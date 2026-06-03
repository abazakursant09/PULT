import asyncio
import random

from sqlalchemy import delete

from database import AsyncSessionLocal
from models.competitor_analysis import CompetitorAnalysis

_BRANDS = [
    "ТехноПрайм", "МегаШоп", "СуперТовар", "ЭлитСтор", "ПрофиМаркет",
    "БыстроПродажа", "ОптимумТрейд", "НовинкаПлюс", "ВыгодаМакс", "ТопСеллер",
    "РусТоргПлюс", "ПремиумГудс", "СмартКупить", "ЭкоПродукт", "УниверсалТрейд",
    "БизнесЛайн", "МастерПродажи", "ГлобалТорг", "АктивСтор", "КвалиТрейд",
    "ПростоПродаж", "МаркетКинг", "РеалТрейд", "СтарПродакт", "ФастСейл",
]

_MARKETPLACES = ["wildberries", "ozon", "yandex_market"]

_MP_DOMAINS = {
    "wildberries": "wildberries.ru",
    "ozon": "ozon.ru",
    "yandex_market": "market.yandex.ru",
}


def _make_competitor(product_id: str, significance: str, rank: int, base_price: float) -> CompetitorAnalysis:
    mp = random.choice(_MARKETPLACES)
    brand = random.choice(_BRANDS)
    slug = brand.lower().replace(" ", "-")
    domain = _MP_DOMAINS[mp]

    price_factor = {"direct": 1.0, "significant": 1.15, "minor": 1.35}[significance]
    price = round(base_price * price_factor * random.uniform(0.85, 1.2), 2)

    return CompetitorAnalysis(
        product_id=product_id,
        competitor_name=brand,
        competitor_url=f"https://{domain}/product/{slug}/{random.randint(10000, 999999)}",
        marketplace=mp,
        price=price,
        rating=round(random.uniform(3.4, 5.0), 1),
        reviews_count=random.randint(5, 8500),
        sales_estimate=random.randint(30, 12000),
        significance=significance,
        rank=rank,
    )


async def collect_competitors(product_id: str, marketplace: str) -> None:
    """Background task: generates 12-20 fake competitors and saves them to DB."""
    await asyncio.sleep(random.uniform(0.5, 1.5))  # simulate network latency

    base_price = random.uniform(800, 12000)

    total = random.randint(12, 20)
    n_direct = random.randint(3, 5)
    n_significant = random.randint(4, 7)
    n_minor = max(1, total - n_direct - n_significant)

    competitors: list[CompetitorAnalysis] = []
    rank = 1
    for significance, count in [
        ("direct", n_direct),
        ("significant", n_significant),
        ("minor", n_minor),
    ]:
        for _ in range(count):
            competitors.append(_make_competitor(product_id, significance, rank, base_price))
            rank += 1

    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(CompetitorAnalysis).where(CompetitorAnalysis.product_id == product_id)
        )
        db.add_all(competitors)
        await db.commit()
