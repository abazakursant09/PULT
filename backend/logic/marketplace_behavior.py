"""
Marketplace Behavior Memory Layer — Sprint 20.

Operational knowledge of WB / Ozon / Yandex Market mechanics.
Not predictions. Not AI. Observed platform behavior that contextualizes signals.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MarketplacePattern:
    marketplace:              str
    category:                 str | None
    pattern_type:             str
    description:              str
    trigger_conditions:       dict
    expected_behavior:        str
    operational_risk:         str
    stabilization_window_days: int
    confidence:               int
    recommendation_bias:      str | None = None


# ── Knowledge base ─────────────────────────────────────────────────────────────
# Tone: "обычно наблюдается", "характерно для", "часто сопровождается"
# No predictions. No probability claims. Operational memory only.

MARKETPLACE_PATTERNS: list[MarketplacePattern] = [

    # ── Wildberries (5) ────────────────────────────────────────────────────────

    MarketplacePattern(
        marketplace="wildberries",
        category="high_ad_spend",
        pattern_type="advertising_spike",
        description=(
            "WB после резкого роста рекламных ставок обычно увеличивает CTR "
            "быстрее конверсии. Органический трафик перераспределяется через 10–15 дн."
        ),
        trigger_conditions={"marketplace": "wildberries", "categories": ["high_ad_spend"]},
        expected_behavior="Краткосрочный рост показов, нормализация органики через 10-15 дн.",
        operational_risk="Резкое снижение ставок после spike ухудшает позиции быстрее, чем постепенное.",
        stabilization_window_days=10,
        confidence=80,
        recommendation_bias="reduce_bid_volatility",
    ),

    MarketplacePattern(
        marketplace="wildberries",
        category="seo_opportunity",
        pattern_type="reindexing_instability",
        description=(
            "На WB частые изменения контента карточки вызывают временную пессимизацию "
            "в поиске на 3–7 дн. Характерно при смене заголовка или главного изображения."
        ),
        trigger_conditions={"marketplace": "wildberries", "categories": ["seo_opportunity"]},
        expected_behavior="Снижение позиций на 3-7 дн. после изменений, затем восстановление.",
        operational_risk="Несколько смен контента подряд суммируют период пессимизации.",
        stabilization_window_days=7,
        confidence=75,
        recommendation_bias="apply_72h_cooldown",
    ),

    MarketplacePattern(
        marketplace="wildberries",
        category="margin_crisis",
        pattern_type="margin_pressure_ads",
        description=(
            "WB в конкурентных категориях быстро поднимает ДРР без пропорционального "
            "роста выручки. Структурное давление на маржу часто сопровождается "
            "высокой долей нецелевого трафика по широким ключам."
        ),
        trigger_conditions={"marketplace": "wildberries", "categories": ["margin_crisis"]},
        expected_behavior="Давление стабилизируется после оптимизации ставок по ключам с низкой конверсией.",
        operational_risk="Широкие ключи на WB снижают ROAS быстрее, чем на других площадках.",
        stabilization_window_days=14,
        confidence=78,
        recommendation_bias=None,
    ),

    MarketplacePattern(
        marketplace="wildberries",
        category="high_ad_spend",
        pattern_type="organic_recovery_lag",
        description=(
            "После снижения рекламного давления органика WB восстанавливается "
            "с задержкой 7–14 дн. Площадка переиндексирует релевантность постепенно."
        ),
        trigger_conditions={"marketplace": "wildberries", "categories": ["high_ad_spend"]},
        expected_behavior="Органический трафик восстанавливается не мгновенно — требует 7-14 дн.",
        operational_risk="Ожидание мгновенного результата от снижения ставок ведёт к преждевременной реакции.",
        stabilization_window_days=14,
        confidence=72,
        recommendation_bias=None,
    ),

    MarketplacePattern(
        marketplace="wildberries",
        category="low_stock",
        pattern_type="stock_position_coupling",
        description=(
            "WB связывает видимость позиций со стабильностью остатков. "
            "Out-of-stock обычно снижает органическую позицию на 20–30 мест, "
            "восстановление требует 3–7 дн. после пополнения."
        ),
        trigger_conditions={"marketplace": "wildberries", "categories": ["low_stock"]},
        expected_behavior="Потеря позиций при out-of-stock, восстановление через 3-7 дн. после пополнения.",
        operational_risk="Позиция не восстанавливается автоматически — нужен активный сток.",
        stabilization_window_days=5,
        confidence=82,
        recommendation_bias=None,
    ),

    # ── Ozon (5) ───────────────────────────────────────────────────────────────

    MarketplacePattern(
        marketplace="ozon",
        category="sales_growth",
        pattern_type="attribution_delay",
        description=(
            "На Ozon рост продаж часто подтверждается с задержкой атрибуции 24–48ч. "
            "Ранние данные могут недооценивать фактический объём конверсий."
        ),
        trigger_conditions={"marketplace": "ozon", "categories": ["sales_growth"]},
        expected_behavior="Финальные данные по продажам доступны через 24-48ч после события.",
        operational_risk="Решения на основе незрелых данных Ozon могут быть преждевременными.",
        stabilization_window_days=2,
        confidence=85,
        recommendation_bias=None,
    ),

    MarketplacePattern(
        marketplace="ozon",
        category="high_rating",
        pattern_type="review_moderation_lag",
        description=(
            "Модерация отзывов на Ozon занимает 24–72ч. "
            "Рейтинг в аналитике часто не учитывает свежие отзывы последних 3 дн."
        ),
        trigger_conditions={"marketplace": "ozon", "categories": ["high_rating"]},
        expected_behavior="Рейтинг в dashboard отстаёт от реального на 1-3 дн.",
        operational_risk="Всплеск негативных отзывов проявится в аналитике с задержкой.",
        stabilization_window_days=3,
        confidence=80,
        recommendation_bias=None,
    ),

    MarketplacePattern(
        marketplace="ozon",
        category="margin_crisis",
        pattern_type="price_index_check",
        description=(
            "Ozon проверяет ценовой индекс каждые 6ч. "
            "Резкое снижение цены временно улучшает позиции, "
            "но запускает повторную проверку и может ограничить рекламные показы."
        ),
        trigger_conditions={"marketplace": "ozon", "categories": ["margin_crisis"]},
        expected_behavior="Изменение цены влияет на позиции в рекламе через 6-12ч.",
        operational_risk="Частые изменения цены увеличивают частоту проверок и могут ограничить показы.",
        stabilization_window_days=1,
        confidence=74,
        recommendation_bias="stabilize_price_first",
    ),

    MarketplacePattern(
        marketplace="ozon",
        category="high_ad_spend",
        pattern_type="boost_attribution_gap",
        description=(
            "Premium-продвижение и Трафаретная реклама Ozon имеют разные окна атрибуции. "
            "Сравнение ROAS между форматами требует нормализации данных через 48–72ч."
        ),
        trigger_conditions={"marketplace": "ozon", "categories": ["high_ad_spend"]},
        expected_behavior="ROAS по разным форматам рекламы Ozon сравним только через 72ч.",
        operational_risk="Ранее сравнение ROAS между форматами даёт искажённую картину.",
        stabilization_window_days=3,
        confidence=76,
        recommendation_bias=None,
    ),

    MarketplacePattern(
        marketplace="ozon",
        category="seo_opportunity",
        pattern_type="search_algo_update",
        description=(
            "Ozon обновляет поисковый алгоритм регулярно. "
            "Временное снижение позиций в первые 3 дн. после апдейта — "
            "характерный паттерн, не связанный с качеством карточки."
        ),
        trigger_conditions={"marketplace": "ozon", "categories": ["seo_opportunity"]},
        expected_behavior="Позиции стабилизируются через 3-5 дн. после алгоритмического обновления.",
        operational_risk="Преждевременные изменения карточки во время переиндексации усугубляют снижение.",
        stabilization_window_days=3,
        confidence=70,
        recommendation_bias=None,
    ),

    # ── Yandex Market (5) ──────────────────────────────────────────────────────

    MarketplacePattern(
        marketplace="yandex_market",
        category="margin_crisis",
        pattern_type="price_oscillation_penalty",
        description=(
            "Частая смена цены на Яндекс Маркете ухудшает Price Index и "
            "стабильность позиции в поиске. "
            "Площадка штрафует нестабильное ценообразование."
        ),
        trigger_conditions={"marketplace": "yandex_market", "categories": ["margin_crisis"]},
        expected_behavior="После стабилизации цены на 14+ дн. позиции восстанавливаются.",
        operational_risk="Каждое изменение цены продлевает период нестабильности позиций.",
        stabilization_window_days=14,
        confidence=80,
        recommendation_bias="stabilize_price_first",
    ),

    MarketplacePattern(
        marketplace="yandex_market",
        category="low_stock",
        pattern_type="logistics_sla_impact",
        description=(
            "Нарушение SLA доставки или out-of-stock влияет на рейтинг магазина "
            "на Яндекс Маркете в течение 14–21 дн. "
            "Площадка учитывает надёжность поставок в ранжировании."
        ),
        trigger_conditions={"marketplace": "yandex_market", "categories": ["low_stock"]},
        expected_behavior="Рейтинг магазина восстанавливается через 14-21 дн. после нормализации поставок.",
        operational_risk="Единичный out-of-stock при высоком трафике снижает рейтинг непропорционально сильно.",
        stabilization_window_days=21,
        confidence=78,
        recommendation_bias=None,
    ),

    MarketplacePattern(
        marketplace="yandex_market",
        category="high_ad_spend",
        pattern_type="cpc_bid_volatility",
        description=(
            "Ставки CPC на Яндекс Маркете в конкурентных категориях требуют "
            "ежедневной калибровки. Еженедельные изменения ставок часто приводят "
            "к потере позиций в аукционе."
        ),
        trigger_conditions={"marketplace": "yandex_market", "categories": ["high_ad_spend"]},
        expected_behavior="Позиции в аукционе YM стабилизируются через 7 дн. после фиксации ставок.",
        operational_risk="Редкое управление ставками на YM неэффективнее, чем на WB или Ozon.",
        stabilization_window_days=7,
        confidence=74,
        recommendation_bias="reduce_bid_volatility",
    ),

    MarketplacePattern(
        marketplace="yandex_market",
        category="seo_opportunity",
        pattern_type="organic_seo_stability",
        description=(
            "Яндекс Маркет органика требует 14–21 дн. для переиндексации "
            "после изменений контента карточки. "
            "Площадка оценивает стабильность описания при ранжировании."
        ),
        trigger_conditions={"marketplace": "yandex_market", "categories": ["seo_opportunity"]},
        expected_behavior="Новые позиции по обновлённому контенту видны через 14-21 дн.",
        operational_risk="Повторные изменения до завершения переиндексации обнуляют таймер.",
        stabilization_window_days=21,
        confidence=76,
        recommendation_bias="apply_72h_cooldown",
    ),

    MarketplacePattern(
        marketplace="yandex_market",
        category="high_rating",
        pattern_type="review_quality_gate",
        description=(
            "Яндекс Маркет понижает видимость товаров с рейтингом <4.0. "
            "Первые 10–15 отзывов оказывают непропорционально большое влияние "
            "на итоговый рейтинг нового товара."
        ),
        trigger_conditions={"marketplace": "yandex_market", "categories": ["high_rating"]},
        expected_behavior="Рейтинг ≥4.0 критичен для органической видимости на YM.",
        operational_risk="Потеря рейтинга ниже 4.0 требует накопления 20+ новых отзывов для восстановления.",
        stabilization_window_days=7,
        confidence=82,
        recommendation_bias=None,
    ),
]


# ── Match engine ───────────────────────────────────────────────────────────────

def match_marketplace_patterns(
    category: str,
    marketplace: str,
    metrics: dict | None = None,
) -> list[MarketplacePattern]:
    """
    Return up to 2 MarketplacePattern objects relevant to this insight.
    Matches on marketplace + category. Priority: higher confidence first.
    """
    mp_norm = (marketplace or "").lower().replace("-", "_")
    cat_norm = (category or "").lower()

    # Normalize marketplace aliases
    if mp_norm in ("wb", "wildberries"):
        mp_norm = "wildberries"
    elif mp_norm in ("ym", "yandex market", "yandex_market"):
        mp_norm = "yandex_market"

    matches = [
        p for p in MARKETPLACE_PATTERNS
        if p.marketplace == mp_norm
        and cat_norm in (p.trigger_conditions.get("categories") or [])
    ]

    matches.sort(key=lambda p: -p.confidence)
    return matches[:2]


def behavior_note_for_insight(
    category: str,
    marketplace: str,
) -> tuple[list[str], str | None, int | None]:
    """
    Convenience wrapper used by action_engine enrichment.
    Returns (pattern_type_slugs, behavior_note, stabilization_window_days).
    """
    patterns = match_marketplace_patterns(category, marketplace)
    if not patterns:
        return [], None, None

    primary = patterns[0]
    return (
        [p.pattern_type for p in patterns],
        primary.description,
        primary.stabilization_window_days,
    )
