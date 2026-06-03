"""
Decision Compression Engine — Sprint 17.

Compresses operational intelligence into ONE primary focus.
Rule-based. No ML. No probability claims beyond what signals provide.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


# ── Priority tiers ─────────────────────────────────────────────────────────────
_TIER: dict[str, int] = {
    "low_stock":       1,
    "high_ad_spend":   2,
    "margin_crisis":   2,
    "seo_opportunity": 3,
    "sales_growth":    4,
}

_TIME_SENS: dict[str, Literal["immediate", "this_week", "this_month"]] = {
    "low_stock":       "immediate",
    "high_ad_spend":   "this_week",
    "margin_crisis":   "this_week",
    "seo_opportunity": "this_month",
    "sales_growth":    "this_month",
}


# ── Chain narratives (2+ signals grouped into one operational story) ────────────
# key: frozenset of cats → (title, reason, expected_impact)
_CHAIN_NARR: dict[frozenset, tuple[str, str, str]] = {
    frozenset({"high_ad_spend", "margin_crisis"}): (
        "Рекламная нагрузка снижает устойчивость экономики товара.",
        "ДРР растёт без компенсации ростом выручки",
        "сжатие маржи в течение 1–2 недель при отсутствии коррекции",
    ),
    frozenset({"seo_opportunity", "high_ad_spend"}): (
        "Рекламные расходы замещают органический рост, который уже доступен.",
        "органические позиции не используются при высокой рекламной нагрузке",
        "упущенный органический трафик при сохранении текущей структуры",
    ),
    frozenset({"low_stock", "sales_growth"}): (
        "Рост продаж создаёт риск обнуления запасов в активный период.",
        "темп продаж опережает текущий запас на складе",
        "выпадение из TopK при stock-out в период активного роста",
    ),
    frozenset({"low_stock", "high_ad_spend"}): (
        "Рекламная нагрузка ускоряет темп продаж при критически низком запасе.",
        "агрессивные ставки при запасе ниже устойчивого горизонта",
        "stock-out в течение нескольких дней при сохранении текущих ставок",
    ),
    frozenset({"margin_crisis", "seo_opportunity"}): (
        "Маржинальное давление ограничивает масштабирование при доступном SEO-потенциале.",
        "SEO-потенциал карточки не реализован на фоне структурного давления на маржу",
        "упущенный органический рост при нарастающем операционном давлении",
    ),
}

# key: frozenset → (primary_action, secondary_action | None)
_CHAIN_ACTIONS: dict[frozenset, tuple[str, str | None]] = {
    frozenset({"high_ad_spend", "margin_crisis"}): (
        "Снизить нагрузку на нерентабельные ключевые слова",
        "Провести структурный разбор затрат — реклама vs себестоимость",
    ),
    frozenset({"seo_opportunity", "high_ad_spend"}): (
        "Запустить SEO-пересборку карточки для усиления органики",
        "Перераспределить бюджет с нерентабельных ключей на органический трафик",
    ),
    frozenset({"low_stock", "sales_growth"}): (
        "Инициировать пополнение склада до исчерпания текущего горизонта",
        "Контролировать темп продаж ежедневно до прихода поставки",
    ),
    frozenset({"low_stock", "high_ad_spend"}): (
        "Снизить рекламные ставки до пополнения склада",
        "Инициировать пополнение склада в приоритетном порядке",
    ),
    frozenset({"margin_crisis", "seo_opportunity"}): (
        "Запустить SEO-пересборку для снижения зависимости от платного трафика",
        None,
    ),
}


# ── Single-signal narratives ───────────────────────────────────────────────────
# key: cat → (title, reason, expected_impact, primary_action)
_SINGLE_NARR: dict[str, tuple[str, str, str, str]] = {
    "low_stock": (
        "Уровень запасов критически низок для поддержания позиций.",
        "остаток не перекрывает расчётный горизонт продаж",
        "потеря позиций и видимости карточки при исчерпании остатка",
        "Инициировать пополнение склада в ближайшие 24–48 часов",
    ),
    "high_ad_spend": (
        "Рекламная нагрузка превышает устойчивый диапазон для данного товара.",
        "ДРР устойчиво выше нормы без признаков снижения",
        "нарастание давления на маржу при сохранении текущих ставок",
        "Снизить нагрузку на нерентабельные ключевые слова",
    ),
    "margin_crisis": (
        "Маржинальное давление достигло операционно значимого уровня.",
        "маржа ниже устойчивого порога без признаков стабилизации",
        "дальнейшее сжатие экономики товара при отсутствии коррекции",
        "Провести структурный разбор затрат и рекламных ключей",
    ),
    "seo_opportunity": (
        "SEO-потенциал карточки не реализован при доступной видимости.",
        "CTR ниже потенциала категории",
        "упущенный органический трафик при сохранении текущей карточки",
        "Запустить авто-пересборку карточки с оптимизацией ключей",
    ),
    "sales_growth": (
        "Подтверждённый рост создаёт операционное окно для масштабирования.",
        "рост выручки устойчив в нескольких периодах подряд",
        "упущенное масштабирование при промедлении с перераспределением бюджета",
        "Масштабировать рекламный бюджет до изменения тренда",
    ),
}


@dataclass
class OperationalFocus:
    focus_id:         str
    title:            str
    reason:           str
    root_cause:       str
    expected_impact:  str
    time_sensitivity: Literal["immediate", "this_week", "this_month"]
    confidence:       int
    is_stable:        bool   # half-life: persistent but not worsening
    linked_signals:   list[str] = field(default_factory=list)
    linked_scenarios: list[str] = field(default_factory=list)
    linked_chains:    list[str] = field(default_factory=list)
    primary_action:   str = ""
    secondary_action: str | None = None
    # Sprint 30: temporal operational momentum
    focus_momentum:   str = "active"   # active | slowing | historical | persistent
    effective_weight: int = 0          # weight after decay × lifecycle multipliers


# ── Sprint 30: Temporal momentum helpers ──────────────────────────────────────

# Decay → focus multiplier; historical signals are ineligible for focus
_DECAY_MULT: dict[str, float] = {
    "persistent": 1.0,
    "fresh":      1.0,
    "aging":      1.0,
    "fading":     0.72,
    "stale":      0.0,   # historical
}

# Lifecycle stage → focus multiplier
_LC_MULT: dict[str, float] = {
    "recurring":   1.15,
    "confirmed":   1.0,
    "stabilized":  0.9,
    "emerging":    0.85,
    "resolved":    0.5,
}


def _momentum_for(decay_state: str | None, lifecycle_stage: str | None) -> str:
    """Map decay + lifecycle state to focus momentum label."""
    if decay_state == "persistent" or lifecycle_stage == "recurring":
        return "persistent"
    if decay_state in ("fresh", "aging", None):
        return "active"
    if decay_state == "fading":
        return "slowing"
    return "historical"   # stale


def _eff_weight(ins: Any) -> float:
    """Effective focus weight after applying decay and lifecycle multipliers."""
    w     = float(getattr(ins, "weight", 0) or 0)
    decay = getattr(ins, "signal_decay_state", None) or "fresh"
    lc    = getattr(ins, "signal_lifecycle_stage", None) or "confirmed"
    dm    = _DECAY_MULT.get(decay, 1.0)
    lm    = _LC_MULT.get(lc, 1.0)
    return w * dm * lm


def _dominant_momentum(insights: list[Any]) -> str:
    """Return the dominant momentum across a set of insights (priority: persistent > active > slowing)."""
    moms = {_momentum_for(
        getattr(i, "signal_decay_state", None),
        getattr(i, "signal_lifecycle_stage", None),
    ) for i in insights}
    for m in ("persistent", "active", "slowing", "historical"):
        if m in moms:
            return m
    return "active"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cat(key: str) -> str:
    base = key.split(":")[0]
    return base[5:] if base.startswith("demo_") else base


def _fid(root_cause: str) -> str:
    return "focus:" + hashlib.md5(root_cause.encode()).hexdigest()[:8]


def _half_life(
    root_cause: str,
    notif_counts: dict[str, int],
    resolved_history: dict[str, datetime],
) -> bool:
    """
    True when focus has been surfaced 3+ times without recent resolution.
    Signals "persistent but stable" — reduce visual dominance.
    """
    if notif_counts.get(root_cause, 0) < 3:
        return False
    res_at = resolved_history.get(root_cause)
    if res_at and (datetime.utcnow() - res_at).days <= 30:
        return False
    return True


def _avg_conf(insights: list[Any], cats: frozenset | set) -> int:
    matched = [i for i in insights if _cat(i.key) in cats]
    if not matched:
        return 70
    return int(sum(i.confidence or 0 for i in matched) / len(matched))


# ── Public API ────────────────────────────────────────────────────────────────

def compute_operational_focus(
    insights:          list[Any],
    chains:            list[Any],
    scenarios:         list[Any],
    resolved_history:  dict[str, datetime] | None = None,
    notif_counts:      dict[str, int] | None = None,
    stability_credit:  float = 0.0,
) -> OperationalFocus | None:
    """
    Returns ONE operational focus from the highest-priority signal cluster.

    Priority: irreversible damage → cascading chains → financial leakage
              → seasonal pressure → growth opportunities.

    stability_credit (0-1): stable operators require higher evidence.
    Inputs accept any object with .key, .status, .is_secondary, .confidence,
    and optionally .weight attrs.
    """
    _rh = resolved_history or {}
    _nc = notif_counts     or {}

    # Minimum weight for focus entry; raised for stable operators
    _min_weight = 65.0 if stability_credit >= 0.7 else 50.0

    # Sprint 30: exclude historical (stale, no recurrence) from focus consideration.
    # Chain collapse is implicit — if root is historical it won't appear in active_cats,
    # so no chain matching occurs and consequence becomes single-signal focus.
    active = [
        i for i in insights
        if getattr(i, "status", "active") not in ("resolved", "dismissed")
        and not getattr(i, "is_secondary", False)
        and (getattr(i, "confidence", 0) or 0) >= 50
        and (getattr(i, "weight", _min_weight) or _min_weight) >= _min_weight
        and _momentum_for(
            getattr(i, "signal_decay_state", None),
            getattr(i, "signal_lifecycle_stage", None),
        ) != "historical"
    ]
    if not active:
        return None

    active_cats: set[str] = {_cat(i.key) for i in active}

    # ── Try chain narratives — use effective weight for evidence threshold ──────
    best_combo: frozenset | None = None
    best_tier:  int = 99
    best_eff_w: float = 0.0
    for combo in _CHAIN_NARR:
        if combo <= active_cats:
            combo_insights = [i for i in active if _cat(i.key) in combo]
            max_eff_w = max(_eff_weight(i) for i in combo_insights) if combo_insights else 0.0
            if max_eff_w < 40:
                continue  # combined evidence too weak after decay
            tier = min(_TIER.get(c, 9) for c in combo)
            if tier < best_tier or (tier == best_tier and max_eff_w > best_eff_w):
                best_tier  = tier
                best_combo = combo
                best_eff_w = max_eff_w

    if best_combo is not None:
        title, reason, impact = _CHAIN_NARR[best_combo]
        primary_action, secondary_action = _CHAIN_ACTIONS.get(
            best_combo, ("Проверить операционный статус товара", None)
        )
        root_cause     = min(best_combo, key=lambda c: _TIER.get(c, 9))
        time_sens      = _TIME_SENS.get(root_cause, "this_week")
        combo_insights = [i for i in active if _cat(i.key) in best_combo]

        linked_sig = [i.key for i in active if _cat(i.key) in best_combo]
        linked_ch  = [
            c.id for c in chains
            if _cat(getattr(c, "root_insight_key", "")) in best_combo
            or _cat(getattr(c, "consequence_insight_key", "")) in best_combo
        ]
        linked_sc  = [
            s.scenario_id for s in scenarios
            if _cat(getattr(s, "source_insight", "")) in best_combo
        ]

        return OperationalFocus(
            focus_id=_fid(root_cause),
            title=title,
            reason=reason,
            root_cause=root_cause,
            expected_impact=impact,
            time_sensitivity=time_sens,
            confidence=_avg_conf(active, best_combo),
            is_stable=_half_life(root_cause, _nc, _rh),
            linked_signals=linked_sig,
            linked_scenarios=linked_sc,
            linked_chains=linked_ch,
            primary_action=primary_action,
            secondary_action=secondary_action,
            focus_momentum=_dominant_momentum(combo_insights),
            effective_weight=int(best_eff_w),
        )

    # ── Single-signal focus — sort by effective weight (Sprint 30) ────────────
    best = max(active, key=lambda i: (
        _eff_weight(i),
        -_TIER.get(_cat(i.key), 9),
        (getattr(i, "confidence", 0) or 0),
    ))
    cat = _cat(best.key)

    if cat not in _SINGLE_NARR:
        return None

    title, reason, impact, primary_action = _SINGLE_NARR[cat]

    linked_sc = [
        s.scenario_id for s in scenarios
        if _cat(getattr(s, "source_insight", "")) == cat
    ]
    linked_ch = [
        c.id for c in chains
        if _cat(getattr(c, "root_insight_key", "")) == cat
        or _cat(getattr(c, "consequence_insight_key", "")) == cat
    ]

    return OperationalFocus(
        focus_id=_fid(cat),
        title=title,
        reason=reason,
        root_cause=cat,
        expected_impact=impact,
        time_sensitivity=_TIME_SENS.get(cat, "this_week"),
        confidence=getattr(best, "confidence", None) or 70,
        is_stable=_half_life(cat, _nc, _rh),
        linked_signals=[best.key],
        linked_scenarios=linked_sc,
        linked_chains=linked_ch,
        primary_action=primary_action,
        secondary_action=None,
        focus_momentum=_momentum_for(
            getattr(best, "signal_decay_state", None),
            getattr(best, "signal_lifecycle_stage", None),
        ),
        effective_weight=int(_eff_weight(best)),
    )


def compress_scenarios(scenarios: list[Any]) -> list[Any]:
    """
    Keep one scenario per path_type (highest confidence wins).
    Returns [conservative?, balanced?, aggressive?] in that order.
    """
    by_path: dict[str, Any] = {}
    for s in scenarios:
        pt = getattr(s, "path_type", None) or s.get("path_type", "") if isinstance(s, dict) else s.path_type
        existing = by_path.get(pt)
        conf = getattr(s, "confidence", 0) if not isinstance(s, dict) else s.get("confidence", 0)
        ex_conf = getattr(existing, "confidence", 0) if existing and not isinstance(existing, dict) else (existing or {}).get("confidence", 0) if existing else 0
        if existing is None or conf > ex_conf:
            by_path[pt] = s
    return [by_path[p] for p in ("conservative", "balanced", "aggressive") if p in by_path]


def focus_briefing_for_telegram(focus: OperationalFocus) -> str:
    """Operational briefing append-text for Telegram. Returns '' if focus is stable."""
    if focus.is_stable:
        return ""
    lines = [
        "\n\n📍 <b>Операционный фокус:</b>",
        f"<i>{focus.title}</i>",
        f"↳ {focus.reason}",
        f"↳ Вероятное следствие: {focus.expected_impact}",
    ]
    if focus.primary_action:
        lines.append(f"↳ {focus.primary_action}")
    return "\n".join(lines)
