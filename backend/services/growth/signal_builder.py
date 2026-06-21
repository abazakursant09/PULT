"""
Growth Signal Builder (A5) — deterministic, template-only.

Turns a TRIGGERED GrowthRuleEvaluation into a seller-facing SignalDraft following
the PULT doctrine (what / why / meaning / recommended_action / expected_effect) +
a canonical action. No AI, no language-model use, no forecast — parametric
templates filled from snapshot-derived evidence.

No Fake Impact: expected_effect never promises guaranteed growth, exact revenue /
profit, or a forecast — only "потенциал роста", "возможность", "снизить риск
ограничения роста", "проверить гипотезу". Growth Engine surfaces opportunity, it
does not predict money.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from .evaluation import GrowthRuleEvaluation


@dataclass(frozen=True)
class SignalDraft:
    problem_type: str
    signal_key: str
    insight_key: str
    category: str
    recommended_action_key: str
    alternative_action_keys: Tuple[str, ...]
    what: str
    why: str
    meaning: str
    what_to_do: str          # recommended_action (human text)
    expected_effect: str
    priority_level: str
    effect_type: str
    effect_band: str
    confidence: float


# primary action + alternatives per rule
_ACTIONS: Mapping[str, Tuple[str, Tuple[str, ...]]] = {
    "profitable_ad_candidate":    ("start_advertising", ("dismiss",)),
    "seo_leverage_candidate":     ("improve_listing", ("dismiss",)),
    "review_leverage_candidate":  ("handle_reviews", ("dismiss",)),
    "stock_expansion_candidate":  ("replenish_stock", ("dismiss",)),
    "margin_expansion_candidate": ("review_price_upside", ("dismiss",)),
}

_EFFECT_BAND: Mapping[str, str] = {
    "profitable_ad_candidate": "high", "seo_leverage_candidate": "medium",
    "review_leverage_candidate": "medium", "stock_expansion_candidate": "high",
    "margin_expansion_candidate": "medium",
}

# confidence by detectability (deterministic, not a forecast)
_CONFIDENCE = {"finance": 0.8, "operations": 0.75, "signals": 0.7}

_TEMPLATES: Mapping[str, Mapping[str, str]] = {
    "profitable_ad_candidate": {
        "what": "Товар прибыльный, но не используется для роста через рекламу.",
        "why": "По товару есть прибыль, но рекламного давления почти нет.",
        "meaning": "PULT видит возможность масштабировать продажи без поиска нового товара.",
        "what_to_do": "Проверить запуск рекламы на этот товар.",
        "expected_effect": "Потенциал роста выручки при сохранении контроля маржи.",
    },
    "seo_leverage_candidate": {
        "what": "Товар уже продаётся, но SEO ограничивает рост.",
        "why": "По карточке есть активные SEO-сигналы.",
        "meaning": "Часть спроса может не доходить до товара из-за слабой карточки.",
        "what_to_do": "Исправить SEO-проблемы карточки.",
        "expected_effect": "Рост органического охвата карточки.",
    },
    "review_leverage_candidate": {
        "what": "Товар продаётся, но отзывы могут ограничивать рост.",
        "why": "Есть активные репутационные сигналы.",
        "meaning": "Часть покупателей может не доверять карточке.",
        "what_to_do": "Разобрать отзывы, которые требуют реакции.",
        "expected_effect": "Снижение репутационного трения.",
    },
    "stock_expansion_candidate": {
        "what": "Товар продаётся, но остатки ограничивают рост.",
        "why": "Текущий остаток ниже заданного порога.",
        "meaning": "Рост может упереться в отсутствие товара.",
        "what_to_do": "Пополнить остатки.",
        "expected_effect": "Снижение риска потерять спрос из-за нехватки товара.",
    },
    "margin_expansion_candidate": {
        "what": "Товар прибыльный, есть пространство для проверки цены.",
        "why": "Маржа высокая и товар приносит прибыль.",
        "meaning": "Можно проверить, не оставляет ли продавец деньги на столе.",
        "what_to_do": "Проверить возможность повышения цены.",
        "expected_effect": "Потенциал роста маржи без увеличения объёма продаж.",
    },
}


def build_signal(ev: GrowthRuleEvaluation, *, marketplace: Optional[str],
                 sku: Optional[str]) -> SignalDraft:
    """Deterministic SignalDraft for a TRIGGERED growth rule. Template-only."""
    pt = ev.problem_type
    tpl = _TEMPLATES[pt]
    primary, alts = _ACTIONS[pt]
    mp = marketplace or "unknown"
    sk = sku or "unknown"
    return SignalDraft(
        problem_type=pt,
        signal_key=f"growth_{pt}",
        insight_key=f"growth_{pt}:{mp}:{sk}",
        category=ev.category,
        recommended_action_key=primary,
        alternative_action_keys=tuple(alts),
        what=tpl["what"],
        why=tpl["why"],
        meaning=tpl["meaning"],
        what_to_do=tpl["what_to_do"],
        expected_effect=tpl["expected_effect"],
        priority_level=ev.severity,
        effect_type=ev.estimated_effect_type,
        effect_band=_EFFECT_BAND[pt],
        confidence=_CONFIDENCE.get(ev.detectability, 0.7),
    )
