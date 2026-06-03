"""
Operator Learning Layer — Sprint 18.

Detects behavioral patterns from OperatorDecision history and silently
adapts recommendations. Never labels the seller. Never hides critical truth.
Rule-based. No ML.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Profile ───────────────────────────────────────────────────────────────────

@dataclass
class OperatorProfile:
    ignore_counts:         dict[str, int]   = field(default_factory=dict)
    accept_counts:         dict[str, int]   = field(default_factory=dict)
    slow_resolve_counts:   dict[str, int]   = field(default_factory=dict)  # accepted but >14 days
    fast_resolve_counts:   dict[str, int]   = field(default_factory=dict)  # accepted and <=3 days
    total_decisions:       int              = 0

    def ignores(self, t: str) -> int: return self.ignore_counts.get(t, 0)
    def accepts(self, t: str) -> int: return self.accept_counts.get(t, 0)
    def slow(self, t: str) -> int:    return self.slow_resolve_counts.get(t, 0)
    def fast(self, t: str) -> int:    return self.fast_resolve_counts.get(t, 0)


_EMPTY = OperatorProfile()


def load_profile(decisions: list[Any]) -> OperatorProfile:
    """
    Build OperatorProfile from OperatorDecision ORM records.
    decisions: list of OperatorDecision objects (or dicts with same fields).
    """
    if not decisions:
        return _EMPTY

    p = OperatorProfile(total_decisions=len(decisions))
    for d in decisions:
        itype   = getattr(d, "insight_type", None) or d.get("insight_type", "")
        ignored = getattr(d, "ignored", False)    or d.get("ignored", False)
        accepted = getattr(d, "accepted", False)  or d.get("accepted", False)
        days     = getattr(d, "resolved_after_days", None) or d.get("resolved_after_days")

        if not itype:
            continue
        if ignored:
            p.ignore_counts[itype] = p.ignore_counts.get(itype, 0) + 1
        if accepted:
            p.accept_counts[itype] = p.accept_counts.get(itype, 0) + 1
            if days is not None:
                if days > 14:
                    p.slow_resolve_counts[itype] = p.slow_resolve_counts.get(itype, 0) + 1
                elif days <= 3:
                    p.fast_resolve_counts[itype] = p.fast_resolve_counts.get(itype, 0) + 1

    return p


# ── Adaptation alternatives ───────────────────────────────────────────────────

_SEO_ALTERNATIVES = [
    "Проверьте позиционирование цены — её отклонение от медианы категории.",
    "Проверьте визуальную подачу карточки: фото, инфографика, первое изображение.",
]

_AD_SPEND_ALTERNATIVES = [
    "Проведите анализ структуры цены — при высоком ДРР снижение себестоимости даёт больший эффект.",
    "Рассмотрите перераспределение бюджета на органические ключи вместо платного трафика.",
]

_MARGIN_ALTERNATIVES = [
    "Проверьте структуру затрат по SKU: логистика, упаковка, себестоимость.",
]

_GROWTH_ALTERNATIVES = [
    "Убедитесь, что складской запас поддержит масштабирование на 2–3 недели вперёд.",
]


def _push_to_end(recs: list[str], keyword: str) -> list[str]:
    """Move any rec containing keyword to the end of the list."""
    front = [r for r in recs if keyword.lower() not in r.lower()]
    back  = [r for r in recs if keyword.lower() in r.lower()]
    return front + back


def adapt_insight(
    insight_type: str,
    recs:         list[str],
    confidence:   int,
    profile:      OperatorProfile,
) -> tuple[list[str], str | None, int]:
    """
    Returns (adapted_recs, adaptation_note | None, adjusted_confidence).

    NEVER: hide risk, suppress urgency for irreversible threats, manipulate facts.
    ADAPTS: recommendation order, alternative surfaces, confidence when failures detected.
    """
    if profile is _EMPTY or profile.total_decisions < 2:
        return recs, None, confidence

    out_recs  = list(recs)
    note: str | None = None
    adj_conf  = confidence

    # ── seo_opportunity ─────────────────────────────────────────────────────
    if insight_type == "seo_opportunity":
        if profile.slow(insight_type) >= 2:
            # Rebuilds accepted but slow — likely not very effective
            adj_conf = max(adj_conf - 8, 40)
            # Surface alternative first, keep rebuild but deprioritize
            out_recs = _SEO_ALTERNATIVES + [r for r in out_recs if "пересборк" not in r.lower()]
            note = (
                "Предыдущие пересборки не дали устойчивого улучшения CTR. "
                "Рекомендуется проверить цену или визуальную подачу."
            )
        elif profile.ignores(insight_type) >= 4:
            # Consistently ignored — surface non-rebuild alternative first
            out_recs = _SEO_ALTERNATIVES[:1] + _push_to_end(out_recs, "пересборк")
            note = "Рекомендация скорректирована: авто-пересборка ранее не применялась."

    # ── high_ad_spend ────────────────────────────────────────────────────────
    elif insight_type == "high_ad_spend":
        if profile.ignores(insight_type) >= 4:
            out_recs = _AD_SPEND_ALTERNATIVES[:1] + out_recs[:2]
            note = "Сценарий учитывает ваш предыдущий режим участия."
        elif profile.slow(insight_type) >= 2:
            # Tried to fix, took long — widen uncertainty
            adj_conf = max(adj_conf - 5, 50)
            note = "Рекомендация скорректирована на основе предыдущих результатов."

    # ── margin_crisis ────────────────────────────────────────────────────────
    elif insight_type == "margin_crisis":
        if profile.ignores(insight_type) >= 3:
            out_recs = _MARGIN_ALTERNATIVES + out_recs[:2]
            note = "Рекомендация скорректирована на основе предыдущих результатов."
        elif profile.slow(insight_type) >= 2:
            adj_conf = max(adj_conf - 5, 50)
            note = "Рекомендация скорректирована на основе предыдущих результатов."

    # ── sales_growth ─────────────────────────────────────────────────────────
    elif insight_type == "sales_growth":
        if profile.fast(insight_type) >= 2:
            # Operator always acts fast on growth — surface stock check
            out_recs = _GROWTH_ALTERNATIVES + out_recs
            # No note — invisible adaptation
        elif profile.ignores(insight_type) >= 3:
            note = "Рекомендация скорректирована на основе предыдущих результатов."

    # ── low_stock: never suppress — irreversible threat ──────────────────────
    # Operator resolves quickly? Just keep as-is. Don't reduce urgency.
    # (low_stock is always time-sensitive, no adaptation applied)

    return out_recs[:4], note, adj_conf


def apply_adaptations(
    insights: list[Any],
    profile:  OperatorProfile,
) -> None:
    """
    Mutate each InsightItem in-place: adapt recommendations + set adaptation_note.
    Skips secondary insights and demo insights.
    """
    if profile is _EMPTY or profile.total_decisions < 2:
        return

    for ins in insights:
        if getattr(ins, "is_demo", False) or getattr(ins, "is_secondary", False):
            continue
        key  = getattr(ins, "key", "") or ""
        itype = key.split(":")[0]
        if itype.startswith("demo_"):
            continue

        recs_orig = list(getattr(ins, "recommendations", []) or [])
        conf_orig = getattr(ins, "confidence", 70) or 70

        new_recs, note, new_conf = adapt_insight(itype, recs_orig, conf_orig, profile)

        ins.recommendations  = new_recs
        ins.confidence       = new_conf
        ins.adaptation_note  = note
