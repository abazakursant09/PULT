"""
Creative Scorer — heuristic-based, zero GPU, zero external APIs.

Scoring model (100 pts total):
  product_coverage  0-30  — photo presence + preset product sizing
  text_density      0-20  — advantage count × quality (length penalties)
  visual_contrast   0-25  — preset-specific contrast vs marketplace background
  mobile_safety     0-15  — layout zone safety for WB/Ozon
  category_fit      0-10  — preset-category alignment matrix

WB and Ozon receive DIFFERENT contrast and mobile_safety scores because
their catalog backgrounds and cropping rules differ meaningfully.
"""
from __future__ import annotations
from dataclasses import dataclass, field

# ── Preset layout characteristics ─────────────────────────────────────────────
# wb_contrast / ozon_contrast: max 25 pts each side
# top_heavy: title text occupies top 5-15% of card → WB crop hazard
# bottom_cta: CTA block in bottom 15% → WB price-badge overlap
# product_size: sizing hint for product_coverage scoring

_LAYOUT: dict[str, dict] = {
    "premium":     {"top_heavy": True,  "bottom_cta": False, "wb_contrast": 17, "ozon_contrast": 21, "product_size": "large"},
    "sale":        {"top_heavy": True,  "bottom_cta": True,  "wb_contrast": 21, "ozon_contrast": 18, "product_size": "medium"},
    "minimal":     {"top_heavy": False, "bottom_cta": False, "wb_contrast": 13, "ozon_contrast": 17, "product_size": "large"},
    "tech":        {"top_heavy": False, "bottom_cta": False, "wb_contrast": 24, "ozon_contrast": 21, "product_size": "medium"},
    "beauty":      {"top_heavy": True,  "bottom_cta": False, "wb_contrast": 16, "ozon_contrast": 23, "product_size": "full"},
    "marketplace": {"top_heavy": False, "bottom_cta": True,  "wb_contrast": 22, "ozon_contrast": 18, "product_size": "medium"},
    "luxury":      {"top_heavy": False, "bottom_cta": False, "wb_contrast": 23, "ozon_contrast": 20, "product_size": "large"},
    "wb-style":    {"top_heavy": False, "bottom_cta": True,  "wb_contrast": 25, "ozon_contrast": 15, "product_size": "large"},
    "ozon-style":  {"top_heavy": False, "bottom_cta": False, "wb_contrast": 15, "ozon_contrast": 25, "product_size": "large"},
}

# Preset → category fit (0-10)
_FIT: dict[str, dict[str, int]] = {
    "auto":        {"tech": 10, "premium": 9, "minimal": 7, "sale": 6, "luxury": 5, "beauty": 3, "marketplace": 7, "wb-style": 7, "ozon-style": 7},
    "beauty":      {"beauty": 10, "luxury": 9, "premium": 7, "minimal": 6, "sale": 7, "tech": 3, "marketplace": 6, "wb-style": 6, "ozon-style": 7},
    "electronics": {"tech": 10, "premium": 8, "minimal": 7, "sale": 6, "luxury": 5, "beauty": 3, "marketplace": 8, "wb-style": 7, "ozon-style": 8},
    "home":        {"premium": 10, "minimal": 9, "luxury": 8, "beauty": 6, "sale": 7, "tech": 5, "marketplace": 7, "wb-style": 7, "ozon-style": 7},
    "clothes":     {"luxury": 10, "beauty": 9, "premium": 8, "minimal": 7, "sale": 8, "tech": 3, "marketplace": 6, "wb-style": 7, "ozon-style": 7},
    "sport":       {"tech": 10, "premium": 8, "minimal": 7, "sale": 9, "luxury": 5, "beauty": 4, "marketplace": 7, "wb-style": 7, "ozon-style": 7},
}

# Best preset per category (for auto_fix suggestions)
_BEST_PRESET: dict[str, str] = {
    "auto": "tech", "beauty": "beauty", "electronics": "tech",
    "home": "premium", "clothes": "luxury", "sport": "tech",
}

_PRESET_LABELS = {
    "premium": "Premium", "sale": "Sale", "minimal": "Minimal", "tech": "Tech",
    "beauty": "Beauty", "marketplace": "Market", "luxury": "Luxury",
    "wb-style": "WB", "ozon-style": "Ozon",
}
_CAT_LABELS = {
    "auto": "Авто", "beauty": "Красота", "home": "Дом",
    "electronics": "Электроника", "clothes": "Одежда", "sport": "Спорт",
}


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class AutoFix:
    action: str     # "set_preset" | "set_marketplace"
    value:  str
    label:  str     # button text: "Переключить на Tech"


@dataclass
class IssueDetail:
    issue_type:   str   # canonical key
    severity:     str   # "critical" | "warning" | "tip"
    description:  str   # specific, uses actual content
    fix_hint:     str   # what to do
    score_impact: int   # negative — points lost due to this issue
    auto_fix:     AutoFix | None = None


@dataclass
class CreativeScore:
    total:                int
    product_coverage:     int    # 0-30
    text_density:         int    # 0-20
    visual_contrast:      int    # 0-25
    mobile_safety:        int    # 0-15
    category_fit:         int    # 0-10
    grade:                str
    predicted_ctr_uplift: float
    improvement_potential: int   # score if all issues fixed
    best_preset_for_cat:  str   # recommended preset for this category
    strengths:            list[str] = field(default_factory=list)
    issues:               list[IssueDetail] = field(default_factory=list)


def _grade(total: int) -> str:
    if total >= 88: return "S"
    if total >= 75: return "A"
    if total >= 60: return "B"
    if total >= 40: return "C"
    return "D"


# ── Main scorer ────────────────────────────────────────────────────────────────

def score_creative(
    product_name: str,
    category: str,
    preset: str,
    marketplace: str,
    advantages: list[str],
    has_product_photo: bool = False,
) -> CreativeScore:
    issues: list[IssueDetail] = []
    strengths: list[str] = []
    layout = _LAYOUT.get(preset, _LAYOUT["premium"])

    # ── product_coverage (0-30) ──────────────────────────────────────────────
    if has_product_photo:
        size = layout["product_size"]
        coverage = {"large": 28, "full": 26, "medium": 22}.get(size, 22)
        strengths.append("Фото товара добавлено")
    else:
        coverage = 5
        issues.append(IssueDetail(
            issue_type="no_photo",
            severity="critical",
            description="Нет фото товара — самая частая причина низкого CTR",
            fix_hint="Добавьте фото в поле «Фото товара» слева",
            score_impact=coverage - 28,  # negative: points lost vs optimal 28
        ))

    # ── text_density (0-20) ──────────────────────────────────────────────────
    filled = [a for a in advantages if a and a.strip()]
    n = len(filled)

    if n == 0:
        density = 3
        issues.append(IssueDetail(
            issue_type="no_advantages",
            severity="critical",
            description="Нет текстовых блоков — карточка неинформативна",
            fix_hint="Добавьте 2-3 конкретных преимущества (до 30 символов)",
            score_impact=-17,
        ))
    elif n == 1:
        density = 11
        issues.append(IssueDetail(
            issue_type="single_advantage",
            severity="warning",
            description="Только 1 преимущество — добавьте ещё 1-2 для полноты",
            fix_hint="Покупатели сканируют 2-3 буллета за 1.5 секунды",
            score_impact=-9,
        ))
    else:
        density = 16 if n == 2 else 20
        strengths.append(f"{n} текстовых блока — хороший охват внимания")

    # Per-text character analysis
    for i, adv in enumerate(filled):
        if len(adv) > 32:
            density = max(density - 2, 0)
            short = adv[:28].rstrip() + "…"
            issues.append(IssueDetail(
                issue_type=f"text_long_{i}",
                severity="warning",
                description=f'Текст #{i+1}: {len(adv)} симв. — WB обрезает после 30 на мобильных',
                fix_hint=f'Пример: «{short}»',
                score_impact=-2,
            ))

    if n >= 2 and all(len(a) <= 28 for a in filled):
        strengths.append("Тексты оптимальной длины для мобильных")

    # ── visual_contrast (0-25) — differs by marketplace ──────────────────────
    if marketplace == "wb":
        contrast = layout["wb_contrast"]
    elif marketplace == "ozon":
        contrast = layout["ozon_contrast"]
    else:
        # "all" → average, slightly conservative
        contrast = round((layout["wb_contrast"] + layout["ozon_contrast"]) / 2)

    if contrast <= 15:
        best_for_mp = "tech" if marketplace in ("wb", "all") else "ozon-style"
        issues.append(IssueDetail(
            issue_type="low_contrast",
            severity="warning",
            description=f"Пресет «{_PRESET_LABELS.get(preset, preset)}» слабо выделяется на фоне {marketplace.upper() if marketplace != 'all' else 'каталога'}",
            fix_hint=f"Попробуйте пресет с высоким контрастом",
            score_impact=contrast - 24,
            auto_fix=AutoFix("set_preset", best_for_mp, f"Применить {_PRESET_LABELS.get(best_for_mp, best_for_mp)}"),
        ))
    elif contrast >= 22:
        strengths.append(f"Высокий контраст на {marketplace.upper() if marketplace != 'all' else 'маркетплейсах'}")

    # ── mobile_safety (0-15) — WB/Ozon platform rules ────────────────────────
    if marketplace == "ozon":
        mobile = 15
        if not layout["top_heavy"]:
            strengths.append("Ozon: полная видимость, безопасная зона")
    else:
        mobile = 15
        penalty_reasons = []

        if layout["top_heavy"]:
            mobile -= 4
            penalty_reasons.append("верхних 8%")
            issues.append(IssueDetail(
                issue_type="wb_top_crop",
                severity="warning" if marketplace == "all" else "critical",
                description=f"Заголовок в зоне обрезки WB (верхние 8% = 58px из 720px)",
                fix_hint="Пресеты без обрезки: Tech, Luxury, Minimal, WB, Ozon",
                score_impact=-4,
                auto_fix=AutoFix("set_preset", "tech", "Применить Tech (безопасный)"),
            ))

        if layout["bottom_cta"] and marketplace in ("wb", "all"):
            mobile -= 3
            penalty_reasons.append("нижних 15%")
            issues.append(IssueDetail(
                issue_type="wb_bottom_cover",
                severity="warning",
                description="CTA-блок в нижних 15% — перекрывается ценником WB в каталоге",
                fix_hint="Пресеты без нижнего CTA: Premium, Minimal, Tech, Luxury",
                score_impact=-3,
                auto_fix=AutoFix("set_preset", "luxury", "Применить Luxury (чистый низ)"),
            ))

        if mobile >= 14:
            strengths.append("Безопасное расположение элементов для WB")

    # ── category_fit (0-10) ──────────────────────────────────────────────────
    cat_map = _FIT.get(category, {})
    cat_fit = cat_map.get(preset, 6)
    best_preset = _BEST_PRESET.get(category, "premium")
    best_fit = cat_map.get(best_preset, 9)
    cat_label = _CAT_LABELS.get(category, category)
    preset_label = _PRESET_LABELS.get(preset, preset)

    if cat_fit >= 9:
        strengths.append(f"Пресет «{preset_label}» оптимален для «{cat_label}»")
    elif cat_fit <= 4:
        best_label = _PRESET_LABELS.get(best_preset, best_preset)
        issues.append(IssueDetail(
            issue_type="cat_mismatch",
            severity="warning",
            description=f"«{preset_label}» слабо подходит категории «{cat_label}» (fit {cat_fit}/10 vs {best_fit}/10)",
            fix_hint=f"Оптимальный пресет для «{cat_label}»: {best_label}",
            score_impact=-(cat_fit - best_fit),
            auto_fix=AutoFix("set_preset", best_preset, f"Применить {best_label}"),
        ))

    # Ozon clothes policy tip
    if category == "clothes" and marketplace in ("ozon", "all") and n > 0:
        issues.append(IssueDetail(
            issue_type="ozon_clothes_policy",
            severity="tip",
            description="Ozon рекомендует для одежды: минимум текста, акцент на фото модели",
            fix_hint="Пресет Minimal или Beauty лучше отображает одежду на Ozon",
            score_impact=0,
            auto_fix=AutoFix("set_marketplace", "wb", "Сделать под WB"),
        ))

    # ── Assemble total ────────────────────────────────────────────────────────
    total = min(100, coverage + density + contrast + mobile + cat_fit)
    grade = _grade(total)
    uplift = round((total - 60) * 0.3, 1)

    if grade in ("S", "A"):
        strengths.append("Карточка готова к публикации")
    elif grade == "D":
        issues.append(IssueDetail(
            issue_type="overall_critical",
            severity="critical",
            description="Карточка требует доработки — комплексные проблемы снижают CTR",
            fix_hint="Устраните проблемы выше, начните с критических",
            score_impact=0,
        ))

    # Improvement potential = score after fixing all non-zero-impact issues
    fixable_pts = sum(abs(i.score_impact) for i in issues if i.score_impact < 0)
    improvement_potential = min(100, total + fixable_pts)

    return CreativeScore(
        total=total,
        product_coverage=coverage,
        text_density=density,
        visual_contrast=contrast,
        mobile_safety=mobile,
        category_fit=cat_fit,
        grade=grade,
        predicted_ctr_uplift=uplift,
        improvement_potential=improvement_potential,
        best_preset_for_cat=best_preset,
        strengths=strengths[:4],
        issues=issues,
    )


def get_optimization_variants(
    product_name: str,
    category: str,
    marketplace: str,
    advantages: list[str],
    has_product_photo: bool = False,
) -> list[dict]:
    variants = [
        ("Bigger Product", "premium"),
        ("Minimal Text",   "minimal"),
        ("High Contrast",  "tech"),
    ]
    results = []
    for name, preset in variants:
        sc = score_creative(product_name, category, preset, marketplace, advantages, has_product_photo)
        results.append({"variant_name": name, "preset": preset, "score": sc})
    results.sort(key=lambda x: x["score"].total, reverse=True)
    return results


def score_for_both_marketplaces(
    product_name: str,
    category: str,
    preset: str,
    advantages: list[str],
    has_product_photo: bool = False,
) -> dict[str, "CreativeScore"]:
    """Score the same card for WB and Ozon separately."""
    return {
        "wb":   score_creative(product_name, category, preset, "wb",   advantages, has_product_photo),
        "ozon": score_creative(product_name, category, preset, "ozon", advantages, has_product_photo),
    }
