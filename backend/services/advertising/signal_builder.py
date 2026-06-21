"""
Advertising Signal Builder (A5) — deterministic, template-only.

Turns a TRIGGERED RuleEvaluation into a seller-facing SignalDraft following the
PULT doctrine (what / why / meaning / what_to_do / expected_effect) + canonical
primary/alternative actions. NO AI, NO LLM — parametric templates filled from
snapshot-derived evidence. Money-first. Same input → same draft.

recommended_action_key is set so a future dedup against the margin loop is
possible (e.g. ad_destroying_profit → stop_auto_promotion, shared with margin).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from .evaluation import RuleEvaluation


@dataclass(frozen=True)
class SignalDraft:
    problem_type: str
    signal_key: str
    insight_key: str
    recommended_action_key: str
    alternative_action_keys: Tuple[str, ...]
    what: str
    why: str
    meaning: str
    what_to_do: str
    expected_effect: str
    priority_level: str
    expected_effect_type: str
    effect_band: str
    confidence: float


# canonical primary + alternatives per problem_type (dedup-aware)
_ACTIONS: Mapping[str, Tuple[str, Tuple[str, ...]]] = {
    "ad_destroying_profit":       ("stop_auto_promotion", ("reduce_budget", "stop_ad_on_product")),
    "ad_spend_without_sales":     ("stop_ad_on_product", ("reduce_budget", "pause_campaign")),
    "ad_on_unprofitable_product": ("stop_ad_on_product", ("stop_auto_promotion",)),
    "ad_on_low_stock":            ("pause_campaign", ("stop_ad_on_product", "replenish_stock")),
    "ad_on_bad_listing":          ("improve_listing", ("pause_campaign",)),
    "ad_on_oos_risk":             ("pause_campaign", ("replenish_stock",)),
}

_EFFECT_BAND: Mapping[str, str] = {
    "ad_destroying_profit": "high", "ad_spend_without_sales": "high",
    "ad_on_unprofitable_product": "high", "ad_on_low_stock": "medium",
    "ad_on_bad_listing": "medium", "ad_on_oos_risk": "medium",
}

_TEMPLATES: Mapping[str, Mapping[str, str]] = {
    "ad_destroying_profit": {
        "what": "Реклама уводит товар в минус: ДРР {drr}%, прибыль {net_profit} ₽.",
        "why": "Расход на рекламу превышает маржу с продаж — заказы убыточны.",
        "meaning": "Чем больше открутка, тем глубже минус по товару.",
        "what_to_do": "Остановить авто-продвижение или срезать бюджет.",
        "expected_effect": "Возврат маржи.",
    },
    "ad_spend_without_sales": {
        "what": "Реклама тратит {ad_spend} ₽, продаж по товару почти нет.",
        "why": "Бюджет уходит на показы/клики без заказов.",
        "meaning": "Это прямой слив денег без отдачи.",
        "what_to_do": "Снять рекламу с товара.",
        "expected_effect": "Прекращение слива бюджета.",
    },
    "ad_on_unprofitable_product": {
        "what": "Реклама крутится на товаре с низкой маржой ({margin}%).",
        "why": "Продвижение усиливает убыток, а не прибыль.",
        "meaning": "Растущая открутка увеличивает потери.",
        "what_to_do": "Снять рекламу до выхода товара в плюс.",
        "expected_effect": "Остановка потерь.",
    },
    "ad_on_low_stock": {
        "what": "Реклама активна при низком остатке ({stock_units} шт).",
        "why": "Бюджет открутится раньше, чем приедет товар.",
        "meaning": "Платный трафик упрётся в «нет в наличии».",
        "what_to_do": "Поставить рекламу на паузу до пополнения.",
        "expected_effect": "Экономия бюджета.",
    },
    "ad_on_bad_listing": {
        "what": "Реклама ведёт на карточку с проблемами ({active_seo_problems}).",
        "why": "Платный трафик попадает на слабую карточку — низкая конверсия.",
        "meaning": "Деньги тратятся, но карточка не конвертит трафик.",
        "what_to_do": "Сначала исправить карточку.",
        "expected_effect": "Рост отдачи рекламы.",
    },
    "ad_on_oos_risk": {
        "what": "Товар скоро закончится ({days_to_oos} дн), реклама активна.",
        "why": "Бюджет открутится в товар, который вот-вот уйдёт в OOS.",
        "meaning": "Платный трафик попадёт на отсутствующий товар.",
        "what_to_do": "Поставить рекламу на паузу до пополнения.",
        "expected_effect": "Экономия бюджета.",
    },
}

_CONFIDENCE = {"finance": 0.9, "requires_operations": 0.6, "requires_seo": 0.6}


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _fmt(text: str, evidence: Optional[Mapping[str, object]]) -> str:
    return text.format_map(_SafeDict(evidence or {}))


def build_signal(ev: RuleEvaluation, *, marketplace: Optional[str], sku: Optional[str]) -> SignalDraft:
    """Deterministic SignalDraft for a TRIGGERED advertising rule. Template-only."""
    pt = ev.problem_type
    tpl = _TEMPLATES[pt]
    primary, alts = _ACTIONS[pt]
    return SignalDraft(
        problem_type=pt,
        signal_key=f"adv_{pt}",
        insight_key=f"adv_{pt}:{(marketplace or 'unknown')}:{(sku or 'unknown')}",
        recommended_action_key=primary,
        alternative_action_keys=tuple(alts),
        what=_fmt(tpl["what"], ev.evidence),
        why=tpl["why"],
        meaning=tpl["meaning"],
        what_to_do=tpl["what_to_do"],
        expected_effect=tpl["expected_effect"],
        priority_level=ev.severity,
        expected_effect_type=ev.estimated_effect_type,
        effect_band=_EFFECT_BAND[pt],
        confidence=_CONFIDENCE.get(ev.detectability, 0.6),
    )
