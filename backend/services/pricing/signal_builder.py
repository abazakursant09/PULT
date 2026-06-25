"""
Pricing Signal Builder (A3-pre) — deterministic, template-only.

Turns a TRIGGERED PricingRuleEvaluation into a seller-facing SignalDraft following
the PULT doctrine (what / why / meaning / what_to_do / expected_effect). No AI, no
language-model use, no forecast — parametric templates filled from observed evidence.

`recommended_action_key` is intentionally None in A3-pre: the set_price binding is a
LATER sprint (A3-bind). This sprint surfaces the margin problem honestly; it does NOT
compute a price, propose set_price, or claim an effect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from .evaluation import PricingRuleEvaluation


@dataclass(frozen=True)
class SignalDraft:
    problem_type: str
    signal_key: str
    insight_key: str
    category: str
    recommended_action_key: Optional[str]
    alternative_action_keys: Tuple[str, ...]
    what: str
    why: str
    meaning: str
    what_to_do: str
    expected_effect: str
    priority_level: str
    effect_type: str
    effect_band: str
    confidence: float


_EFFECT_BAND: Mapping[str, str] = {
    "negative_margin": "high", "margin_below_target": "medium", "price_below_floor": "medium",
}

# confidence by detectability (deterministic, not a forecast)
_CONFIDENCE = {"finance": 0.8, "rule": 0.75}

_TEMPLATES: Mapping[str, Mapping[str, str]] = {
    "negative_margin": {
        "what": "Товар продаётся с отрицательной маржой.",
        "why": "Чистая прибыль по товару за период отрицательная при текущей цене.",
        "meaning": "Каждая продажа уносит деньги — цена не покрывает затраты.",
        "what_to_do": "Проверить цену товара относительно затрат.",
        "expected_effect": "Возврат к положительной марже при корректной цене.",
    },
    "margin_below_target": {
        "what": "Маржа товара ниже целевого уровня.",
        "why": "Наблюдаемая маржа положительна, но ниже заданного порога.",
        "meaning": "Товар зарабатывает меньше, чем должен по цели продавца.",
        "what_to_do": "Проверить цену относительно целевой маржи.",
        "expected_effect": "Потенциал восстановления маржи до целевого уровня.",
    },
    "price_below_floor": {
        "what": "Текущая цена ниже заданного ценового пола.",
        "why": "Цена товара ниже min_price из правила ценообразования продавца.",
        "meaning": "Товар продаётся дешевле, чем разрешает правило продавца.",
        "what_to_do": "Проверить цену относительно установленного пола.",
        "expected_effect": "Возврат цены к допустимому диапазону правила.",
    },
}


def build_signal(ev: PricingRuleEvaluation, *, marketplace: Optional[str],
                 sku: Optional[str]) -> SignalDraft:
    """Deterministic SignalDraft for a TRIGGERED pricing rule. Template-only."""
    pt = ev.problem_type
    tpl = _TEMPLATES[pt]
    mp = marketplace or "unknown"
    sk = sku or "unknown"
    return SignalDraft(
        problem_type=pt,
        signal_key=f"pricing_{pt}",
        insight_key=f"pricing_{pt}:{mp}:{sk}",
        category=ev.category,
        recommended_action_key=None,           # A3-bind wires set_price later
        alternative_action_keys=tuple(),
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
