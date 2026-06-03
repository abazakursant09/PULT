"""
Counterfactual Simulation Layer — operational pressure trajectories.

NOT forecasting. NOT AI prediction.
Bounded operational simulation: models pressure DIRECTION, not exact numbers.

Every OperationalScenario declares:
  assumption, expected_effect, tradeoff, evidence_basis, uncertainty_note.

Confidence discipline:
  - Every scenario carries an explicit uncertainty_note.
  - Low-evidence scenarios use reduced confidence language.
  - PULT never predicts exact revenue or guarantees outcomes.
"""
from __future__ import annotations

import uuid
from typing import Any


# ── Internal helpers ──────────────────────────────────────────────────────────

def _clevel(conf: int) -> str:
    if conf >= 75: return "high"
    if conf >= 55: return "medium"
    return "low"


def _sid() -> str:
    return str(uuid.uuid4())[:8]


def _uncertainty_context(
    days_active: int,
    past_cnt:    int,
    has_history: bool,
) -> tuple[str, int]:
    """
    Returns (uncertainty_note, confidence_penalty).
    Shorter history or no past pattern → higher penalty + stronger uncertainty language.
    Past recurrence → bonus (negative penalty).
    """
    if days_active < 7:
        return (
            f"Данных {days_active} дн. — оценка приблизительная. "
            "Рекомендуется перепроверить через 7 дней.",
            18,
        )
    if days_active < 14:
        return (
            "Оценка основана на ограниченной истории. "
            "Направление вероятно, но временной горизонт может сместиться.",
            10,
        )
    if past_cnt == 0 and not has_history:
        return (
            "Исторических данных по этому товару недостаточно — "
            "сценарий опирается на рыночные закономерности.",
            8,
        )
    if past_cnt >= 3:
        return (
            f"Паттерн наблюдается {past_cnt}× — исторический контекст "
            "повышает достоверность оценки.",
            -8,
        )
    if past_cnt >= 2:
        return (
            f"Паттерн повторяется {past_cnt}× — оценка подкреплена историей.",
            -5,
        )
    return (
        "Оценка основана на операционной логике и рыночных закономерностях.",
        0,
    )


def _mp_constraint_note(marketplace: str, scenario_type: str) -> str:
    """
    Return a marketplace-specific constraint relevant to the scenario.
    Respects real mechanics from marketplace_mechanics.py knowledge.
    """
    if marketplace == "wildberries":
        if scenario_type == "reduce_ads":
            return "WB: резкое снижение ставок может дать просадку позиций на 2–5 дней"
        if scenario_type in ("increase_discount", "decrease_discount"):
            return "WB: частая смена цены влияет на ранжирование — делать постепенно"
        if scenario_type == "controlled_scale":
            return "WB: cooldown 72ч между пересборками карточки"

    elif marketplace == "ozon":
        if scenario_type == "reduce_ads":
            return "Ozon: атрибуция 48ч — эффект снижения ставок виден с задержкой"
        if scenario_type in ("increase_discount", "decrease_discount"):
            return "Ozon: изменение цены может нарушить ценовой индекс и снизить видимость"
        if scenario_type == "aggressive_scale":
            return "Ozon: данные с лагом 48ч — масштабирование на непроверенных данных рискованно"

    elif marketplace == "yandex_market":
        if scenario_type == "aggressive_scale":
            return "YM: при stock < 70% доступности карточка выпадает из TopK на 2 недели"
        if scenario_type == "reduce_ads":
            return "YM: снижение ставок снижает риск нецелевых кликов конкурентов"
        if scenario_type == "increase_stock":
            return "YM: доступность > 70% критична для сохранения позиций в категории"

    return ""


# ── Scenario builders per rule category ──────────────────────────────────────

def _scenarios_high_ad_spend(
    insight_key:   str,
    marketplace:   str,
    base_conf:     int,
    days_active:   int,
    past_cnt:      int,
    has_history:   bool,
    ad_ratio_pct:  float,
    margin_pct:    float,
) -> list[dict]:
    unc, penalty = _uncertainty_context(days_active, past_cnt, has_history)
    mp_note = _mp_constraint_note(marketplace, "reduce_ads")

    scenarios = []

    # ── Balanced: controlled ad reduction ─────────────────────────────────────
    conf_b = max(52, base_conf - 5 - max(0, penalty))
    scenarios.append({
        "scenario_id":       _sid(),
        "source_insight":    insight_key,
        "scenario_type":     "reduce_ads",
        "path_type":         "balanced",
        "assumption":        f"Снижение ДРР на 15–20% за счёт отсева нецелевых ключей",
        "expected_effect":   "Вероятная стабилизация маржи за 7–14 дней",
        "tradeoff":          (
            f"Краткосрочное снижение трафика (2–5 дней адаптации). "
            + (mp_note + ". " if mp_note else "")
        ).rstrip(),
        "risk_level":        "medium",
        "confidence":        conf_b,
        "confidence_level":  _clevel(conf_b),
        "time_horizon_days": 14,
        "reversible":        True,
        "causal_chain": [
            f"ДРР {ad_ratio_pct:.0f}% → отсев нецелевых ключей",
            "снижение рекламной нагрузки → стабилизация маржи",
            "органический трафик восстанавливается через 7–14 дней",
        ],
        "evidence_basis": (
            f"Текущий ДРР {ad_ratio_pct:.0f}% устойчиво превышает норму "
            f"на протяжении {days_active} дн."
        ),
        "uncertainty_note": unc,
    })

    # ── Conservative: audit first, act later ──────────────────────────────────
    conf_c = max(48, base_conf - 12 - max(0, penalty))
    scenarios.append({
        "scenario_id":       _sid(),
        "source_insight":    insight_key,
        "scenario_type":     "controlled_scale",
        "path_type":         "conservative",
        "assumption":        "Аудит ключевых слов по ROAS — без немедленного изменения ставок",
        "expected_effect":   "Понимание структуры неэффективного трафика за 7 дней",
        "tradeoff":          "Давление на маржу продолжается во время анализа",
        "risk_level":        "low",
        "confidence":        conf_c,
        "confidence_level":  _clevel(conf_c),
        "time_horizon_days": 7,
        "reversible":        True,
        "causal_chain": [
            "анализ ROAS по ключевым словам",
            "выявление нецелевых кластеров трафика",
            "точечная корректировка ставок с минимальным риском",
        ],
        "evidence_basis":  f"Рекомендуется при неясной структуре кампании (активна {days_active} дн.)",
        "uncertainty_note": unc,
    })

    # ── Aggressive: status quo (risk illustration) ────────────────────────────
    conf_a = max(55, base_conf - max(0, penalty))
    scenarios.append({
        "scenario_id":       _sid(),
        "source_insight":    insight_key,
        "scenario_type":     "maintain_current",
        "path_type":         "aggressive",
        "assumption":        "Сохранение текущей рекламной нагрузки",
        "expected_effect":   "Давление на маржу вероятно продолжится",
        "tradeoff":          (
            f"При ДРР {ad_ratio_pct:.0f}% и марже {margin_pct:.1f}% "
            "операционный буфер минимален. Риск дальнейшего сжатия маржи."
        ),
        "risk_level":        "high",
        "confidence":        conf_a,
        "confidence_level":  _clevel(conf_a),
        "time_horizon_days": 14,
        "reversible":        True,
        "causal_chain": [
            f"ДРР {ad_ratio_pct:.0f}% сохраняется",
            "маржинальное давление продолжается",
            "при росте затрат без роста выручки — структурное ухудшение",
        ],
        "evidence_basis":  f"Паттерн наблюдается {days_active} дн. без стабилизации",
        "uncertainty_note": unc,
    })

    return scenarios


def _scenarios_margin_crisis(
    insight_key:      str,
    marketplace:      str,
    base_conf:        int,
    days_active:      int,
    past_cnt:         int,
    has_history:      bool,
    pressure_source:  str,
    margin_pct:       float,
) -> list[dict]:
    unc, penalty = _uncertainty_context(days_active, past_cnt, has_history)

    scenarios = []

    # ── Balanced scenario depends on pressure source ───────────────────────────
    if pressure_source == "ad_driven":
        conf_b = max(50, base_conf - 8 - max(0, penalty))
        mp_note = _mp_constraint_note(marketplace, "reduce_ads")
        scenarios.append({
            "scenario_id":       _sid(),
            "source_insight":    insight_key,
            "scenario_type":     "reduce_ads",
            "path_type":         "balanced",
            "assumption":        "Снижение рекламной нагрузки — первопричины маржинального давления",
            "expected_effect":   "Стабилизация маржи следует за стабилизацией ДРР за 7–14 дней",
            "tradeoff":          (
                "Возможное краткосрочное снижение трафика. "
                + (mp_note + "." if mp_note else "")
            ).rstrip(),
            "risk_level":        "medium",
            "confidence":        conf_b,
            "confidence_level":  _clevel(conf_b),
            "time_horizon_days": 14,
            "reversible":        True,
            "causal_chain": [
                "снижение ДРР → уменьшение рекламной нагрузки",
                "рекламные расходы — первопричина давления на маржу",
                "стабилизация маржи следует за стабилизацией ДРР",
            ],
            "evidence_basis":  "Маржинальное давление имеет рекламную природу",
            "uncertainty_note": unc,
        })

    elif pressure_source == "logistics":
        conf_b = max(48, base_conf - 8 - max(0, penalty))
        scenarios.append({
            "scenario_id":       _sid(),
            "source_insight":    insight_key,
            "scenario_type":     "controlled_scale",
            "path_type":         "balanced",
            "assumption":        "Оптимизация упаковки и схемы отгрузки",
            "expected_effect":   "Снижение логистической нагрузки на 10–25% при правильном подборе",
            "tradeoff":          "Требует 1–3 нед. на согласование с логистом",
            "risk_level":        "low",
            "confidence":        conf_b,
            "confidence_level":  _clevel(conf_b),
            "time_horizon_days": 21,
            "reversible":        True,
            "causal_chain": [
                "оптимизация упаковки → снижение стоимости доставки",
                "снижение логистической нагрузки → восстановление маржи",
            ],
            "evidence_basis":  "Логистика — основной драйвер давления на маржу",
            "uncertainty_note": unc,
        })

    elif pressure_source == "commission":
        conf_b = max(46, base_conf - 10 - max(0, penalty))
        mp_note = _mp_constraint_note(marketplace, "decrease_discount")
        scenarios.append({
            "scenario_id":       _sid(),
            "source_insight":    insight_key,
            "scenario_type":     "controlled_scale",
            "path_type":         "balanced",
            "assumption":        "Проверка тарифного плана и перекатегоризация товара",
            "expected_effect":   "Возможное снижение комиссии при переходе на другой тариф",
            "tradeoff":          (
                "Эффект зависит от доступных тарифных опций площадки. "
                + (mp_note + "." if mp_note else "")
            ).rstrip(),
            "risk_level":        "low",
            "confidence":        conf_b,
            "confidence_level":  _clevel(conf_b),
            "time_horizon_days": 14,
            "reversible":        True,
            "causal_chain": [
                "аудит категории и тарифа → выявление оптимального плана",
                "снижение комиссии → стабилизация маржи",
            ],
            "evidence_basis":  "Комиссия площадки — основной источник давления",
            "uncertainty_note": unc,
        })

    else:  # structural
        conf_b = max(46, base_conf - 12 - max(0, penalty))
        scenarios.append({
            "scenario_id":       _sid(),
            "source_insight":    insight_key,
            "scenario_type":     "decrease_discount",
            "path_type":         "balanced",
            "assumption":        "Постепенное повышение цены на 5–10% с мониторингом конверсии",
            "expected_effect":   "Улучшение маржинальности при сохранении объёма продаж",
            "tradeoff":          "Возможное снижение конверсии на 10–20% — необходим A/B мониторинг",
            "risk_level":        "medium",
            "confidence":        conf_b,
            "confidence_level":  _clevel(conf_b),
            "time_horizon_days": 21,
            "reversible":        True,
            "causal_chain": [
                "повышение цены → улучшение маржи",
                "мониторинг конверсии → корректировка при необходимости",
                "при сохранении объёма — структурное улучшение",
            ],
            "evidence_basis":  "Структурное давление без явного доминирующего источника",
            "uncertainty_note": unc,
        })

    # ── Status quo (aggressive / risk illustration) ───────────────────────────
    conf_a = max(55, base_conf - max(0, penalty))
    scenarios.append({
        "scenario_id":       _sid(),
        "source_insight":    insight_key,
        "scenario_type":     "maintain_current",
        "path_type":         "aggressive",
        "assumption":        "Сохранение текущей структуры затрат",
        "expected_effect":   "Давление на маржу вероятно продолжится",
        "tradeoff":          (
            f"При марже {margin_pct:.1f}% операционный буфер минимален. "
            "Риск дальнейшего ухудшения при нестабильной выручке."
        ),
        "risk_level":        "high",
        "confidence":        conf_a,
        "confidence_level":  _clevel(conf_a),
        "time_horizon_days": 14,
        "reversible":        True,
        "causal_chain": [
            "затраты сохраняются → маржа остаётся под давлением",
            "при дальнейшем снижении маржи — операционная устойчивость снижается",
        ],
        "evidence_basis":  f"Маржа {margin_pct:.1f}% ниже безопасного порога {days_active} дн.",
        "uncertainty_note": unc,
    })

    return scenarios


def _scenarios_seo_opportunity(
    insight_key:       str,
    marketplace:       str,
    base_conf:         int,
    days_active:       int,
    past_cnt:          int,
    rebuild_worked:    bool,
    rebuild_ctr_delta: float | None,
) -> list[dict]:
    has_history = rebuild_worked or rebuild_ctr_delta is not None
    unc, penalty = _uncertainty_context(days_active, past_cnt, has_history)

    memory_boost = 0
    memory_note  = ""
    if rebuild_worked and rebuild_ctr_delta and rebuild_ctr_delta > 0:
        memory_boost = 10
        memory_note  = f"SEO-пересборка ранее дала +{rebuild_ctr_delta:.1f}% CTR для этого товара"
    elif rebuild_ctr_delta is not None and rebuild_ctr_delta <= 0:
        memory_boost = -12
        memory_note  = "Предыдущая пересборка не улучшила CTR — рассмотрите другой стиль карточки"

    mp_note = _mp_constraint_note(marketplace, "controlled_scale")

    scenarios = []

    # ── Balanced: controlled rebuild ──────────────────────────────────────────
    conf_b = max(50, base_conf + memory_boost - max(0, penalty))
    assumption = (
        f"Авто-пересборка карточки с лучшим стилем категории"
        + (f" ({memory_note})" if memory_note and memory_boost > 0 else "")
    )
    tradeoff_b = (
        (mp_note + ". " if mp_note else "")
        + "Эффект виден через 5–10 дней после индексации"
    )
    scenarios.append({
        "scenario_id":       _sid(),
        "source_insight":    insight_key,
        "scenario_type":     "controlled_scale",
        "path_type":         "balanced",
        "assumption":        assumption,
        "expected_effect":   "Вероятное улучшение CTR и снижение стоимости рекламного клика",
        "tradeoff":          tradeoff_b,
        "risk_level":        "low",
        "confidence":        conf_b,
        "confidence_level":  _clevel(conf_b),
        "time_horizon_days": 10,
        "reversible":        True,
        "causal_chain": [
            "пересборка карточки → улучшение визуального CTR",
            "рост органического трафика → снижение зависимости от рекламы",
            "нормализация CPC → снижение рекламной нагрузки",
        ],
        "evidence_basis":   (
            memory_note if memory_note and memory_boost > 0
            else "Рейтинг товара хороший — проблема на уровне карточки, не продукта"
        ),
        "uncertainty_note": unc,
    })

    # ── Aggressive: rebuild + scale ads ───────────────────────────────────────
    conf_a = max(46, base_conf - 8 + memory_boost - max(0, penalty))
    mp_note_agg = _mp_constraint_note(marketplace, "aggressive_scale")
    scenarios.append({
        "scenario_id":       _sid(),
        "source_insight":    insight_key,
        "scenario_type":     "aggressive_scale",
        "path_type":         "aggressive",
        "assumption":        "Пересборка карточки + одновременное масштабирование рекламы",
        "expected_effect":   "Ускоренный рост трафика при улучшенном CTR",
        "tradeoff":          (
            "Повышенные рекламные расходы при неподтверждённом CTR новой карточки. "
            + (mp_note_agg + "." if mp_note_agg else "")
        ).rstrip(),
        "risk_level":        "high",
        "confidence":        conf_a,
        "confidence_level":  _clevel(conf_a),
        "time_horizon_days": 10,
        "reversible":        True,
        "causal_chain": [
            "новая карточка → потенциально высокий CTR",
            "увеличение бюджета → рост трафика",
            "если CTR не улучшится — бюджет потрачен неэффективно",
        ],
        "evidence_basis":  "Агрессивный путь оправдан только при высокой уверенности в визуальном улучшении",
        "uncertainty_note": (
            "Рекомендуется сначала пересборка, затем масштабирование после подтверждения CTR."
            if memory_boost <= 0 else unc
        ),
    })

    # ── Conservative: maintain current (opportunity cost) ────────────────────
    conf_c = max(50, base_conf - 5 - max(0, penalty))
    scenarios.append({
        "scenario_id":       _sid(),
        "source_insight":    insight_key,
        "scenario_type":     "maintain_current",
        "path_type":         "conservative",
        "assumption":        "Сохранение текущей карточки без изменений",
        "expected_effect":   "CTR остаётся ниже потенциала — рекламная нагрузка не снижается",
        "tradeoff":          "Упущенный органический трафик при хорошем рейтинге товара",
        "risk_level":        "medium",
        "confidence":        conf_c,
        "confidence_level":  _clevel(conf_c),
        "time_horizon_days": 14,
        "reversible":        True,
        "causal_chain": [
            "карточка не меняется → CTR остаётся ниже нормы",
            "конкуренты с лучшим визуалом продолжают опережать",
        ],
        "evidence_basis":  "Бездействие — тоже выбор; PULT показывает его операционное следствие",
        "uncertainty_note": unc,
    })

    return scenarios


def _scenarios_low_stock(
    insight_key: str,
    marketplace:  str,
    base_conf:    int,
    days_active:  int,
    past_cnt:     int,
    stock:        int,
    days_left:    int,
) -> list[dict]:
    unc, penalty = _uncertainty_context(days_active, past_cnt, past_cnt > 0)
    mp_note = _mp_constraint_note(marketplace, "increase_stock")
    recurrence = (
        f"Ситуация повторяется {past_cnt}× — рекомендуется систематический порог пополнения."
        if past_cnt >= 2 else ""
    )

    # For low stock, penalty matters less — urgency is real
    conf_b = max(65, base_conf - max(0, penalty) // 2)

    scenarios = [
        {
            "scenario_id":       _sid(),
            "source_insight":    insight_key,
            "scenario_type":     "increase_stock",
            "path_type":         "balanced",
            "assumption":        "Экстренное пополнение до уровня ≥ 30 дней продаж",
            "expected_effect":   "Сохранение позиций в поиске, предотвращение штрафа за out-of-stock",
            "tradeoff":          (
                (mp_note + ". " if mp_note else "")
                + "Заморозка оборотных средств в запасе."
            ).strip(),
            "risk_level":        "low",
            "confidence":        conf_b,
            "confidence_level":  _clevel(conf_b),
            "time_horizon_days": 3,
            "reversible":        False,
            "causal_chain": [
                f"остаток {stock} шт. → риск out-of-stock через {days_left} дн.",
                "пополнение → сохранение позиций в поиске",
                "предотвращение дорогостоящего восстановления органики",
            ],
            "evidence_basis":  (
                f"Запас {stock} шт. ≈ {days_left} дн. продаж. " + recurrence
            ).strip(),
            "uncertainty_note": "Точный срок пополнения зависит от поставщика и логистики.",
        },
        {
            "scenario_id":       _sid(),
            "source_insight":    insight_key,
            "scenario_type":     "maintain_current",
            "path_type":         "aggressive",
            "assumption":        "Не пополнять — продать оставшийся запас",
            "expected_effect":   f"Out-of-stock вероятен через {days_left} дн.",
            "tradeoff":          (
                "При нулевом остатке позиции в поиске теряются мгновенно. "
                "Восстановление — от 3 до 10 дней."
            ),
            "risk_level":        "high",
            "confidence":        max(70, base_conf),
            "confidence_level":  "high",
            "time_horizon_days": days_left,
            "reversible":        False,
            "causal_chain": [
                f"остаток {stock} шт. → out-of-stock через {days_left} дн.",
                "выпадение из поиска → потеря органического трафика",
                "дорогостоящее восстановление рекламой после возврата",
            ],
            "evidence_basis":  f"Текущая скорость продаж исчерпает запас через {days_left} дн.",
            "uncertainty_note": "PULT не рекомендует этот сценарий. Показан как ориентир риска.",
        },
    ]
    return scenarios


def _scenarios_sales_growth(
    insight_key:  str,
    marketplace:  str,
    base_conf:    int,
    days_active:  int,
    past_cnt:     int,
    growth_pct:   int,
    has_low_stock: bool,
) -> list[dict]:
    unc, penalty = _uncertainty_context(days_active, past_cnt, False)
    stock_risk = " При текущем темпе роста — риск out-of-stock." if has_low_stock else ""

    conf_b = max(55, base_conf - 5 - max(0, penalty))
    conf_a = max(48, base_conf - 12 - max(0, penalty))
    conf_c = max(55, base_conf - max(0, penalty))

    mp_note_agg = _mp_constraint_note(marketplace, "aggressive_scale")

    scenarios = [
        {
            "scenario_id":       _sid(),
            "source_insight":    insight_key,
            "scenario_type":     "controlled_scale",
            "path_type":         "balanced",
            "assumption":        "Масштабирование рекламного бюджета на 20–30% с мониторингом маржи",
            "expected_effect":   f"Усиление растущего тренда (+{growth_pct}%) при контроле рентабельности",
            "tradeoff":          ("Требует мониторинга запаса и ДРР." + stock_risk).strip(),
            "risk_level":        "low" if not has_low_stock else "medium",
            "confidence":        conf_b,
            "confidence_level":  _clevel(conf_b),
            "time_horizon_days": 14,
            "reversible":        True,
            "causal_chain": [
                f"рост +{growth_pct}% подтверждён в нескольких периодах",
                "масштабирование рекламы → усиление тренда",
                "мониторинг маржи и склада → устойчивый рост",
            ],
            "evidence_basis":  f"Рост +{growth_pct}% подтверждён — не разовый всплеск",
            "uncertainty_note": unc,
        },
        {
            "scenario_id":       _sid(),
            "source_insight":    insight_key,
            "scenario_type":     "aggressive_scale",
            "path_type":         "aggressive",
            "assumption":        "Максимальное масштабирование рекламы и ассортимента",
            "expected_effect":   "Ускорение роста выручки при повышенных рисках",
            "tradeoff":          (
                "Высокая зависимость от устойчивости тренда."
                + stock_risk
                + (" " + mp_note_agg + "." if mp_note_agg else "")
            ).strip(),
            "risk_level":        "high",
            "confidence":        conf_a,
            "confidence_level":  _clevel(conf_a),
            "time_horizon_days": 14,
            "reversible":        True,
            "causal_chain": [
                "агрессивное масштабирование → рост трафика",
                "при исчерпании запаса → прерывание роста и потеря позиций",
            ],
            "evidence_basis":  "Агрессивный сценарий оправдан при достаточном запасе",
            "uncertainty_note": (
                "Исторических данных об агрессивном масштабировании нет — высокая неопределённость."
                if past_cnt == 0 else unc
            ),
        },
        {
            "scenario_id":       _sid(),
            "source_insight":    insight_key,
            "scenario_type":     "maintain_current",
            "path_type":         "conservative",
            "assumption":        "Наблюдать за трендом ещё 7 дней без масштабирования",
            "expected_effect":   "Дополнительное подтверждение тренда перед инвестициями",
            "tradeoff":          "Упущенный момент роста — конкуренты могут опередить",
            "risk_level":        "low",
            "confidence":        conf_c,
            "confidence_level":  _clevel(conf_c),
            "time_horizon_days": 7,
            "reversible":        True,
            "causal_chain": [
                "ожидание → больше данных",
                "если тренд устойчив — масштабирование через 7 дней с меньшим риском",
            ],
            "evidence_basis":  "Подходит при неопределённости в природе роста",
            "uncertainty_note": unc,
        },
    ]
    return scenarios


# ── Public API ────────────────────────────────────────────────────────────────

def generate_scenarios_for_insight(
    insight_key:        str,
    rule_category:      str,
    marketplace:        str,
    insight_confidence: int,
    meta:               dict[str, Any],
    rebuild_outcomes:   dict[str, Any] | None = None,
    notif_counts:       dict[str, int] | None = None,
) -> list[dict]:
    """
    Generate 2-3 OperationalScenarios for a single insight.

    Returned dicts match the OperationalScenario Pydantic model in action_engine.py.

    meta keys (all optional with safe defaults):
      ad_ratio_pct:     float  — ad spend ratio as percent (e.g. 34.0)
      margin_pct:       float  — margin as percent (e.g. 3.1)
      pressure_source:  str    — "ad_driven" | "logistics" | "commission" | "structural"
      days_active:      int    — days of available data
      growth_pct:       int    — growth percent (for sales_growth)
      stock:            int    — current units (for low_stock)
      days_left:        int    — estimated days until depletion (for low_stock)
      has_low_stock:    bool   — same product also has low_stock insight
      product_name:     str
    """
    nc  = notif_counts    or {}
    rb  = rebuild_outcomes or {}

    days_active  = int(meta.get("days_active", 14))
    past_cnt     = int(nc.get(insight_key, 0))
    product_name = str(meta.get("product_name", ""))

    rebuild_obj       = rb.get(product_name)
    rebuild_worked    = bool(rebuild_obj and getattr(rebuild_obj, "winner", False))
    rebuild_ctr_delta = getattr(rebuild_obj, "delta_ctr_percent", None) if rebuild_obj else None
    has_history       = rebuild_obj is not None

    if rule_category == "high_ad_spend":
        return _scenarios_high_ad_spend(
            insight_key=insight_key,
            marketplace=marketplace,
            base_conf=insight_confidence,
            days_active=days_active,
            past_cnt=past_cnt,
            has_history=has_history,
            ad_ratio_pct=float(meta.get("ad_ratio_pct", 25.0)),
            margin_pct=float(meta.get("margin_pct", 5.0)),
        )
    if rule_category == "margin_crisis":
        return _scenarios_margin_crisis(
            insight_key=insight_key,
            marketplace=marketplace,
            base_conf=insight_confidence,
            days_active=days_active,
            past_cnt=past_cnt,
            has_history=has_history,
            pressure_source=str(meta.get("pressure_source", "structural")),
            margin_pct=float(meta.get("margin_pct", 3.0)),
        )
    if rule_category == "seo_opportunity":
        return _scenarios_seo_opportunity(
            insight_key=insight_key,
            marketplace=marketplace,
            base_conf=insight_confidence,
            days_active=days_active,
            past_cnt=past_cnt,
            rebuild_worked=rebuild_worked,
            rebuild_ctr_delta=rebuild_ctr_delta,
        )
    if rule_category == "low_stock":
        return _scenarios_low_stock(
            insight_key=insight_key,
            marketplace=marketplace,
            base_conf=insight_confidence,
            days_active=days_active,
            past_cnt=past_cnt,
            stock=int(meta.get("stock", 3)),
            days_left=int(meta.get("days_left", 2)),
        )
    if rule_category == "sales_growth":
        return _scenarios_sales_growth(
            insight_key=insight_key,
            marketplace=marketplace,
            base_conf=insight_confidence,
            days_active=days_active,
            past_cnt=past_cnt,
            growth_pct=int(meta.get("growth_pct", 20)),
            has_low_stock=bool(meta.get("has_low_stock", False)),
        )
    return []


def scenario_context_for_telegram(
    rule_category: str,
    marketplace:   str,
    past_cnt:      int,
    rebuild_obj:   Any = None,
) -> str:
    """
    Returns a brief scenario-aware context note for Telegram messages.
    One line max. Empty string if nothing meaningful to add.
    """
    if rule_category == "high_ad_spend":
        if past_cnt >= 2:
            return "\n\n🎯 Снижение ДРР ранее стабилизировало ситуацию за 7–14 дней."
        mp_note = _mp_constraint_note(marketplace, "reduce_ads")
        if mp_note:
            return f"\n\n⚙️ {mp_note}"

    elif rule_category == "margin_crisis":
        if past_cnt >= 2:
            return "\n\n🎯 Структурный разбор затрат ранее помогал восстановить маржу."

    elif rule_category == "sales_growth":
        if marketplace == "ozon":
            return "\n\n⏱ Данные Ozon подтверждены с учётом 48ч задержки атрибуции."
        if past_cnt >= 2:
            return "\n\n🎯 Исторически устойчивый рост — условия повторяются."

    elif rule_category == "seo_opportunity":
        rb = rebuild_obj
        if rb and getattr(rb, "winner", False) and getattr(rb, "delta_ctr_percent", None):
            delta = rb.delta_ctr_percent
            if delta > 0:
                return f"\n\n🎯 Пересборка ранее дала +{delta:.1f}% CTR для этого товара."

    elif rule_category == "low_stock":
        mp_note = _mp_constraint_note(marketplace, "increase_stock")
        if mp_note:
            return f"\n\n⚙️ {mp_note}"
        if past_cnt >= 2:
            return "\n\n🔁 Ситуация повторяется — рассмотрите систематический порог пополнения."

    return ""
