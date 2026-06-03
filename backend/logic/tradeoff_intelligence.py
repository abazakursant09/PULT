"""
Operational Tradeoff Intelligence — Sprint 34.

Maps each stabilization action to its secondary operational consequences.
NOT a risk model. NOT a warning system.
Tradeoffs: what temporarily arises AFTER intervention — and why that's acceptable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class OperationalTradeoff:
    side_effect_type:    str            # operational category of secondary pressure
    severity:            str            # mild | moderate | significant
    expected_duration_days: int         # approximate days until secondary pressure subsides
    reversibility:       str            # reversible | conditionally_reversible | monitor_required
    confidence_band:     str            # low | moderate | stable
    tradeoff_note:       str            # one-sentence restrained description of secondary effect
    stabilization_benefit: str          # what the primary intervention achieves
    acceptable_when:     list[str]      # conditions under which tradeoff is worth taking
    avoid_when:          list[str]      # conditions under which to delay or reconsider


# ── Tradeoff Registry ─────────────────────────────────────────────────────────
# Keyed by insight category (first segment of insight key).
# Only categories with meaningful tradeoffs are registered.
# Omission = no significant tradeoff for that signal type.

_REGISTRY: dict[str, OperationalTradeoff] = {
    "high_ad_spend": OperationalTradeoff(
        side_effect_type="revenue_velocity_drop",
        severity="mild",
        expected_duration_days=10,  # midpoint 5–14d
        reversibility="reversible",
        confidence_band="stable",
        tradeoff_note=(
            "Снижение рекламной нагрузки временно замедляет оборот "
            "на период переходной оптимизации."
        ),
        stabilization_benefit="Маржа стабилизируется. Unit-экономика восстанавливает устойчивость.",
        acceptable_when=[
            "маржинальное давление активно",
            "рекламный расход выше 25% от выручки",
        ],
        avoid_when=[
            "пиковый сезон или активная акция",
            "запуск нового SKU",
        ],
    ),

    "margin_crisis": OperationalTradeoff(
        side_effect_type="ctr_instability",
        severity="moderate",
        expected_duration_days=14,  # midpoint 7–21d
        reversibility="reversible",
        confidence_band="moderate",
        tradeoff_note=(
            "Корректировка цены временно нарушает CTR-сигнал "
            "до переиндексации карточки площадкой."
        ),
        stabilization_benefit="Unit-экономика выходит в устойчивую зону. Структурный дрейф останавливается.",
        acceptable_when=[
            "маржа ниже порога безубыточности",
            "без активных рекламных кампаний на этот SKU",
        ],
        avoid_when=[
            "период активных продаж",
            "продукт на стадии рейтингового роста",
        ],
    ),

    "seo_opportunity": OperationalTradeoff(
        side_effect_type="indexing_instability",
        severity="mild",
        expected_duration_days=10,  # midpoint 5–14d
        reversibility="reversible",
        confidence_band="stable",
        tradeoff_note=(
            "Пересмотр контента карточки вызывает кратковременную "
            "нестабильность индексации и позиций."
        ),
        stabilization_benefit="Органический трафик восстанавливается на более высокой базе.",
        acceptable_when=[
            "карточка давно не обновлялась",
            "нет активных рекламных кампаний",
        ],
        avoid_when=[
            "высокая рекламная активность на SKU",
            "критический период продаж",
        ],
    ),
}


def get_tradeoff(category: str) -> Optional[OperationalTradeoff]:
    """Return tradeoff for insight category, or None if no meaningful tradeoff exists."""
    return _REGISTRY.get(category)


def build_tradeoff_note(tradeoff: OperationalTradeoff, trajectory_state: Optional[str]) -> str:
    """
    Return contextual tradeoff note.
    Escalating trajectory gets slightly more explicit secondary-risk framing.
    """
    if trajectory_state in ("escalating", "structurally_accumulating"):
        return (
            f"{tradeoff.tradeoff_note} "
            f"На текущей траектории вмешательство оправдано: "
            f"{tradeoff.stabilization_benefit.lower()}"
        )
    return tradeoff.tradeoff_note
