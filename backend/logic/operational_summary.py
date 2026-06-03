"""
Operational Intelligence Summary — Sprint 25.
Aggregates signal lifecycle, portfolio state, and operator load into a narrative summary.
No ML. Pure operational heuristics on current insight state.
"""
from __future__ import annotations

import dataclasses
from typing import Optional, Literal

_CAT_LABELS: dict[str, str] = {
    "seo_opportunity": "SEO",
    "high_ad_spend":   "рекламных расходов",
    "margin_crisis":   "маржинального давления",
    "low_stock":       "складских остатков",
    "sales_growth":    "роста продаж",
    "high_rating":     "рейтинга",
}

_MP_SHORT: dict[str, str] = {
    "wildberries":   "WB",
    "ozon":          "Ozon",
    "yandex_market": "ЯМ",
}

PortfolioDirection = Literal["stabilizing", "unstable", "mixed", "expanding_pressure"]
OperatorLoad       = Literal["low", "moderate", "high"]


@dataclasses.dataclass
class OperationalSummary:
    summary_type:          Literal["daily", "weekly"]
    operational_shift:     str               # primary narrative sentence
    dominant_pressure:     Optional[str]
    improving_systems:     list[str]
    destabilizing_systems: list[str]
    recurring_patterns:    list[str]         # category slugs
    stabilized_patterns:   list[str]         # category slugs
    portfolio_direction:   PortfolioDirection
    operator_load:         OperatorLoad
    summary_note:          str
    narrative_lines:       list[str]         # pre-built display lines, max 4


def _cat(ins) -> str:
    """Base category slug from insight, stripping demo_ prefix."""
    key = getattr(ins, "key", "") or ""
    raw = key.split(":")[0]
    return raw[len("demo_"):] if raw.startswith("demo_") else raw


def _stage(ins) -> str:
    return getattr(ins, "signal_lifecycle_stage", "") or ""


def build_operational_summary(
    *,
    insights:           list,
    portfolio_patterns: list,
    resolved_history:   dict,
    fatigue_score:      float,
    stability_credit:   float,
    summary_type:       Literal["daily", "weekly"] = "daily",
) -> OperationalSummary:
    """
    Build an operational narrative from current insight state.
    Describes system trajectory, not individual alerts.
    insights: InsightItem objects (duck-typed, not Pydantic).
    portfolio_patterns: PortfolioPattern dataclass objects.
    """
    active = [
        i for i in insights
        if getattr(i, "status", "") not in ("resolved", "dismissed")
    ]

    # ── Lifecycle buckets ─────────────────────────────────────────────────────
    recurring   = [i for i in active if _stage(i) == "recurring"]
    stabilized  = [i for i in active if _stage(i) == "stabilized"]
    emerging    = [i for i in active if _stage(i) == "emerging"]
    confirmed   = [i for i in active if _stage(i) == "confirmed"]
    resolved_lc = [i for i in active if _stage(i) == "resolved"]

    # Preserve insertion order, deduplicate
    recurring_cats  = list(dict.fromkeys(_cat(i) for i in recurring))
    stabilized_cats = list(dict.fromkeys(_cat(i) for i in stabilized + resolved_lc))

    n_recurring  = len(recurring)
    n_stabilized = len(stabilized) + len(resolved_lc)
    n_emerging   = len(emerging)

    # ── Portfolio pattern analysis ────────────────────────────────────────────
    has_systemic = any(
        getattr(p, "stabilization_complexity", "") == "systemic"
        and getattr(p, "confidence", 0) >= 70
        for p in portfolio_patterns
    )
    has_localized = any(
        getattr(p, "stabilization_complexity", "") == "localized"
        for p in portfolio_patterns
    )

    # Dominant marketplace from systemic patterns
    systemic_mp: str | None = None
    for p in portfolio_patterns:
        if getattr(p, "stabilization_complexity", "") == "systemic":
            mp = getattr(p, "marketplace", None) or ""
            systemic_mp = _MP_SHORT.get(mp) or mp or None
            break

    # ── Portfolio direction ───────────────────────────────────────────────────
    portfolio_direction: PortfolioDirection
    if has_systemic and has_localized:
        portfolio_direction = "expanding_pressure"
    elif n_recurring >= 2 and (has_systemic or fatigue_score > 0.5):
        portfolio_direction = "unstable"
    elif n_recurring >= 1 and n_stabilized == 0:
        portfolio_direction = "unstable"
    elif n_stabilized > n_recurring and fatigue_score < 0.4:
        portfolio_direction = "stabilizing"
    elif n_recurring > 0 and n_stabilized > 0:
        portfolio_direction = "mixed"
    else:
        portfolio_direction = "stabilizing"

    # ── Operator load ─────────────────────────────────────────────────────────
    operator_load: OperatorLoad
    if fatigue_score > 0.6:
        operator_load = "high"
    elif fatigue_score > 0.35:
        operator_load = "moderate"
    else:
        operator_load = "low"

    # ── Dominant pressure ─────────────────────────────────────────────────────
    dominant_cat = recurring_cats[0] if recurring_cats else None
    if not dominant_cat:
        for i in confirmed:
            if getattr(i, "type", "") == "warning":
                dominant_cat = _cat(i)
                break
    dominant_pressure = _CAT_LABELS.get(dominant_cat) if dominant_cat else None

    # ── Improving / destabilizing systems ────────────────────────────────────
    improving_systems: list[str] = []
    for cat in stabilized_cats:
        label = _CAT_LABELS.get(cat, cat)
        if label not in improving_systems:
            improving_systems.append(label)
    # Confirmed positive signals also count as improving
    for i in confirmed:
        if getattr(i, "type", "") == "positive":
            label = _CAT_LABELS.get(_cat(i), _cat(i))
            if label not in improving_systems:
                improving_systems.append(label)

    destabilizing_systems: list[str] = []
    for cat in recurring_cats:
        label = _CAT_LABELS.get(cat, cat)
        if label not in destabilizing_systems:
            destabilizing_systems.append(label)

    # ── Operational shift (primary narrative) ─────────────────────────────────
    # Detect SEO stable + ad/margin recurring = pressure shift
    seo_in_active          = any(_cat(i) == "seo_opportunity" for i in active)
    seo_stable             = "seo_opportunity" not in recurring_cats
    ad_margin_recurring    = any(c in recurring_cats for c in ("high_ad_spend", "margin_crisis"))

    operational_shift: str
    if seo_stable and ad_margin_recurring and seo_in_active:
        # A. Pressure shift: SEO contained, ad/margin now recurring
        operational_shift = "Операционное давление сместилось из SEO в рекламную экономику."
    elif has_systemic and has_localized:
        # B. Portfolio expansion: localized → systemic transition
        operational_shift = "Давление распространилось на несколько SKU — паттерн стал системным."
    elif has_systemic and n_recurring >= 2:
        # C. Already systemic with recurring pressure
        operational_shift = (
            "Давление сохраняет системный характер — несколько категорий под повторяющимся воздействием."
        )
    elif n_recurring == 0 and n_stabilized > 0:
        # D. Recovery: recurring gone, stabilized present
        operational_shift = "Повторяющиеся паттерны уступают место периоду операционной стабилизации."
    elif fatigue_score < 0.3 and stability_credit > 0.5 and n_recurring == 0:
        # E. Operator recovery
        operational_shift = (
            "Операционная нагрузка снизилась — система демонстрирует признаки устойчивости."
        )
    elif n_emerging >= 2:
        # F. Noise phase
        operational_shift = (
            "Часть сигналов остаётся в фазе подтверждения — данных для полной оценки пока недостаточно."
        )
    else:
        # G. Default
        operational_shift = "Операционное состояние портфеля остаётся под мониторингом."

    # ── Summary note (secondary observation) ─────────────────────────────────
    summary_note: str
    if n_recurring >= 2:
        summary_note = "Повторяющиеся паттерны продолжают возвращаться после временной стабилизации."
    elif n_stabilized > n_recurring and n_stabilized > 0:
        summary_note = "Операционные системы демонстрируют признаки восстановления."
    elif n_emerging >= 2:
        summary_note = "Часть новых сигналов остаётся в фазе подтверждения."
    elif n_recurring == 1:
        summary_note = "Один повторяющийся паттерн сохраняет операционное давление."
    else:
        summary_note = "ПУЛЬТ продолжает мониторинг операционного состояния."

    # ── Narrative lines (max 4, for direct UI display) ────────────────────────
    lines: list[str] = []

    # 1. Primary shift (always)
    lines.append(operational_shift)

    # 2. Stabilization note
    if improving_systems and len(lines) < 4:
        cats_str = " и ".join(improving_systems[:2])
        lines.append(f"Стабилизация в области {cats_str} сохраняется.")
    elif n_stabilized > 0 and len(lines) < 4:
        stab_ages = [getattr(i, "signal_operational_age", 0) or 0 for i in stabilized]
        age_hint  = f" {max(stab_ages)} дн." if stab_ages and max(stab_ages) > 0 else ""
        lines.append(f"Ранее нестабильные области восстановились{age_hint}.")

    # 3. Systemic expansion note
    if has_systemic and len(lines) < 4:
        mp_hint = f" {systemic_mp}" if systemic_mp else ""
        lines.append(f"Давление распространилось на несколько SKU категории{mp_hint}.")

    # 4. Emerging / noise note
    if n_emerging >= 1 and len(lines) < 4:
        lines.append("Часть новых сигналов остаётся в фазе подтверждения.")

    # Deduplicate keeping order, cap at 4
    seen: set[str] = set()
    narrative_lines: list[str] = []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            narrative_lines.append(ln)
    narrative_lines = narrative_lines[:4]

    return OperationalSummary(
        summary_type=summary_type,
        operational_shift=operational_shift,
        dominant_pressure=dominant_pressure,
        improving_systems=improving_systems[:3],
        destabilizing_systems=destabilizing_systems[:3],
        recurring_patterns=recurring_cats[:3],
        stabilized_patterns=stabilized_cats[:3],
        portfolio_direction=portfolio_direction,
        operator_load=operator_load,
        summary_note=summary_note,
        narrative_lines=narrative_lines,
    )
