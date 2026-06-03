"""
Marketplace Operational Memory — WB / Ozon / YM mechanics knowledge base.

Each entry maps (rule_category, marketplace) → real constraint + automation level.
This is factual knowledge, not heuristics. Update when marketplace policies change.

AutomationLevel semantics:
  safe_auto       — PULT can act without seller intervention
  human_required  — PULT recommends; seller must confirm and execute
  blocked         — automation structurally disabled; risk exceeds benefit
  delayed         — action valid but data window not settled yet; wait N hours
  critical_alert  — urgent human attention; PULT escalates, never acts
"""
from __future__ import annotations
from typing import TypedDict, Literal

AutomationLevel = Literal[
    "safe_auto",
    "human_required",
    "blocked",
    "delayed",
    "critical_alert",
]


class Mechanic(TypedDict):
    automation_level:  AutomationLevel
    mechanic_name:     str            # short KB slug
    risk_note:         str            # shown to seller in signal card
    delay_hours:       int            # only relevant for "delayed"
    safe_threshold:    dict           # numeric guardrails for safe_auto


# ── Wildberries ───────────────────────────────────────────────────────────────

_WB: dict[str, Mechanic] = {
    "high_ad_spend": {
        "automation_level": "human_required",
        "mechanic_name":    "wb_autoactions_margin_risk",
        "risk_note": (
            "WB autoactions ранее снижали маржу ниже безопасного порога. "
            "Изменение рекламных ставок требует ручного подтверждения."
        ),
        "delay_hours":   0,
        "safe_threshold": {"margin_pct_min": 0.10},
    },
    "margin_crisis": {
        "automation_level": "human_required",
        "mechanic_name":    "wb_autoactions_margin_risk",
        "risk_note": (
            "WB autoactions ранее снижали маржу ниже безопасного порога. "
            "Изменение цены или структуры затрат — только вручную."
        ),
        "delay_hours":   0,
        "safe_threshold": {"margin_pct_min": 0.10},
    },
    "seo_opportunity": {
        "automation_level": "safe_auto",
        "mechanic_name":    "wb_indexation_cooldown",
        "risk_note": (
            "Частая смена описания может вызвать временную пессимизацию индексации WB. "
            "PULT применяет cooldown 72ч между пересборками одной карточки."
        ),
        "delay_hours":   72,
        "safe_threshold": {"rebuilds_per_card_per_week": 1},
    },
    "low_stock": {
        "automation_level": "critical_alert",
        "mechanic_name":    "wb_fbo_quota_lock",
        "risk_note": (
            "При нулевом остатке WB снимает товар с витрины мгновенно. "
            "Восстановление позиций занимает 3–10 дней. Нужны немедленные действия."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
    "high_rating": {
        "automation_level": "safe_auto",
        "mechanic_name":    "wb_organic_boost",
        "risk_note": (
            "Высокий рейтинг на WB напрямую улучшает органическую позицию. "
            "Авто-пересборка карточки безопасна — контент-политика не нарушается."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
    "sales_growth": {
        "automation_level": "safe_auto",
        "mechanic_name":    "wb_growth_signal",
        "risk_note": (
            "Устойчивый рост продаж на WB — безопасный сигнал для масштабирования. "
            "PULT не изменяет ставки автоматически — только рекомендует."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
}

# ── Ozon ──────────────────────────────────────────────────────────────────────

_OZON: dict[str, Mechanic] = {
    "sales_growth": {
        "automation_level": "delayed",
        "mechanic_name":    "ozon_attribution_lag",
        "risk_note": (
            "Ozon аналитика имеет задержку атрибуции 24–48ч. "
            "Ранний анализ роста может быть ложным. PULT ждёт 48ч перед сигналом."
        ),
        "delay_hours":   48,
        "safe_threshold": {},
    },
    "high_ad_spend": {
        "automation_level": "delayed",
        "mechanic_name":    "ozon_attribution_lag",
        "risk_note": (
            "Данные о расходах на рекламу в Ozon Performance обновляются с задержкой. "
            "Решения по бюджету принимать только после полного закрытия суток."
        ),
        "delay_hours":   48,
        "safe_threshold": {},
    },
    "margin_crisis": {
        "automation_level": "human_required",
        "mechanic_name":    "ozon_price_correction_cascade",
        "risk_note": (
            "Ozon автоматически корректирует цены при демпинге конкурентов. "
            "Ручное повышение цены может нарушить ценовой индекс и снизить видимость."
        ),
        "delay_hours":   0,
        "safe_threshold": {"price_index_min": 0.95},
    },
    "low_stock": {
        "automation_level": "critical_alert",
        "mechanic_name":    "ozon_fbs_availability_drop",
        "risk_note": (
            "При нулевом остатке FBS-товар отключается в выдаче Ozon мгновенно. "
            "Повторная активация требует ручного подтверждения со стороны продавца."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
    "seo_opportunity": {
        "automation_level": "safe_auto",
        "mechanic_name":    "ozon_content_policy",
        "risk_note": (
            "Ozon не пессимизирует карточки за частые правки контента. "
            "Авто-пересборка безопасна при соблюдении контент-политики."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
    "high_rating": {
        "automation_level": "safe_auto",
        "mechanic_name":    "ozon_rating_boost",
        "risk_note": (
            "Высокий рейтинг на Ozon улучшает позиции в поиске и в выборке 'Топ'. "
            "Авто-пересборка карточки безопасна."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
}

# ── Яндекс Маркет ─────────────────────────────────────────────────────────────

_YM: dict[str, Mechanic] = {
    "low_stock": {
        "automation_level": "critical_alert",
        "mechanic_name":    "ym_availability_sensitivity",
        "risk_note": (
            "YM чувствителен к падению доступности товара: при < 70% доступности "
            "карточка вылетает из TopK органики. Восстановление — до 2 недель."
        ),
        "delay_hours":   0,
        "safe_threshold": {"availability_pct_min": 0.70},
    },
    "high_ad_spend": {
        "automation_level": "human_required",
        "mechanic_name":    "ym_click_fraud_risk",
        "risk_note": (
            "Завышенные ставки в Директе на YM привлекают нецелевые клики конкурентов. "
            "Изменение ставок — только вручную с анализом качества трафика."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
    "margin_crisis": {
        "automation_level": "human_required",
        "mechanic_name":    "ym_price_index_monitoring",
        "risk_note": (
            "YM мониторит цену продавца и снижает CPC при завышенных ставках. "
            "Изменение цены требует ручного расчёта ценового индекса."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
    "seo_opportunity": {
        "automation_level": "safe_auto",
        "mechanic_name":    "ym_content_safe",
        "risk_note": (
            "YM принимает обновления контента без задержки индексации. "
            "Авто-пересборка карточки безопасна."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
    "sales_growth": {
        "automation_level": "safe_auto",
        "mechanic_name":    "ym_growth_signal",
        "risk_note": (
            "Рост продаж на YM — достоверный сигнал: атрибуция закрывается в сутки. "
            "PULT рекомендует масштабирование без автоматического изменения ставок."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
    "high_rating": {
        "automation_level": "safe_auto",
        "mechanic_name":    "ym_rating_visibility",
        "risk_note": (
            "Рейтинг на YM влияет на показ в категориях и кнопке 'Лучший выбор'. "
            "Авто-пересборка карточки безопасна."
        ),
        "delay_hours":   0,
        "safe_threshold": {},
    },
}

# ── Unknown / fallback ────────────────────────────────────────────────────────

_FALLBACK: Mechanic = {
    "automation_level": "human_required",
    "mechanic_name":    "unknown_marketplace",
    "risk_note":        "Механика площадки неизвестна. Все действия требуют ручного подтверждения.",
    "delay_hours":      0,
    "safe_threshold":   {},
}

# ── Public lookup ─────────────────────────────────────────────────────────────

_REGISTRY: dict[str, dict[str, Mechanic]] = {
    "wildberries":   _WB,
    "ozon":          _OZON,
    "yandex_market": _YM,
}


def get_mechanic(rule_category: str, marketplace: str) -> Mechanic:
    """
    Return marketplace mechanic for a given rule category.
    rule_category: the prefix of insight key (e.g. "high_ad_spend", "low_stock")
    marketplace:   normalized marketplace slug ("wildberries", "ozon", "yandex_market")
    """
    mp_map = _REGISTRY.get(marketplace, {})
    return mp_map.get(rule_category, _FALLBACK)


def automation_label(level: AutomationLevel) -> str:
    return {
        "safe_auto":      "Авто-безопасно",
        "human_required": "Нужно решение",
        "blocked":        "Заблокировано",
        "delayed":        "Ждём данные",
        "critical_alert": "Критично",
    }.get(level, level)
