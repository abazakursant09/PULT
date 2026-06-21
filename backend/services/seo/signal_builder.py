"""
SEO Signal Builder (A5) — deterministic, template-only.

Turns a TRIGGERED RuleEvaluation into a seller-facing SignalDraft following the
PULT doctrine (what / why / meaning / what_to_do / expected_effect) plus the
canonical recommended action + alternatives. NO AI, NO LLM — pure parametric
templates filled from snapshot-derived evidence. Same input → same draft.

Action mapping note (A5 constraint): description_missing and description_too_short
both use `improve_description` (NOT generate_description).
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


# canonical primary + alternative actions per problem_type
_ACTIONS: Mapping[str, Tuple[str, Tuple[str, ...]]] = {
    "required_attributes_missing": ("complete_required_fields", ()),
    "wrong_category_placement":    ("fix_category_placement", ("assign_subcategory",)),
    "title_too_short":             ("optimize_title", ("insert_primary_keyword",)),
    "title_too_long":              ("trim_title", ("restructure_title",)),
    "attributes_incomplete":       ("fill_optional_attributes", ("complete_required_fields",)),
    "filter_attributes_missing":   ("add_filter_attributes", ("align_filter_membership",)),
    "variant_attributes_missing":  ("add_variant_attributes", ("fill_optional_attributes",)),
    "attribute_values_invalid":    ("fix_attribute_values", ("complete_required_fields",)),
    "description_missing":         ("improve_description", ()),       # NOT generate_description
    "description_too_short":       ("improve_description", ()),       # NOT enrich/generate
    "content_completeness_low":    ("fill_optional_attributes", ("improve_description", "add_media")),
    "media_below_minimum":         ("add_media", ()),
}

# qualitative effect band per problem_type (Signal Catalog)
_EFFECT_BAND: Mapping[str, str] = {
    "required_attributes_missing": "high", "wrong_category_placement": "high",
    "title_too_short": "medium", "title_too_long": "low",
    "attributes_incomplete": "medium", "filter_attributes_missing": "high",
    "variant_attributes_missing": "medium", "attribute_values_invalid": "medium",
    "description_missing": "medium", "description_too_short": "low",
    "content_completeness_low": "medium", "media_below_minimum": "medium",
}

# doctrine templates ({placeholders} filled from evidence; missing keys tolerated)
_TEMPLATES: Mapping[str, Mapping[str, str]] = {
    "required_attributes_missing": {
        "what": "Не заполнены обязательные характеристики ({filled_count} из {required_count}).",
        "why": "Маркетплейс исключает неполные карточки из фильтров и снижает ранг.",
        "meaning": "Товар не находят по характеристикам — потерянный охват.",
        "what_to_do": "Заполнить обязательные характеристики категории.",
        "expected_effect": "Возврат карточки в фильтры.",
    },
    "wrong_category_placement": {
        "what": "Карточка размещена не в той категории.",
        "why": "Ранжирование и фильтры считаются внутри категории.",
        "meaning": "Карточка соревнуется не за свой спрос — почти невидима.",
        "what_to_do": "Перенести карточку в корректную категорию.",
        "expected_effect": "Доступ к целевому спросу.",
    },
    "title_too_short": {
        "what": "Заголовок короче нормы ({title_length} симв при минимуме {title_min_len}).",
        "why": "Мало вхождений ключей — узкий охват запросов.",
        "meaning": "Карточку находят по меньшему числу запросов.",
        "what_to_do": "Переписать заголовок с ключами и структурой.",
        "expected_effect": "Шире находимость.",
    },
    "title_too_long": {
        "what": "Заголовок превышает лимит ({title_length} при максимуме {title_max_len}) — обрезается.",
        "why": "Обрезка теряет ключи и читаемость.",
        "meaning": "Часть запросов и смысла не доходит до покупателя.",
        "what_to_do": "Сократить заголовок, сохранив ключи.",
        "expected_effect": "Сохранение ключей.",
    },
    "attributes_incomplete": {
        "what": "Заполнено мало характеристик ({attribute_fill_rate}).",
        "why": "Полнота карточки — фактор ранга и фильтров.",
        "meaning": "Ниже в выдаче и часть фильтров недоступна.",
        "what_to_do": "Дозаполнить характеристики.",
        "expected_effect": "Рост ранга и охвата.",
    },
    "filter_attributes_missing": {
        "what": "Нет значений для фильтруемых атрибутов.",
        "why": "Без них карточка выпадает из соответствующих фильтров.",
        "meaning": "Покупатели с фильтром не увидят товар.",
        "what_to_do": "Добавить фильтруемые атрибуты.",
        "expected_effect": "Включение в фильтры.",
    },
    "variant_attributes_missing": {
        "what": "Не заданы вариативные атрибуты (размер/цвет).",
        "why": "Вариации влияют на охват и склейку карточек.",
        "meaning": "Теряются переходы по вариациям спроса.",
        "what_to_do": "Добавить вариативные атрибуты.",
        "expected_effect": "Шире находимость.",
    },
    "attribute_values_invalid": {
        "what": "Значения атрибутов в неверном формате.",
        "why": "Невалидные значения не попадают в фильтры.",
        "meaning": "Атрибут заполнен, но не работает на находимость.",
        "what_to_do": "Исправить формат значений.",
        "expected_effect": "Включение в фильтры.",
    },
    "description_missing": {
        "what": "Описание отсутствует.",
        "why": "Описание индексируется и несёт семантику.",
        "meaning": "Потерян канал индексации по запросам.",
        "what_to_do": "Добавить описание с ключами и структурой.",
        "expected_effect": "Индексация по описанию.",
    },
    "description_too_short": {
        "what": "Описание короче нормы ({description_length} симв при минимуме {description_min_len}).",
        "why": "Мало семантики — уже охват.",
        "meaning": "Меньше запросов, по которым находят товар.",
        "what_to_do": "Дополнить описание ключами и структурой.",
        "expected_effect": "Шире охват.",
    },
    "content_completeness_low": {
        "what": "Общая полнота карточки низкая ({content_completeness}).",
        "why": "Полные карточки ранжируются выше.",
        "meaning": "Карточка проигрывает в выдаче более полным.",
        "what_to_do": "Дозаполнить ключевые блоки карточки.",
        "expected_effect": "Рост ранга.",
    },
    "media_below_minimum": {
        "what": "Изображений меньше минимума ({image_count} из {media_min_images}).",
        "why": "Медиа влияет на конверсию и доверие.",
        "meaning": "Покупатель не получает достаточно визуала — реже покупает.",
        "what_to_do": "Добавить изображения до минимума.",
        "expected_effect": "Рост конверсии.",
    },
}

_CONFIDENCE = {"static_card": 0.9, "requires_search_data": 0.5}


class _SafeDict(dict):
    def __missing__(self, key):  # tolerate any missing evidence key
        return "{" + key + "}"


def _fmt(text: str, evidence: Optional[Mapping[str, object]]) -> str:
    return text.format_map(_SafeDict(evidence or {}))


def build_signal(ev: RuleEvaluation, *, marketplace: Optional[str], sku: Optional[str]) -> SignalDraft:
    """Deterministic SignalDraft for a TRIGGERED rule evaluation. Template-only."""
    pt = ev.problem_type
    tpl = _TEMPLATES[pt]
    primary, alts = _ACTIONS[pt]
    return SignalDraft(
        problem_type=pt,
        signal_key=f"seo_{pt}",
        insight_key=f"seo_{pt}:{(marketplace or 'unknown')}:{(sku or 'unknown')}",
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
        confidence=_CONFIDENCE.get(ev.detectability, 0.5),
    )
