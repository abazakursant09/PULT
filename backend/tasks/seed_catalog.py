"""
Seeds the Supplier and TransportCompany tables with realistic stub data
on first startup. Idempotent — skips if rows already exist.
"""
import logging
import random
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import AsyncSessionLocal
from models.supplier import Supplier
from models.transport_company import TransportCompany

log = logging.getLogger(__name__)

SUPPLIERS = [
    # Одежда — Россия
    dict(company_name="ООО «МодаОпт»",              industry="clothing",    region="Москва",           country="russia",
         description="Оптовое производство одежды: базовые коллекции, спортивная линейка, детская одежда. OEM/ODM.",
         website="modaopt.ru",     phone="+7 (495) 100-22-33", min_order_qty=100,
         is_verified=True,  rating=4.7, total_reviews=52, total_deals=183),
    dict(company_name="ЗАО «ТрендФэшн»",            industry="clothing",    region="Санкт-Петербург",  country="russia",
         description="Производство верхней одежды и трикотажа. Сертификат ГОСТ. Логотипирование от 50 шт.",
         website="trendfashion.ru",phone="+7 (812) 444-55-66", min_order_qty=50,
         is_verified=True,  rating=4.5, total_reviews=38, total_deals=97),
    # Одежда — Китай
    dict(company_name="Guangzhou StylePro Apparel",  industry="clothing",    region="Гуанчжоу",         country="china",
         description="OEM/ODM clothing factory: T-shirts, hoodies, sportswear, workwear. MOQ 100pcs.",
         website="stylepro.cn",    phone="+86 20 8800 0011",  min_order_qty=100,
         is_verified=True,  rating=4.8, total_reviews=74, total_deals=320),
    dict(company_name="Yiwu FashionBase Trading",    industry="clothing",    region="Иу",               country="china",
         description="Ready-to-ship clothing stock: basics, seasonal collections. Fast delivery.",
         website="fashionbase.cn", phone="+86 579 8600 0022", min_order_qty=200,
         is_verified=False, rating=4.3, total_reviews=31, total_deals=112),
    # Текстиль — Россия
    dict(company_name="ЗАО «ТекстильПро»",           industry="textile",     region="Иваново",          country="russia",
         description="Производство тканей и трикотажа: хлопок, лён, джинс, флис. 15 лет на рынке.",
         website="textilepro.ru",  phone="+7 (493) 232-50-10", min_order_qty=100,
         is_verified=True,  rating=4.6, total_reviews=29, total_deals=74),
    dict(company_name="ООО «ТканьОпт»",              industry="textile",     region="Иваново",          country="russia",
         description="Оптовая торговля тканями. Широкий ассортимент: сатин, бязь, кулирка, футер.",
         website="tkaniopt.ru",    phone="+7 (493) 215-00-44", min_order_qty=50,
         is_verified=False, rating=4.2, total_reviews=17, total_deals=48),
    # Текстиль — Китай
    dict(company_name="Guangzhou FashionLink Trading", industry="textile",   region="Гуанчжоу",         country="china",
         description="Fabric & apparel manufacturing: OEM sportswear, uniforms, fashion collections.",
         website="fashionlink.cn", phone="+86 20 8800 5678",  min_order_qty=300,
         is_verified=True,  rating=4.5, total_reviews=29, total_deals=145),
    # Электроника — Россия
    dict(company_name="ООО «ЭлектроТех»",            industry="electronics", region="Москва",           country="russia",
         description="Электронные компоненты, кабели, LED-освещение, умные гаджеты для дома.",
         website="electrotech.ru", phone="+7 (495) 999-12-34", min_order_qty=50,
         is_verified=True,  rating=4.6, total_reviews=48, total_deals=165),
    dict(company_name="ООО «ТехноОпт»",              industry="electronics", region="Москва",           country="russia",
         description="Оптовые поставки электроники: гаджеты, аксессуары, компьютерная периферия.",
         website="technoopt.ru",   phone="+7 (495) 777-88-99", min_order_qty=30,
         is_verified=True,  rating=4.4, total_reviews=33, total_deals=121),
    # Электроника — Китай
    dict(company_name="Shenzhen TechSource Co., Ltd.", industry="electronics", region="Шэньчжэнь",     country="china",
         description="Consumer electronics, LED displays, PCB manufacturing. ISO 9001 certified.",
         website="techsource.cn",  phone="+86 755 8800 1234", min_order_qty=200,
         is_verified=True,  rating=4.8, total_reviews=62, total_deals=310),
    dict(company_name="Shenzhen SmartGoods Co.",       industry="electronics", region="Шэньчжэнь",     country="china",
         description="Smart home devices, wireless earbuds, portable speakers, phone accessories.",
         website="smartgoods.cn",  phone="+86 755 8900 5566", min_order_qty=100,
         is_verified=True,  rating=4.6, total_reviews=44, total_deals=198),
    # Товары для дома — Россия
    dict(company_name="ООО «ДомКомфорт»",            industry="home_goods",  region="Москва",           country="russia",
         description="Товары для дома: постельное бельё, кухонный инвентарь, органайзеры, светильники.",
         website="domkomfort.ru",  phone="+7 (495) 200-30-40", min_order_qty=50,
         is_verified=True,  rating=4.5, total_reviews=41, total_deals=134),
    dict(company_name="ЗАО «УютОпт»",               industry="home_goods",  region="Екатеринбург",     country="russia",
         description="Оптовые поставки домашнего текстиля и декора. Принты под заказ.",
         website="uyutopt.ru",     phone="+7 (343) 300-10-20", min_order_qty=100,
         is_verified=False, rating=4.3, total_reviews=22, total_deals=67),
    # Товары для дома — Китай
    dict(company_name="Yiwu HomeStyle Manufacturing", industry="home_goods",  region="Иу",              country="china",
         description="Home goods, kitchen tools, storage solutions, bedding. Factory direct price.",
         website="homestyle.cn",   phone="+86 579 8500 1122", min_order_qty=200,
         is_verified=True,  rating=4.4, total_reviews=36, total_deals=152),
    # Мебель
    dict(company_name="ЗАО «МебельКрафт»",           industry="furniture",   region="Воронеж",          country="russia",
         description="Производство корпусной и мягкой мебели. Индивидуальные размеры под заказ.",
         website="mebelcraft.ru",  phone="+7 (473) 260-80-00", min_order_qty=20,
         is_verified=True,  rating=4.4, total_reviews=18, total_deals=55),
    # Косметика
    dict(company_name="ООО «КосметикаПрофи»",        industry="cosmetics",   region="Санкт-Петербург",  country="russia",
         description="Оптовая поставка косметики и парфюмерии. OEM/СТМ от 500 шт.",
         website="cosmeticprofi.ru",phone="+7 (812) 333-77-00", min_order_qty=100,
         is_verified=True,  rating=4.3, total_reviews=15, total_deals=43),
    # Прочее
    dict(company_name="ОАО «МеталлСтрой»",           industry="metals",      region="Челябинск",        country="russia",
         description="Прокат металла, трубы, арматура. Прямые поставки с завода.",
         website="metallstroy.ru", phone="+7 (351) 777-55-00", min_order_qty=1000,
         is_verified=True,  rating=4.8, total_reviews=57, total_deals=203),
    dict(company_name="ООО «УпаковкаЭксперт»",       industry="packaging",   region="Новосибирск",      country="russia",
         description="Картонная тара, стрейч-пленка, пакеты ПВД/ПНД. Любые объёмы.",
         website="upakovka-expert.ru",phone="+7 (383) 213-00-10",min_order_qty=1000,
         is_verified=False, rating=4.1, total_reviews=7,  total_deals=19),
    dict(company_name="ОАО «МашиностроительПлюс»",   industry="machinery",   region="Екатеринбург",     country="russia",
         description="Промышленное оборудование: станки, прессы, конвейеры. Гарантия 2 года.",
         website="mashplus.ru",    phone="+7 (343) 388-55-55", min_order_qty=1,
         is_verified=True,  rating=4.9, total_reviews=31, total_deals=87),
    dict(company_name="Beijing PrecisionParts Ltd.",  industry="machinery",   region="Пекин",            country="china",
         description="CNC machined parts, auto components, industrial fasteners. High precision.",
         website="bjprecision.com",phone="+86 10 6800 3344",  min_order_qty=100,
         is_verified=True,  rating=4.7, total_reviews=41, total_deals=189),
]

TRANSPORT_COMPANIES = [
    dict(name="Экспресс-Логистик",    region="Вся Россия",        delivery_types="auto,express",
         description="Быстрая доставка по всей России. Гарантия сроков.",
         phone="+7 (800) 555-01-01", rating=4.6, total_reviews=128,
         price_per_kg=85.0,  price_per_m3=1200.0, min_transit_days=2, max_transit_days=5),
    dict(name="ТрансКарго",           region="Вся Россия",        delivery_types="auto,cargo",
         description="Сборные грузы, крупногабаритные перевозки. Страхование груза.",
         phone="+7 (800) 555-02-02", rating=4.4, total_reviews=95,
         price_per_kg=62.0,  price_per_m3=900.0,  min_transit_days=3, max_transit_days=8),
    dict(name="АвиаДост",             region="Вся Россия",        delivery_types="air,express",
         description="Авиаперевозки грузов. Работаем со всеми крупными аэропортами.",
         phone="+7 (800) 555-03-03", rating=4.8, total_reviews=74,
         price_per_kg=320.0, price_per_m3=4500.0, min_transit_days=1, max_transit_days=2),
    dict(name="Деловой Транзит",       region="Москва и МО",       delivery_types="auto,express",
         description="Доставка по Москве и Подмосковью. Курьерская служба.",
         phone="+7 (495) 777-04-04", rating=4.5, total_reviews=56,
         price_per_kg=95.0,  price_per_m3=1400.0, min_transit_days=1, max_transit_days=3),
    dict(name="ГрузТрек",             region="Сибирь и Урал",     delivery_types="auto,rail,cargo",
         description="Перевозки по Сибири, Уралу и Дальнему Востоку. ЖД-экспедирование.",
         phone="+7 (383) 444-05-05", rating=4.3, total_reviews=83,
         price_per_kg=54.0,  price_per_m3=780.0,  min_transit_days=4, max_transit_days=12),
    dict(name="Скоростная Доставка",   region="Москва — Санкт-Петербург", delivery_types="auto,express",
         description="Ежедневные рейсы Москва–СПб. Онлайн-трекинг.",
         phone="+7 (800) 555-06-06", rating=4.7, total_reviews=112,
         price_per_kg=78.0,  price_per_m3=1100.0, min_transit_days=1, max_transit_days=2),
    dict(name="ТК Меридиан",           region="Вся Россия + СНГ",  delivery_types="auto,rail,cargo",
         description="Международные перевозки. Таможенное оформление под ключ.",
         phone="+7 (495) 888-07-07", rating=4.5, total_reviews=67,
         price_per_kg=71.0,  price_per_m3=1050.0, min_transit_days=5, max_transit_days=15),
    dict(name="АлтайФрахт",            region="Западная Сибирь",   delivery_types="auto,cargo",
         description="Перевозки по Западной Сибири и Казахстану. Рефрижераторы.",
         phone="+7 (385) 222-08-08", rating=4.2, total_reviews=38,
         price_per_kg=49.0,  price_per_m3=720.0,  min_transit_days=3, max_transit_days=9),
    dict(name="МегаЛайн Логистика",    region="Вся Россия",        delivery_types="auto,rail,express,cargo",
         description="Полный спектр логистических услуг. Складское хранение.",
         phone="+7 (800) 555-09-09", rating=4.6, total_reviews=148,
         price_per_kg=68.0,  price_per_m3=980.0,  min_transit_days=2, max_transit_days=7),
]


async def seed_catalog() -> None:
    async with AsyncSessionLocal() as db:
        # Check if already seeded
        sup_count = await db.execute(select(Supplier))
        if sup_count.scalars().first() is not None:
            return

        log.info("seed_catalog: seeding suppliers and transport companies...")

        for s in SUPPLIERS:
            db.add(Supplier(**s))

        for tc in TRANSPORT_COMPANIES:
            db.add(TransportCompany(**tc))

        await db.commit()
        log.info("seed_catalog: done — %d suppliers, %d TKs", len(SUPPLIERS), len(TRANSPORT_COMPANIES))
