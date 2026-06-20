"""
Stable insight key builder — единый источник идентичности рантайм-инсайта.

Канонизирует (marketplace, sku) ПЕРЕД сборкой ключа, чтобы один товар не порождал
разные ключи из-за форматных расхождений импорта ("Wildberries" vs "wb",
"  sku " vs "SKU"). Ключ — будущий анкор дедупликации для Insight → Decision.

Форма ключа сохранена: f"{insight_type}:{marketplace}:{sku}".

promotable=False, когда sku отсутствует — или это плейсхолдер "unknown", которым
агрегатор финансов (routers/action_engine.py) заменяет пустой sku
(`finance dict keyed by row.sku or "unknown"`). Такой ключ годен ТОЛЬКО для
отображения/статуса; он НЕ может быть долговременным анкором Decision — иначе
разные товары без sku схлопнулись бы в одно решение.

Чистый/детерминированный: только нормализация + форматирование. Без БД, без
состояния, без нового реестра/идентичности.

СОВМЕСТИМОСТЬ (accept-reset): нормализация меняет строку ключа относительно
старой сырой схемы. Ранее сохранённые InsightRecord.status и Telegram-дедуп по
сырым ключам могут разово сброситься. На текущем этапе это допустимо; backfill
в этом срезе НЕ делается.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

from services.product_resolver import normalize_marketplace, normalize_sku

# Плейсхолдер пустого sku из агрегации финансов в action_engine. normalize_sku
# его не уберёт (это непустая строка), поэтому ловим сентинел здесь —
# нормализованный sku в верхнем регистре.
_MISSING_SKU = "UNKNOWN"


class InsightKey(NamedTuple):
    key: str
    promotable: bool


def build_insight_key(
    insight_type: str, marketplace: Optional[str], sku: Optional[str]
) -> InsightKey:
    """
    Стабильный ключ инсайта из (insight_type, marketplace, sku).

    marketplace → normalize_marketplace (канон-слаг). sku → normalize_sku
    (trim/upper, пустой → None). Пустой/отсутствующий sku или плейсхолдер
    "unknown" → key '<type>:<mp>:unknown', promotable=False. Иначе
    '<type>:<mp>:<sku>', promotable=True.
    """
    mp = normalize_marketplace(marketplace)
    sku_n = normalize_sku(sku)
    if sku_n is None or sku_n == _MISSING_SKU:
        return InsightKey(key=f"{insight_type}:{mp}:unknown", promotable=False)
    return InsightKey(key=f"{insight_type}:{mp}:{sku_n}", promotable=True)
