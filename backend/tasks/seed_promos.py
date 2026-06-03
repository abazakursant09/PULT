"""
Seeds demo promo codes on first startup. Idempotent.
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from database import AsyncSessionLocal
from models.promo_code import PromoCode

log = logging.getLogger(__name__)

PROMOS = [
    # Percent discount
    dict(code="WELCOME20",   type="percent",         value=20,  description="20% скидка на первый месяц",
         applicable_plans="profi,maximum", max_activations=None, blogger_name=None,
         expires_at=None),
    dict(code="SALE10",      type="percent",         value=10,  description="10% скидка для новых пользователей",
         applicable_plans="all",           max_activations=500,  blogger_name=None,
         expires_at=None),
    # Fixed rub discount
    dict(code="RUB500",      type="fixed",           value=500, description="Скидка 500 ₽ на любой тариф",
         applicable_plans="all",           max_activations=200,  blogger_name=None,
         expires_at=None),
    # Extended trial
    dict(code="TRIAL30",     type="extended_trial",  value=30,  description="30 дней пробного периода вместо 7",
         applicable_plans="all",           max_activations=None, blogger_name=None,
         expires_at=None),
    # Blogger — free 3 months Profi
    dict(code="BLOGGER_IVAN", type="blogger_free",   value=90,  description="3 месяца «Профи» бесплатно — от Ивана Иванова",
         applicable_plans="profi",         max_activations=None, blogger_name="Иван Иванов",
         expires_at=datetime.utcnow() + timedelta(days=180)),
    dict(code="IVAN20",      type="percent",         value=20,  description="20% скидка от Ивана Иванова",
         applicable_plans="all",           max_activations=None, blogger_name="Иван Иванов",
         expires_at=datetime.utcnow() + timedelta(days=180)),
    dict(code="BLOGGER_ANNA", type="blogger_free",  value=90,  description="3 месяца «Профи» бесплатно — от Анны Петровой",
         applicable_plans="profi",         max_activations=None, blogger_name="Анна Петрова",
         expires_at=datetime.utcnow() + timedelta(days=365)),
    dict(code="ANNA15",      type="percent",         value=15,  description="15% скидка от Анны Петровой",
         applicable_plans="all",           max_activations=None, blogger_name="Анна Петрова",
         expires_at=datetime.utcnow() + timedelta(days=365)),
]


async def seed_promos() -> None:
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(PromoCode))
        if existing.scalars().first() is not None:
            return
        log.info("seed_promos: seeding %d promo codes...", len(PROMOS))
        for p in PROMOS:
            db.add(PromoCode(**p))
        await db.commit()
        log.info("seed_promos: done")
