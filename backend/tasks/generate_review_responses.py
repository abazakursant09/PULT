import asyncio
import random
import uuid
from datetime import datetime

from sqlalchemy import delete, select

from database import AsyncSessionLocal
from models.review_response import ReviewResponse
from models.legal_case import LegalCase
from models.product import Product
from tasks.legal_ai import _detect_risk, _REVIEW_TEMPLATES
from tasks.scheduler import send_critical_alert_to_user

_AUTHORS = [
    "Иван Петров", "Мария Сидорова", "Алексей Козлов", "Екатерина Новикова",
    "Дмитрий Волков", "Анна Морозова", "Сергей Лебедев", "Ольга Соколова",
    "Михаил Попов", "Татьяна Белова", "Андрей Кузнецов", "Наталья Фёдорова",
    "Павел Смирнов", "Людмила Орлова", "Виктор Зайцев", "Светлана Медведева",
    "Роман Захаров", "Юлия Степанова", "Игорь Васильев", "Кристина Никитина",
]

_POSITIVE_REVIEWS = [
    "Отличный товар! Очень доволен покупкой, всё соответствует описанию.",
    "Быстрая доставка, качество на высоте. Буду заказывать снова!",
    "Прекрасное качество за свои деньги. Рекомендую всем!",
    "Товар превзошёл мои ожидания. Упаковка целая, всё работает отлично.",
    "Очень доволен! Качество лучше, чем ожидал. Спасибо продавцу.",
    "Супер! Быстро доставили, всё в порядке. Однозначно рекомендую.",
    "Пришёл быстро, упаковка аккуратная. Качество соответствует цене.",
    "Отличное соотношение цена/качество. Использую уже неделю — всё работает.",
    "Заказывал впервые, остался очень доволен. Теперь только здесь буду покупать!",
    "Хороший товар, упаковка надёжная, доставка быстрая. 5 из 5!",
]

_NEGATIVE_REVIEWS = [
    "Товар пришёл с небольшим браком, немного разочарован качеством.",
    "Не совсем соответствует описанию. Ожидал лучшего за такую цену.",
    "Доставка задержалась, упаковка была слегка помята. Не очень доволен.",
    "Качество оставляет желать лучшего. Дома выглядит хуже, чем на фото.",
    "Товар работает, но качество сборки слабоватое. Немного жаль.",
    "Размер оказался не тот, что указан на сайте. Пришлось подбирать.",
    "Инструкция только на иностранном языке. Неудобно разбираться.",
]

_PROBLEMATIC_REVIEWS = [
    "Верните деньги немедленно! Требую возврат!",
    "Это обман! Хочу возврат товара и средств!",
    "Отвратительный товар, требую компенсацию и возврат денег!",
    "Я подам в суд на вас! Мошенники! Верните деньги!",
    "Б**я, что за дерьмо вы продаёте!!! Возврат!!!",
    "Это полная туфта, отстой. Требую возврат немедленно!",
]

_POSITIVE_RESPONSES = [
    "Спасибо за ваш тёплый отзыв! Мы рады, что товар оправдал ваши ожидания. "
    "Будем и дальше радовать вас качественной продукцией!",
    "Благодарим за высокую оценку! Ваш отзыв очень важен для нас. "
    "Ждём вас снова!",
    "Спасибо за доверие! Мы стараемся для вас и очень рады "
    "положительной обратной связи.",
    "Большое спасибо за отзыв! Приятно знать, что покупка вас порадовала. "
    "Будем рады видеть вас снова!",
    "Благодарим за отличную оценку! Мы рады, что вы довольны качеством "
    "товара и сервисом. Возвращайтесь!",
    "Спасибо за добрые слова! Для нас важно, чтобы каждый покупатель "
    "остался доволен. До новых покупок!",
]

_NEGATIVE_RESPONSES = [
    "Приносим искренние извинения за возникшие неудобства. Напишите нам "
    "в личные сообщения — мы обязательно решим проблему в кратчайшие сроки!",
    "Нам очень жаль, что товар вас расстроил. Свяжитесь с нами, и мы найдём "
    "оптимальное решение — замену или дополнительную помощь.",
    "Спасибо за честный отзыв. Мы рассмотрим вашу ситуацию и постараемся "
    "всё исправить. Пожалуйста, напишите в поддержку.",
    "Приносим извинения за причинённые неудобства. Мы приняли к сведению "
    "ваши замечания и работаем над улучшением качества.",
    "Сожалеем о вашем опыте. Напишите нам детали — мы обязательно "
    "разберёмся и постараемся компенсировать неудобства.",
    "Спасибо, что сообщили об этом. Мы уже работаем над устранением "
    "проблемы и будем рады помочь вам лично — напишите в чат.",
]

_RATING_ONLY_RESPONSE = "Спасибо за вашу оценку! Будем рады видеть вас снова."

_SKIP_KEYWORDS = [
    "верните деньги", "требую возврат", "возврат денег", "возврат товара",
    "подам в суд", "мошенник", "дерьм", "туфта", "отстой",
    "б**я", "хочу возврат", "компенсацию и возврат", "немедленно верн",
]


def _is_problematic(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _SKIP_KEYWORDS)


async def generate_review_responses(product_id: str) -> None:
    import logging
    log = logging.getLogger(__name__)

    await asyncio.sleep(random.uniform(0.3, 1.2))

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Product).where(Product.id == product_id))
            product = result.scalar_one_or_none()
            if not product:
                return
            auto_mode: bool = bool(getattr(product, "auto_mode", False))
    except Exception as exc:
        log.error("generate_review_responses: failed to load product %s: %s", product_id, exc)
        return

    reviews: list[ReviewResponse] = []

    # Guaranteed 2-3 rating-only reviews (stars, no comment text)
    for _ in range(random.randint(2, 3)):
        reviews.append(ReviewResponse(
            id=str(uuid.uuid4()),
            product_id=product_id,
            review_text=None,
            author=random.choice(_AUTHORS),
            rating=random.randint(4, 5),
            response_text=_RATING_ONLY_RESPONSE,
            status="published" if auto_mode else "pending",
        ))

    # 5-12 probabilistic content reviews
    for _ in range(random.randint(5, 12)):
        roll = random.random()

        if roll < 0.65:
            review_text = random.choice(_POSITIVE_REVIEWS)
            rating = random.randint(4, 5)
            response_text = random.choice(_POSITIVE_RESPONSES)
            status = "published" if auto_mode else "pending"
        elif roll < 0.85:
            # 1-2 star reviews → no auto-response, send to Legal Shield
            review_text = random.choice(_NEGATIVE_REVIEWS)
            rating = random.randint(1, 2)
            response_text = None
            status = "skipped"
        else:
            review_text = random.choice(_PROBLEMATIC_REVIEWS)
            rating = 1
            response_text = None
            status = "skipped"

        reviews.append(ReviewResponse(
            id=str(uuid.uuid4()),
            product_id=product_id,
            review_text=review_text,
            author=random.choice(_AUTHORS),
            rating=rating,
            response_text=response_text,
            status=status,
        ))

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(ReviewResponse).where(ReviewResponse.product_id == product_id)
            )
            db.add_all(reviews)
            await db.commit()
    except Exception as exc:
        log.error("generate_review_responses: failed to save reviews for product %s: %s", product_id, exc)
        return

    # Send critical alert for low-rated reviews
    very_negative = [r for r in reviews if r.rating is not None and r.rating <= 2]
    if very_negative:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Product).where(Product.id == product_id))
                p = result.scalar_one_or_none()
            if p:
                alert_text = (
                    f"⭐ Получен негативный отзыв на товар <b>«{p.name}»</b>\n\n"
                    f"Оценка: {'★' * very_negative[0].rating}{'☆' * (5 - very_negative[0].rating)} ({very_negative[0].rating}/5)\n"
                    f"Автор: {very_negative[0].author or 'Аноним'}\n"
                    f"Отзывов с оценкой ≤2: {len(very_negative)}\n\n"
                    f"Рекомендуем ответить на отзыв в течение 24 часов."
                )
                await send_critical_alert_to_user(str(p.user_id), alert_text)
        except Exception as exc:
            log.warning("Failed to send critical alert for low review: %s", exc)

    # Create legal cases for all 1-2 star reviews (negative + problematic)
    to_legal = [r for r in reviews if r.status == "skipped" and r.review_text]
    if not to_legal:
        return

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(LegalCase).where(
                    LegalCase.product_id == product_id,
                    LegalCase.case_type == "review",
                )
            )
            for review in to_legal:
                risk = _detect_risk(review.review_text or "")
                tmpl = _REVIEW_TEMPLATES.get(risk, _REVIEW_TEMPLATES["high"])
                snippet = (review.review_text or "")[:120].strip()
                if len(review.review_text or "") > 120:
                    snippet += "…"
                case = LegalCase(
                    id=str(uuid.uuid4()),
                    product_id=product_id,
                    review_id=review.id,
                    case_type="review",
                    status="open",
                    title=tmpl["title"],
                    description=(
                        f"{tmpl['description']}\n\n"
                        f"Автор: {review.author or 'Аноним'} · Оценка: {review.rating}/5\n"
                        f"Текст отзыва: «{snippet}»"
                    ),
                    risk_level=risk,
                    ai_recommendation=tmpl["ai_recommendation"],
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(case)
            await db.commit()
    except Exception as exc:
        log.error("generate_review_responses: failed to create legal cases for product %s: %s", product_id, exc)
