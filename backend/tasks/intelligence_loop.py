"""
Telegram Intelligence Loop — Stage 26 + Final Polish.
Runs every 30 min. Processes all users with telegram + notify_insights=True.
- Groups insights by product (digest if 2+)
- Sorts by priority: critical → SEO → growth
- Hard cap: max 3 alert messages/day per user
- Per-key cooldowns + 1 retention/72h
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings as app_settings
from database import AsyncSessionLocal
from models.user import User
from models.telegram_settings import TelegramSettings
from models.telegram_notification_log import TelegramNotificationLog
from models.insight import InsightRecord
from services.telegram import send_message_with_keyboard
from data.marketplace_mechanics import get_mechanic
from logic.simulation import scenario_context_for_telegram
from logic.focus_engine import compute_operational_focus, focus_briefing_for_telegram
from logic.portfolio_patterns import detect_portfolio_patterns, insight_to_summary
from logic.operational_summary import build_operational_summary as _build_op_summary
from logic.execution_sequencing import build_execution_sequence as _build_sequence

logger = logging.getLogger(__name__)

_FE = app_settings.frontend_url.rstrip("/")

_ALERT_TYPES  = {"margin_crisis", "high_ad_spend", "low_stock"}
_SEO_TYPES    = {"seo_opportunity", "high_rating"}
_GROWTH_TYPES = {"sales_growth"}

# Lower = higher priority
_TYPE_PRIORITY: dict[str, int] = {
    "margin_crisis": 0, "high_ad_spend": 0, "low_stock": 0,
    "seo_opportunity": 1,
    "high_rating": 2, "sales_growth": 2,
}

# Per-key cooldowns (hours)
_COOLDOWN: dict[str, int] = {
    "seo_opportunity": 24,
    "sales_growth":    24,
    "high_rating":     48,
    "margin_crisis":   12,
    "high_ad_spend":   12,
    "low_stock":        8,
    "retention":       72,
}

_MAX_ALERTS_PER_DAY = 3


# ── Anti-spam helpers ──────────────────────────────────────────────────────────

async def _was_sent_recently(db: AsyncSession, user_id: str, key: str, hours: int) -> bool:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    q = await db.execute(
        select(TelegramNotificationLog)
        .where(
            TelegramNotificationLog.user_id == user_id,
            TelegramNotificationLog.notification_key == key,
            TelegramNotificationLog.sent_at >= cutoff,
        )
        .limit(1)
    )
    return q.scalar_one_or_none() is not None


async def _log_sent(db: AsyncSession, user_id: str, key: str) -> None:
    db.add(TelegramNotificationLog(user_id=user_id, notification_key=key))
    await db.commit()


async def _get_or_set_first_detected(db: AsyncSession, user_id: str, insight_key: str) -> datetime:
    """
    Track when a signal was first observed (not first sent).
    Uses a dedicated log entry — never re-created once written.
    Returns the first-detection timestamp.
    """
    first_key = f"first_detected:{insight_key}"
    cutoff    = datetime.utcnow() - timedelta(days=90)
    q = await db.execute(
        select(TelegramNotificationLog)
        .where(
            TelegramNotificationLog.user_id == user_id,
            TelegramNotificationLog.notification_key == first_key,
            TelegramNotificationLog.sent_at >= cutoff,
        )
        .order_by(TelegramNotificationLog.sent_at.asc())
        .limit(1)
    )
    existing = q.scalar_one_or_none()
    if existing:
        return existing.sent_at
    record = TelegramNotificationLog(user_id=user_id, notification_key=first_key)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record.sent_at


async def _signal_is_mature(
    db: AsyncSession,
    user_id: str,
    insight_key: str,
    marketplace: str,
) -> bool:
    """
    Gate: return True only if the signal has been observed longer than
    the marketplace's attribution delay (delay_hours from KB).
    Prevents premature escalation on platforms with known data lag (e.g. Ozon 48h).
    """
    rule_category = insight_key.split(":")[0]
    mechanic      = get_mechanic(rule_category, marketplace or "")
    delay_hours   = mechanic.get("delay_hours", 0)
    if delay_hours <= 0:
        return True
    first_seen = await _get_or_set_first_detected(db, user_id, insight_key)
    age_hours  = (datetime.utcnow() - first_seen).total_seconds() / 3600
    return age_hours >= delay_hours


async def _alerts_sent_today(db: AsyncSession, user_id: str) -> int:
    """Count insight notifications sent in the last 24h (for daily cap)."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    q = await db.execute(
        select(func.count()).select_from(TelegramNotificationLog).where(
            TelegramNotificationLog.user_id == user_id,
            TelegramNotificationLog.notification_key.like("insight:%"),
            TelegramNotificationLog.sent_at >= cutoff,
        )
    )
    return q.scalar() or 0


# ── Message formatters ─────────────────────────────────────────────────────────

def _mp_label(mp: str) -> str:
    return {
        "wildberries":   "Wildberries",
        "ozon":          "Ozon",
        "yandex_market": "Яндекс Маркет",
    }.get(mp, mp.replace("_", " ").title()) if mp else ""


def _memory_line(insight: dict) -> str:
    """Returns a memory footnote line or empty string. Never dominates the message."""
    mc = insight.get("memory_context")
    return f"\n\n🧠 <i>{mc}</i>" if mc else ""


def _behavior_line(insight: dict) -> str:
    """Marketplace behavior memory line — only for confident, non-modeled signals."""
    note = insight.get("marketplace_behavior_note")
    conf = insight.get("confidence", 0)
    if not note or conf < 70:
        return ""
    mp_label = _mp_label(insight.get("marketplace", ""))
    prefix = f"{mp_label}: " if mp_label else ""
    return f"\n\n↳ <i>Механика площадки: {prefix}{note}</i>"


def _certainty_line(insight: dict) -> str:
    """Decision certainty footnote — only for high/low bands, never shows score."""
    band = insight.get("decision_confidence_band")
    if band == "high":
        return "\n\n↳ <i>Сигнал подтверждается устойчивой операционной историей.</i>"
    if band == "low":
        return "\n\n↳ <i>Сигнал требует дополнительного подтверждения.</i>"
    return ""


def _lifecycle_line(insight: dict) -> str:
    """Lifecycle note for recurring/emerging signals. Stabilized/confirmed/resolved: no line."""
    stage = insight.get("signal_lifecycle_stage")
    if stage == "recurring":
        return "\n\n↳ Паттерн повторяется после предыдущей стабилизации."
    if stage == "emerging":
        return "\n\n↳ Сигнал в ранней фазе и требует подтверждения."
    return ""


def _decay_note(insight: dict) -> str:
    """Suppress or annotate based on decay state. Only persistent gets no annotation."""
    decay = insight.get("signal_decay_state")
    if decay == "stale":
        return "\n\n↳ <i>Сигнал сохраняется в памяти, но активная динамика не подтверждена.</i>"
    return ""


def _feedback_line(insight: dict) -> str:
    """Outcome feedback evidence line — only if bias is clearly known (reinforce/deprioritize)."""
    note  = insight.get("outcome_feedback_note")
    delta = insight.get("recommendation_confidence_delta", 0) or 0
    if not note or delta == 0:
        return ""
    if delta == +10:
        return f"\n\n↳ <i>Аналогичное действие ранее приводило к устойчивой стабилизации.</i>"
    if delta == -12:
        return f"\n\n↳ <i>Предыдущие аналогичные действия не дали устойчивого эффекта.</i>"
    return ""


def _outcome_line(insight: dict) -> str:
    """Retrospective outcome memory line. Operational tone; no success/failure framing."""
    note  = insight.get("outcome_memory_note")
    conf  = insight.get("outcome_confidence") or insight.get("confidence", 0)
    state = insight.get("outcome_state", "")
    if not note or conf < 70:
        return ""
    if state == "repeated":
        return f"\n\n↳ <i>Паттерн ранее возвращался после временной стабилизации.</i>"
    return f"\n\n↳ <i>История: {note}</i>"


def _fmt_seo_opportunity(insight: dict) -> tuple[str, list[list[dict]]]:
    name    = insight.get("product_name") or insight.get("product_sku") or "Товар"
    mp      = _mp_label(insight.get("marketplace", ""))
    conf    = insight.get("confidence", 0)
    est     = insight.get("impact_estimate", "")
    reasons = insight.get("reasons", [])[:2]

    reasons_str = "\n".join(f"• {r}" for r in reasons)
    text = (
        f"🔍 <b>SEO-возможность — Бизнес-Пульт</b>\n\n"
        f"📦 <b>{name}</b> · {mp}\n\n"
        f"⚠️ <b>{insight.get('title', 'Карточка снижает CTR')}</b>\n"
        f"{reasons_str}\n\n"
        f"📊 Потенциал: <i>{est}</i>\n"
        f"Уверенность: {conf}%"
        f"{_memory_line(insight)}"
        f"{_feedback_line(insight)}"
    )
    params    = insight.get("action_params") or {}
    product_q = params.get("product", name)
    keyboard  = [[
        {"text": "✨ Авто-пересборка", "url": f"{_FE}/dashboard/seo-cards?product={product_q}&auto=1"},
        {"text": "📋 SEO-карточки",    "url": f"{_FE}/dashboard/seo-cards"},
    ]]
    return text, keyboard


def _fmt_sales_growth(insight: dict) -> tuple[str, list[list[dict]]]:
    name    = insight.get("product_name") or insight.get("product_sku") or "Товар"
    mp      = _mp_label(insight.get("marketplace", ""))
    est     = insight.get("impact_estimate", "")
    conf    = insight.get("confidence", 0)
    title   = insight.get("title", "Рост подтверждён")
    reasons = insight.get("reasons", [])[:2]

    reasons_str = "\n".join(f"• {r}" for r in reasons)
    text = (
        f"📈 <b>Устойчивый рост — Бизнес-Пульт</b>\n\n"
        f"<b>{title}</b>\n"
        f"📦 {name} · {mp}\n\n"
        f"{reasons_str}\n\n"
        f"💰 {est}\n"
        f"Уверенность: {conf}%"
        f"{_memory_line(insight)}"
        f"{_behavior_line(insight)}"
        f"{_outcome_line(insight)}"
        f"{_certainty_line(insight)}"
        f"{_lifecycle_line(insight)}"
        f"{_feedback_line(insight)}"
        f"{insight.get('scenario_context', '')}"
    )
    keyboard = [[
        {"text": "📊 Открыть финансы", "url": f"{_FE}/dashboard/finance"},
        {"text": "🏠 Дашборд",          "url": f"{_FE}/dashboard"},
    ]]
    return text, keyboard


def _fmt_high_rating(insight: dict) -> tuple[str, list[list[dict]]]:
    name = insight.get("product_name") or insight.get("product_sku") or "Товар"
    mp   = _mp_label(insight.get("marketplace", ""))
    est  = insight.get("impact_estimate", "")

    text = (
        f"⭐ <b>Отличный рейтинг — Бизнес-Пульт</b>\n\n"
        f"<b>{insight.get('title', 'Высокий рейтинг товара')}</b>\n"
        f"📦 {name} · {mp}\n\n"
        f"💡 {est}\n\n"
        f"Используйте момент — масштабируйте рекламу!"
    )
    params    = insight.get("action_params") or {}
    product_q = params.get("product", name)
    keyboard  = [[
        {"text": "✨ Пересобрать карточку", "url": f"{_FE}/dashboard/seo-cards?product={product_q}&auto=1"},
        {"text": "🏠 Дашборд",              "url": f"{_FE}/dashboard"},
    ]]
    return text, keyboard


def _fmt_critical_alert(insight: dict) -> tuple[str, list[list[dict]]]:
    name    = insight.get("product_name") or insight.get("product_sku") or "Товар"
    mp      = _mp_label(insight.get("marketplace", ""))
    est     = insight.get("impact_estimate", "")
    reasons = insight.get("reasons", [])[:3]
    recs    = insight.get("recommendations", [])[:1]

    reasons_str = "\n".join(f"• {r}" for r in reasons)
    rec_str     = f"\n\n💡 {recs[0]}" if recs else ""

    # Use insight title directly — it's already source-specific (ad_driven / logistics / structural)
    insight_title = insight.get("title", "")
    type_label = {
        "margin_crisis": f"🔴 {insight_title}" if insight_title else "🔴 Давление на маржу",
        "high_ad_spend": "🔴 Рекламная нагрузка вне устойчивого диапазона",
        "low_stock":     "🟡 Критически низкий остаток",
    }.get(insight.get("rule_type", ""), "⚠️ Проблема обнаружена")

    text = (
        f"🚨 <b>Критический алерт — Бизнес-Пульт</b>\n\n"
        f"{type_label}\n"
        f"📦 <b>{name}</b> · {mp}\n\n"
        f"{reasons_str}\n\n"
        f"💸 {est}"
        f"{rec_str}"
        f"{_memory_line(insight)}"
        f"{_behavior_line(insight)}"
        f"{_outcome_line(insight)}"
        f"{_certainty_line(insight)}"
        f"{_lifecycle_line(insight)}"
        f"{_feedback_line(insight)}"
        f"{insight.get('scenario_context', '')}"
    )
    action_url = {
        "margin_crisis": f"{_FE}/dashboard/finance",
        "high_ad_spend": f"{_FE}/dashboard/finance",
        "low_stock":     f"{_FE}/suppliers",
    }.get(insight.get("rule_type", ""), f"{_FE}/dashboard")

    keyboard = [[
        {"text": "⚡ Решить сейчас", "url": action_url},
        {"text": "🏠 Дашборд",       "url": f"{_FE}/dashboard"},
    ]]
    return text, keyboard


def _fmt_digest(product_name: str, mp_raw: str, insights: list[dict]) -> tuple[str, list[list[dict]]]:
    """One message for multiple insights about the same product."""
    mp   = _mp_label(mp_raw)
    name = product_name or "Товар"

    TYPE_ICON = {
        "margin_crisis":   "🔴 Давление на маржу",
        "high_ad_spend":   "🔴 Рекламная нагрузка",
        "low_stock":       "🟡 Низкий остаток",
        "seo_opportunity": "🔍 SEO-возможность",
        "high_rating":     "⭐ Высокий рейтинг",
        "sales_growth":    "📈 Рост продаж",
    }
    mp_part = f" · {mp}" if mp else ""
    lines = [f"⚡ <b>Несколько инсайтов — {name}</b>{mp_part}", ""]
    for ins in insights:
        rt    = ins.get("rule_type", "")
        label = TYPE_ICON.get(rt, rt)
        est   = ins.get("impact_estimate", "")
        lines.append(f"{label}" + (f": <i>{est}</i>" if est else ""))

    lines += ["", "👉 Откройте Разведку для деталей и решений."]
    text     = "\n".join(lines)
    keyboard = [[
        {"text": "🚀 Разведка", "url": f"{_FE}/dashboard/action-engine"},
        {"text": "🏠 Дашборд",  "url": f"{_FE}/dashboard"},
    ]]
    return text, keyboard


def _fmt_retention(user_name: str, insight_count: int) -> tuple[str, list[list[dict]]]:
    count_str = f"{insight_count} новых инсайтов" if insight_count else "новые инсайты"
    text = (
        f"👋 <b>Бизнес-Пульт ждёт вас</b>\n\n"
        f"Привет, <b>{user_name}</b>!\n\n"
        f"Пока вас не было, мы обнаружили {count_str} по вашим товарам. "
        f"Загляните — возможно, есть возможности для роста или требуют внимания проблемы.\n\n"
        f"⚡ <i>Пульт работает непрерывно, даже когда вы отдыхаете.</i>"
    )
    keyboard = [[
        {"text": "🚀 Открыть Пульт",  "url": f"{_FE}/dashboard"},
        {"text": "🔍 Action Engine", "url": f"{_FE}/dashboard/action-engine"},
    ]]
    return text, keyboard


def _insight_to_dict(insight, notif_counts: dict[str, int] | None = None) -> dict:
    rule_type = insight.key.split(":")[0]
    mp        = insight.marketplace or ""
    past_cnt  = (notif_counts or {}).get(insight.key, 0)
    return {
        "title":            insight.title,
        "product_name":     insight.product_name,
        "product_sku":      insight.product_sku,
        "marketplace":      mp,
        "confidence":       insight.confidence,
        "reasons":          insight.reasons,
        "recommendations":  insight.recommendations,
        "impact_estimate":  insight.impact.estimate if insight.impact else "",
        "action_params":    (insight.actions[0].params if insight.actions else None),
        "rule_type":        rule_type,
        "impact_score":     insight.impact_score or 0,
        "memory_context":          getattr(insight, "memory_context", None),
        "scenario_context":        scenario_context_for_telegram(rule_type, mp, past_cnt, None),
        "marketplace_behavior_note": getattr(insight, "marketplace_behavior_note", None),
        "outcome_memory_note":        getattr(insight, "outcome_memory_note", None),
        "outcome_state":              getattr(insight, "outcome_state", None),
        "outcome_confidence":         getattr(insight, "outcome_confidence", None),
        "decision_confidence_band":   getattr(insight, "decision_confidence_band", None),
        "signal_lifecycle_stage":     getattr(insight, "signal_lifecycle_stage", None),
        "outcome_feedback_note":          getattr(insight, "outcome_feedback_note", None),
        "recommendation_confidence_delta": getattr(insight, "recommendation_confidence_delta", None),
        "signal_decay_state":              getattr(insight, "signal_decay_state", None),
    }


# ── Sprint 28: root cause + memory line helpers ───────────────────────────────

def _sequencing_line(active: list, portfolio_patterns: list, fatigue_score: float = 0.0) -> Optional[str]:
    """
    Build sequencing narrative for Telegram digest.
    Only emitted when: systemic pressure + 2+ high-friction signals + recurring lifecycle.
    Max 1 narrative per digest. No explicit commands. No certainty language.
    """
    # Condition: systemic pattern exists
    has_systemic = any(
        getattr(p, "stabilization_complexity", "") == "systemic"
        for p in portfolio_patterns
    )
    if not has_systemic:
        return None

    # Condition: 2+ high-friction signals
    _HIGH_FRICTION = {"margin_crisis", "high_ad_spend"}
    high_friction = [i for i in active if i.key.split(":")[0] in _HIGH_FRICTION]
    if len(high_friction) < 2:
        return None

    # Condition: at least one recurring lifecycle
    has_recurring = any(
        getattr(i, "signal_lifecycle_stage", None) == "recurring"
        for i in active
    )
    if not has_recurring:
        return None

    # Build sequence to get first two stages
    seq = _build_sequence(active, portfolio_patterns, None, fatigue_score)
    stage1 = [s for s in seq if s.sequence_stage == 1 and s.unlocks_next_stage]
    stage2 = [s for s in seq if s.sequence_stage == 2]

    if stage1 and stage2:
        first = stage1[0].insight_title
        second = stage2[0].insight_title
        return (
            f"↳ Рекомендуется сначала стабилизировать «{first}», "
            f"а затем пересматривать «{second}»."
        )
    return None


def _trajectory_line(active: list) -> Optional[str]:
    """
    Build trajectory narrative for Telegram digest.
    Only emitted for escalating or structurally_accumulating insights.
    Restrained language. No exact forecasts. No AI certainty claims.
    """
    escalating = [
        i for i in active
        if getattr(i, "trajectory_state", None) in ("escalating", "structurally_accumulating")
    ]
    if not escalating:
        return None
    top = escalating[0]
    state = getattr(top, "trajectory_state", "")
    if state == "structurally_accumulating":
        return "↳ Давление начинает переходить в структурный операционный паттерн."
    return "↳ Окно мягкой стабилизации постепенно сокращается."


def _root_cause_line(pattern) -> Optional[str]:
    """
    Returns restrained root cause note for Telegram digest.
    Only emitted for systemic patterns with root_cause_confidence >= 70.
    No AI language. Operational tone only.
    """
    note = getattr(pattern, "root_cause_note", None)
    conf = getattr(pattern, "root_cause_confidence", None)
    if note and conf is not None and conf >= 70:
        return f"↳ {note}"
    return None


def _cross_mp_memory_line(pattern) -> Optional[str]:
    """
    Returns historical memory note for Telegram digest.
    Only emitted when cross_mp_memory_note is present (confidence already filtered upstream).
    """
    note = getattr(pattern, "cross_mp_memory_note", None)
    if note:
        return f"↳ {note}"
    return None


def _forecast_line(active: list) -> Optional[str]:
    """
    Build forecast narrative for Telegram digest.
    Only emitted when fragile/critical + confidence >= 70 + recurring/persistent lifecycle.
    No deadline language. No catastrophe framing.
    """
    candidates = [
        i for i in active
        if getattr(i, "forecast_fragility_state", None) in ("fragile", "critical")
        and (getattr(i, "forecast_escalation_probability", 0) or 0) >= 70
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed")
    ]
    if not candidates:
        return None
    top = candidates[0]
    fragility = getattr(top, "forecast_fragility_state", "")
    if fragility == "critical":
        return "↳ Операционная гибкость существенно сократилась. Вмешательство на текущем этапе значительно эффективнее."
    return "↳ Давление постепенно приближается к фазе структурной нестабильности."


def _capacity_line(active: list, portfolio_patterns: list, fatigue_score: float = 0.0) -> Optional[str]:
    """
    Build capacity narrative for Telegram digest.
    Only emitted for overloaded, or saturated + recurring pressure.
    No scores, no percentages, no psychological language.
    """
    from logic.operator_capacity import compute_operator_capacity as _cc
    cap = _cc(active, portfolio_patterns, fatigue_score=fatigue_score, stability_credit=0.0)
    if cap.capacity_state == "overloaded":
        return "↳ Одновременное количество активных стабилизаций постепенно увеличивает операционную нагрузку."
    if cap.capacity_state == "saturated":
        recurring = [i for i in active if getattr(i, "signal_lifecycle_stage", None) == "recurring"]
        if recurring:
            return "↳ Одновременное количество активных стабилизаций постепенно увеличивает операционную нагрузку."
    return None


def _reversal_line(active: list) -> Optional[str]:
    """
    Build intervention reversal narrative for Telegram digest — Sprint 49.
    Only for overextended/structurally_locked with probability >= 70 + recurring/confirmed.
    No blame. No "rollback now". Only operational diminishing return signal.
    """
    candidates = [
        i for i in active
        if getattr(i, "reversal_state", None) in ("overextended", "structurally_locked")
        and (getattr(i, "reversal_probability", 0) or 0) >= 70
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed")
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    if not candidates:
        return None
    top = candidates[0]
    if getattr(top, "reversal_state", None) == "structurally_locked":
        return "↳ Система постепенно накапливает зависимость от текущей модели стабилизации."
    return "↳ Часть стабилизационных действий начинает демонстрировать признаки снижающейся отдачи."


def _resilience_line(active: list) -> Optional[str]:
    """
    Build resilience snapshot narrative for Telegram digest — Sprint 51.
    Only for brittle/collapsing/exhausted with score <= 30 + recurring/confirmed.
    Measured operational restraint. No panic.
    """
    candidates = [
        i for i in active
        if getattr(i, "resilience_state", None) in ("brittle", "collapsing", "exhausted")
        and (getattr(i, "resilience_score", 100) or 100) <= 30
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    if not candidates:
        return None
    top = min(candidates, key=lambda i: (getattr(i, "resilience_score", 100) or 100))
    if getattr(top, "resilience_state", None) in ("collapsing", "exhausted"):
        return "↳ Операционный абсорбционный ресурс в части зон приближается к критическому уровню."
    return "↳ Часть операционных зон демонстрирует признаки снижения способности компенсировать давление."


def _resilience_trajectory_line(active: list) -> Optional[str]:
    """
    Build resilience trajectory narrative for Telegram digest — Sprint 52.
    Only for degrading recurring OR structurally_degrading with confidence >= 70.
    Institutional, calm, historically aware.
    """
    candidates = [
        i for i in active
        if (
            (
                getattr(i, "resilience_trajectory", None) == "degrading"
                and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
            )
            or getattr(i, "resilience_trajectory", None) == "structurally_degrading"
        )
        and (getattr(i, "resilience_trajectory_confidence", 0) or 0) >= 70
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    if not candidates:
        return None
    top = max(candidates, key=lambda i: (getattr(i, "resilience_trajectory_confidence", 0) or 0))
    if getattr(top, "resilience_trajectory", None) == "structurally_degrading":
        return "↳ Несколько операционных слоёв постепенно теряют способность к самостоятельной стабилизации."
    return "↳ Способность системы компенсировать давление постепенно снижается на нескольких операционных уровнях."


def _recovery_capacity_line(active: list) -> Optional[str]:
    """
    Structural recovery capacity line for Telegram digest — Sprint 61.
    Emitted only for restructuring_dependent, continuity_without_recovery, structurally_exhausted
    with confidence >= 80. Institutional, restrained, non-catastrophic.
    NEVER: deterministic collapse, blame operator, panic framing.
    """
    from logic.structural_recovery_capacity import compute_structural_recovery_capacity as _crc
    if not active:
        return None
    src = _crc(active)
    if src.recovery_capacity_confidence < 80:
        return None
    if src.recovery_state == "structurally_exhausted":
        return "↳ Операционная система сохраняет continuity, но признаки структурной восстановимости increasingly не наблюдаются."
    if src.recovery_state == "continuity_without_recovery":
        return "↳ Система поддерживает операционную continuity без признаков устойчивой структурной recoverability."
    if src.recovery_state == "restructuring_dependent":
        return "↳ Структурное восстановление всё больше зависит от системной реструктуризации, а не от локальной адаптации."
    return None


def _inertia_line(active: list) -> Optional[str]:
    """
    Institutional inertia line for Telegram digest — Sprint 60.
    Emitted only for structural_inertia, locked_operational_behavior, institutional_freeze
    with confidence >= 78. Institutional, restrained, non-catastrophic.
    NEVER: blame operator, deterministic collapse, panic framing.
    """
    from logic.institutional_inertia import compute_institutional_inertia as _ci
    if not active:
        return None
    inertia = _ci(active)
    if inertia.inertia_confidence < 78:
        return None
    if inertia.inertia_state == "institutional_freeze":
        return "↳ Операционная система increasingly сохраняет continuity за счёт структурной repetition, постепенно ограничивая adaptive flexibility."
    if inertia.inertia_state == "locked_operational_behavior":
        return "↳ Execution patterns воспроизводятся независимо от результата intervention cycles, снижая operational responsiveness."
    if inertia.inertia_state == "structural_inertia":
        return "↳ Система всё больше поддерживает continuity через recurring structural compensation вместо adaptive restructuring."
    return None


def _doctrine_line(active: list) -> Optional[str]:
    """
    Operational doctrine line for Telegram digest — Sprint 59.
    Emitted only for structurally_embedded_doctrine, rigid_operational_doctrine,
    stabilization_dependency (recurring) with confidence >= 76.
    Institutional. No blame. No panic. No emotional framing.
    """
    from logic.operational_doctrine import compute_operational_doctrine as _cd
    if not active:
        return None
    doc = _cd(active)
    if doc.doctrine_confidence < 76:
        return None
    if doc.doctrine_state == "rigid_operational_doctrine":
        return "↳ Часть operational responses постепенно закрепляется независимо от исходных signal sources, снижая системную adaptive flexibility."
    if doc.doctrine_state == "structurally_embedded_doctrine":
        return "↳ Повторяющиеся execution responses начинают приобретать признаки устойчивого structural operational doctrine."
    if doc.doctrine_state == "stabilization_dependency":
        recurring_count = sum(
            1 for i in active
            if getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        )
        if recurring_count >= 2:
            return "↳ Операционная система всё больше зависит от повторяющихся stabilization cycles для поддержания execution continuity."
    return None


def _topology_line(active: list) -> Optional[str]:
    """
    Stability topology line for Telegram digest — Sprint 58.
    Emitted only for structurally_unbalanced, collapsing_compensation,
    fragmented_stability (recurring) with confidence >= 74.
    Institutional. Non-catastrophic. Non-dramatic. No collapse language.
    """
    from logic.stability_topology import compute_stability_topology as _ct
    if not active:
        return None
    topo = _ct(active)
    if topo.topology_confidence < 74:
        return None
    if topo.topology_state == "collapsing_compensation":
        return "↳ Часть execution layers постепенно удерживает stability за счёт временных compensating structures."
    if topo.topology_state == "structurally_unbalanced":
        return "↳ Краткосрочная operational continuity всё больше зависит от ограниченного числа structural support layers."
    if topo.topology_state == "fragmented_stability":
        recurring_count = sum(
            1 for i in active
            if getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        )
        if recurring_count >= 2:
            return "↳ Согласованность между recovery, observability и execution постепенно снижается."
    return None


def _phase_transition_line(active: list) -> Optional[str]:
    """
    Operational phase transition line for Telegram digest — Sprint 57.
    Emitted only for structural_pressure_formation, resilience_fragmentation,
    constrained_operation with confidence >= 72. Institutional, restrained.
    NEVER: panic, urgency, deterministic collapse, "critical", "danger", "must act now".
    """
    from logic.phase_transition import compute_phase_transition as _cpt
    if not active:
        return None
    pt = _cpt(active)
    if pt.phase_confidence < 72:
        return None
    if pt.phase == "constrained_operation":
        return "↳ Система постепенно входит в фазу constrained operation с накопленным coordination pressure."
    if pt.phase == "resilience_fragmentation":
        return "↳ Часть операционных слоёв начинает терять согласованность между восстановлением и observability."
    if pt.phase == "structural_pressure_formation":
        return "↳ Повторяющееся давление постепенно формирует признаки устойчивого structural load."
    return None


def _regime_line(active: list) -> Optional[str]:
    """
    Operational regime line for Telegram digest — Sprint 55.
    Emitted only for containment, constrained, or defensive (recurring) with confidence >= 70.
    Institutional tone. No panic. No consulting language.
    """
    from logic.operational_regime import compute_operational_regime as _cr
    if not active:
        return None
    reg = _cr(active)
    if reg.regime_confidence < 70:
        return None
    if reg.regime == "containment":
        return "↳ Часть recurring-сигналов начинает формировать containment-oriented operational regime."
    if reg.regime == "constrained":
        return "↳ Операционные решения сейчас ограничены сниженной наблюдаемостью и сужением пространства стабилизации."
    if reg.regime == "defensive":
        recurring_count = sum(
            1 for i in active
            if getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        )
        if recurring_count >= 2:
            return "↳ Система постепенно смещается в defensive operating mode с более узким пространством безопасных вмешательств."
    return None


def _energy_line(active: list) -> Optional[str]:
    """
    Decision energy line for Telegram digest — Sprint 56.
    Emitted only for disruptive, structurally_exhausting, or draining recurring, confidence >= 70.
    Institutional. Systems-oriented. No burnout language. No blame. No panic.
    """
    from logic.decision_energy import compute_decision_energy as _ce
    if not active:
        return None
    energy = _ce(active)
    if energy.energy_confidence < 70:
        return None
    if energy.energy_state == "structurally_exhausting":
        return "↳ Повторяющиеся стабилизационные циклы постепенно сужают пространство операционной гибкости."
    if energy.energy_state == "disruptive":
        return "↳ Часть текущих стабилизационных вмешательств постепенно начинает создавать повышенную координационную нагрузку на систему."
    if energy.energy_state == "draining":
        recurring_count = sum(
            1 for i in active
            if getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        )
        if recurring_count >= 2:
            return "↳ Повторяющиеся стабилизационные циклы постепенно сужают пространство операционной гибкости."
    return None


def _strategic_memory_line(active: list) -> Optional[str]:
    """
    Build strategic memory drift narrative for Telegram digest — Sprint 54.
    Only for compounding_repetition, historically_disconnected, or fragmented recurring with confidence >= 70.
    Institutional, historically aware, restrained.
    """
    compounding = [
        i for i in active
        if getattr(i, "strategic_drift_state", None) == "compounding_repetition"
        and (getattr(i, "drift_confidence", 0) or 0) >= 70
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    disconnected = [
        i for i in active
        if getattr(i, "strategic_drift_state", None) == "historically_disconnected"
        and (getattr(i, "drift_confidence", 0) or 0) >= 70
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    fragmented = [
        i for i in active
        if getattr(i, "strategic_drift_state", None) == "fragmented"
        and (getattr(i, "drift_confidence", 0) or 0) >= 70
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    if compounding:
        return "↳ Некоторые recurring-сценарии продолжают воспроизводить исторически неустойчивые паттерны восстановления."
    if disconnected:
        return "↳ Часть повторяющихся циклов постепенно отклоняется от ранее устойчивых сценариев стабилизации."
    if fragmented:
        return "↳ Повторяющиеся стабилизационные циклы демонстрируют признаки расхождения с ранее устойчивыми сценариями."
    return None


def _adaptive_capacity_line(active: list) -> Optional[str]:
    """
    Build adaptive capacity narrative for Telegram digest — Sprint 53.
    Only for deteriorating/strengthening recurring with confidence >= 70.
    Institutional, restrained, non-emotional.
    """
    deteriorating = [
        i for i in active
        if getattr(i, "adaptive_capacity_state", None) == "deteriorating"
        and (getattr(i, "adaptation_confidence", 0) or 0) >= 70
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    strengthening = [
        i for i in active
        if getattr(i, "adaptive_capacity_state", None) == "strengthening"
        and (getattr(i, "adaptation_confidence", 0) or 0) >= 70
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    if deteriorating:
        return "↳ Каждый следующий цикл начинает требовать большего времени на стабилизацию и восстановление observability."
    if strengthening:
        return "↳ Несмотря на повторяющееся давление, скорость операционного восстановления постепенно улучшается."
    return None


def _cascade_line(active: list) -> Optional[str]:
    """
    Build secondary pressure cascade narrative for Telegram digest — Sprint 50.
    Only for coupled_instability/structurally_cascading with probability >= 60 + recurring.
    No panic. No deterministic causality. Only operational coupling signal.
    """
    candidates = [
        i for i in active
        if getattr(i, "cascade_state", None) in ("coupled_instability", "structurally_cascading")
        and (getattr(i, "cascade_probability", 0) or 0) >= 60
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed", "persistent")
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    if not candidates:
        return None
    top = max(candidates, key=lambda i: (getattr(i, "cascade_probability", 0) or 0))
    if getattr(top, "cascade_state", None) == "structurally_cascading":
        return "↳ Системное давление начинает охватывать несколько операционных зон одновременно."
    return "↳ Операционное давление в части зон начинает создавать связанную нагрузку на смежные процессы."


def _timing_line(active: list) -> Optional[str]:
    """
    Build intervention timing narrative for Telegram digest — Sprint 48.
    Only for narrowing_window/structurally_late (recurring) or 2+ observation_phase fragmented.
    No raw days. No deadlines. No urgency language.
    """
    structurally_late = [
        i for i in active
        if getattr(i, "timing_state", None) == "structurally_late"
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    narrowing = [
        i for i in active
        if getattr(i, "timing_state", None) == "narrowing_window"
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed")
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    obs_fragmented = [
        i for i in active
        if getattr(i, "timing_state", None) == "observation_phase"
        and getattr(i, "signal_lifecycle_stage", None) == "recurring"
        and getattr(i, "status", "") not in ("resolved", "dismissed")
    ]
    if structurally_late:
        return "↳ Часть операционных сигналов перешла в фазу структурного давления — стабилизация потребует дополнительного времени."
    if len(narrowing) >= 2 or (narrowing and obs_fragmented):
        return "↳ Часть сигналов постепенно переходит в более чувствительную фазу стабилизации."
    if len(obs_fragmented) >= 2:
        return "↳ Система продолжает восстанавливать наблюдаемость после серии изменений."
    return None


def _counterfactual_line(active: list) -> Optional[str]:
    """
    Build counterfactual narrative for Telegram digest.
    Only for accelerating/structurally_locked + recurring/persistent + confidence >= 70.
    No exact numbers, no deadlines, no percentages.
    """
    candidates = [
        i for i in active
        if getattr(i, "counterfactual_pressure_state", None) in ("accelerating", "structurally_locked")
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed")
        and (getattr(i, "confidence", 0) or 0) >= 70
    ]
    if not candidates:
        return None
    top = candidates[0]
    state = getattr(top, "counterfactual_pressure_state", "")
    if state == "structurally_locked":
        return "↳ Часть операционной гибкости постепенно снижается по мере накопления давления."
    return "↳ При сохранении текущей динамики система обычно переходит в следующую фазу в течение ближайших недель."


def _lock_line(active: list) -> Optional[str]:
    """
    Build stabilization lock narrative for Telegram digest.
    Only emitted when 2+ insights are in waiting/stabilizing state.
    No exact deadlines. No countdowns.
    """
    locked = [
        i for i in active
        if getattr(i, "recovery_signal_state", None) in ("waiting", "stabilizing")
    ]
    if len(locked) < 2:
        return None
    # Check if reopening state is common — use softer framing
    reopening = [i for i in active if getattr(i, "recovery_signal_state", None) == "reopening"]
    if reopening:
        return "↳ После завершения текущего окна наблюдения можно будет безопасно продолжить стабилизацию."
    return "↳ Система постепенно возвращается к стабильной интерпретации результатов."


def _recovery_line(active: list) -> Optional[str]:
    """
    Build recovery narrative for Telegram digest.
    Only emitted for structural/unstable recurring with probability < 45.
    No optimistic framing. No raw scores. Operational realism only.
    """
    candidates = [
        i for i in active
        if getattr(i, "recovery_state", None) in ("structural", "unstable")
        and (getattr(i, "recovery_probability", 100) or 100) < 45
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed")
    ]
    if not candidates:
        return None
    top = candidates[0]
    state = getattr(top, "recovery_state", "")
    if state == "structural":
        return "↳ Текущее давление редко стабилизируется без пересмотра закупочной или ценовой модели."
    return "↳ Даже после частичной стабилизации паттерн может возвращаться при росте нагрузки."


def _comparison_line(active: list) -> Optional[str]:
    """
    Telegram comparative narrative.
    One restrained line only. Never a matrix dump.
    Emitted when 2+ insights have path_comparison set and at least one is recurring/confirmed.
    """
    with_comparison = [
        i for i in active
        if getattr(i, "path_comparison", None) is not None
        and getattr(i, "signal_lifecycle_stage", None) in ("recurring", "confirmed")
    ]
    if len(with_comparison) < 1:
        return None
    top = with_comparison[0]
    dim = getattr(getattr(top, "path_comparison", None), "comparison_dimension", "")
    if dim == "reversibility":
        return "↳ Структурная стабилизация обычно развивается медленнее, но формирует более устойчивый recovery profile."
    if dim == "speed":
        return "↳ Более быстрые stabilization-сценарии могут сопровождаться временной revenue volatility."
    if dim == "observability":
        return "↳ Более быстрый сценарий может временно снизить observability результатов."
    return "↳ Некоторые сигналы допускают несколько stabilization paths с разным volatility profile."


def _strategy_line(active: list) -> Optional[str]:
    """
    Operator strategy note for Telegram digest.
    Emitted only for aggressive/oscillating/weak pacing/operator-driven volatility.
    """
    _OPERATOR_CATS = {"high_ad_spend", "margin_crisis"}
    recurring  = [i for i in active if getattr(i, "signal_lifecycle_stage", None) == "recurring"]
    escalating = [i for i in active if getattr(i, "trajectory_state", None) in ("escalating", "structurally_accumulating")]
    temp_fixes = [i for i in active
                  if getattr(i, "outcome_state", None) == "temporary"
                  and (getattr(i, "signal_recurrence_count", None) or 0) >= 2]
    waiting    = [i for i in active if getattr(i, "recovery_signal_state", None) == "waiting"]
    op_rec     = [i for i in recurring if getattr(i, "key", "").split(":")[0] in _OPERATOR_CATS]

    if len(recurring) >= 2 and len(temp_fixes) >= 1:
        return "↳ Паттерн указывает на склонность к ранним вмешательствам до завершения стабилизационного окна."
    if len(recurring) >= 3 and len(escalating) >= 1:
        return "↳ Высокая частота вмешательств без завершённых стабилизационных циклов."
    if len(waiting) >= 2 and len(recurring) >= 1:
        return "↳ Слабая дисциплина пейсинга увеличивает накопленное давление при повторяющихся сигналах."
    if len(op_rec) >= 2:
        return "↳ Значительная часть волатильности связана с решениями оператора, а не рыночной динамикой."
    return None


def _observability_recovery_line(active: list) -> Optional[str]:
    """
    Observability recovery note for Telegram digest — Sprint 44.
    Emitted for fragmented/reset_required states, or recovering with confidence >= 70.
    One restrained sentence. No deadlines, no commands, no urgency.
    """
    from logic.observability_recovery import compute_observability_recovery as _cor
    concurrent = sum(
        1 for i in active
        if getattr(i, "recovery_signal_state", None) in ("waiting", "stabilizing")
    )
    states = [_cor(i, concurrent_active=concurrent).obs_recovery_state for i in active]
    severe = [s for s in states if s in ("fragmented", "reset_required")]
    recovering = [
        i for i, s in zip(active, states)
        if s == "recovering" and (getattr(i, "confidence", 0) or 0) >= 70
    ]
    if severe:
        return "↳ Часть сигналов пока остаётся в фазе ограниченной наблюдаемости из-за параллельных вмешательств."
    if recovering:
        return "↳ Система продолжает наблюдать эффект предыдущих изменений. Новые выводы станут надёжнее после стабилизации окна наблюдения."
    return None


def _commitment_line(active: list) -> Optional[str]:
    """
    Strategy commitment note for Telegram digest — Sprint 43.
    One restrained operational note only. Never sends strategy matrix.
    Emitted only when commitment state is fragmented or clearly stabilizing.
    """
    from logic.strategy_commitment import compute_strategy_commitment as _cc
    commitment = _cc(active, [])
    state = commitment.commitment_state
    if state in ("fragmented", "abandoned"):
        return "↳ Частая смена stabilization path может снижать observability recovery dynamics."
    if state == "stabilizing":
        return "↳ Recovery cycle остаётся достаточно последовательным."
    return None


def _tradeoff_line(active: list) -> Optional[str]:
    """
    Build tradeoff narrative for Telegram digest.
    Only emitted when a moderate+ tradeoff exists AND trajectory is escalating/persistent.
    Restrained: states secondary effect and duration. No alarm language.
    """
    candidates = [
        i for i in active
        if getattr(i, "tradeoff_note", None)
        and getattr(i, "tradeoff_severity", None) in ("moderate", "significant")
        and getattr(i, "trajectory_state", None) in ("escalating", "persistent", "structurally_accumulating")
    ]
    if not candidates:
        return None
    top = candidates[0]
    note = getattr(top, "tradeoff_note", "")
    days = getattr(top, "tradeoff_duration_days", None)
    benefit = getattr(top, "stabilization_benefit", None)
    line = f"↳ {note}"
    if days:
        line += f" (≈ {days}д)"
    if benefit:
        line += f" {benefit}"
    return line


# ── Core per-user logic ────────────────────────────────────────────────────────

async def _process_user(
    user: User,
    tg_settings: TelegramSettings,
    db: AsyncSession,
) -> int:
    """Process one user. Returns count of Telegram messages sent."""
    from routers.action_engine import _compute_insights  # local import (circular guard)

    if not user.telegram_chat_id:
        return 0

    chat_id = user.telegram_chat_id
    uid     = str(user.id)

    # Load insight statuses
    s_res = await db.execute(select(InsightRecord).where(InsightRecord.user_id == uid))
    _records = s_res.scalars().all()
    statuses: dict[str, tuple[str, str]] = {
        r.insight_key: (r.status, r.id) for r in _records
    }
    resolved_history: dict[str, datetime] = {
        r.insight_key: r.updated_at
        for r in _records
        if r.status in ("resolved", "improved", "stabilized") and r.updated_at
    }

    # Notification recurrence counts (90-day window) — used for scenario_context_for_telegram
    from datetime import timedelta
    cutoff_90 = datetime.utcnow() - timedelta(days=90)
    nc_res = await db.execute(
        select(TelegramNotificationLog.notification_key, func.count().label("cnt"))
        .where(
            TelegramNotificationLog.user_id == uid,
            TelegramNotificationLog.notification_key.like("insight:%"),
            TelegramNotificationLog.sent_at >= cutoff_90,
        )
        .group_by(TelegramNotificationLog.notification_key)
    )
    notif_counts: dict[str, int] = {
        row.notification_key.replace("insight:", "", 1): row.cnt
        for row in nc_res
    }

    try:
        insights = await _compute_insights(uid, db, statuses)
    except Exception:
        logger.exception("intelligence_loop: _compute_insights failed for user %s", uid)
        return 0

    # Sprint 83: bind cognition output to the constitutional substrate AFTER
    # computation (read-only, deterministic, fail-closed). Observation only —
    # cognition behavior, Telegram output, and InsightRecord persistence unchanged.
    try:
        from cognition_binding import observe_and_record
        observe_and_record(insights)
    except Exception:
        pass

    # Active, non-demo insights
    active = [i for i in insights if i.status not in ("resolved", "dismissed") and not i.is_demo]

    # Sort by priority tier then impact_score descending
    active.sort(key=lambda i: (
        _TYPE_PRIORITY.get(i.key.split(":")[0], 9),
        -(i.impact_score or 0),
    ))

    # Group by normalized product_name
    groups: dict[str, list] = defaultdict(list)
    for ins in active:
        key = (ins.product_name or "").strip().lower() or ins.key
        groups[key].append(ins)

    # Daily alert cap (counted before this cycle)
    alerts_today = await _alerts_sent_today(db, uid)

    sent = 0

    # ── Operational focus briefing (once per 48h, before per-insight alerts) ──
    focus = compute_operational_focus(active, [], [])
    if focus and not focus.is_stable and alerts_today < _MAX_ALERTS_PER_DAY:
        focus_key = f"focus:{focus.root_cause}"
        if not await _was_sent_recently(db, uid, focus_key, 48):
            briefing_text = (
                f"📋 <b>Операционный обзор — Бизнес-Пульт</b>\n\n"
                f"<b>Внимание сейчас:</b>\n"
                f"{focus.title}\n\n"
                f"↳ {focus.reason}\n"
                f"↳ Вероятное следствие: {focus.expected_impact}"
            )
            if focus.primary_action:
                briefing_text += f"\n↳ {focus.primary_action}"
            if focus.secondary_action:
                briefing_text += f"\n↳ {focus.secondary_action}"
            keyboard = [[
                {"text": "⚡ Детали", "url": f"{_FE}/dashboard/action-engine"},
                {"text": "🏠 Дашборд", "url": f"{_FE}/dashboard"},
            ]]
            ok = await send_message_with_keyboard(chat_id, briefing_text, keyboard)
            if ok:
                await _log_sent(db, uid, focus_key)
                alerts_today += 1
                sent += 1

    # ── Systemic portfolio pattern alert (once/24h, max 1 per cycle) ──────────
    if alerts_today < _MAX_ALERTS_PER_DAY:
        portfolio_key = "portfolio:systemic"
        if not await _was_sent_recently(db, uid, portfolio_key, 24):
            pp_all = detect_portfolio_patterns(
                [insight_to_summary(i) for i in active],
                resolved_history=resolved_history,
                insights_raw=active,
            )
            pp_systemic = [p for p in pp_all if p.stabilization_complexity == "systemic" and p.confidence >= 75]
            if pp_systemic:
                p0 = pp_systemic[0]
                mp_label = {
                    "wildberries": "Wildberries", "ozon": "Ozon", "yandex_market": "Яндекс Маркет",
                }.get(p0.marketplace or "", p0.marketplace or "")
                mp_part = f"{mp_label}: " if mp_label else ""
                portfolio_text = (
                    f"📊 <b>Контекст портфеля — Бизнес-Пульт</b>\n\n"
                    f"↳ <i>{mp_part}{p0.operational_summary}</i>"
                )
                if len(pp_systemic) > 1:
                    portfolio_text += f"\n↳ <i>{pp_systemic[1].operational_summary}</i>"
                # Sprint 28: add root cause note (restrained, only if confidence >= 70)
                rc_line = _root_cause_line(p0)
                if rc_line:
                    portfolio_text += f"\n\n{rc_line}"
                # Sprint 28: add historical memory (max 1 systemic narrative)
                mem_line = _cross_mp_memory_line(p0)
                if mem_line:
                    portfolio_text += f"\n{mem_line}"
                # Sprint 32: add sequencing line (only if conditions met)
                seq_line = _sequencing_line(active, pp_systemic, fatigue_score=0.0)
                if seq_line:
                    portfolio_text += f"\n\n{seq_line}"
                # Sprint 33: add trajectory line (only escalating/structurally_accumulating)
                traj_line = _trajectory_line(active)
                if traj_line:
                    portfolio_text += f"\n{traj_line}"
                # Sprint 34: add tradeoff line (moderate+ severity, escalating/persistent trajectory only)
                td_line = _tradeoff_line(active)
                if td_line:
                    portfolio_text += f"\n{td_line}"
                # Sprint 35: add forecast line (fragile/critical + recurring/persistent + conf >= 70)
                fc_line = _forecast_line(active)
                if fc_line:
                    portfolio_text += f"\n{fc_line}"
                # Sprint 36: add recovery line (structural/unstable recurring + probability < 45)
                rec_line = _recovery_line(active)
                if rec_line:
                    portfolio_text += f"\n{rec_line}"
                # Sprint 37: add capacity line (overloaded or saturated + recurring)
                cap_line = _capacity_line(active, pp_systemic, fatigue_score=0.0)
                if cap_line:
                    portfolio_text += f"\n{cap_line}"
                # Sprint 38: add lock line (2+ insights in waiting/stabilizing)
                lk_line = _lock_line(active)
                if lk_line:
                    portfolio_text += f"\n{lk_line}"
                # Sprint 39: counterfactual (accelerating/locked + recurring + confidence >= 70)
                cf_line = _counterfactual_line(active)
                if cf_line:
                    portfolio_text += f"\n{cf_line}"
                # Sprint 40: operator strategy (aggressive/oscillating/weak pacing/operator-driven)
                st_line = _strategy_line(active)
                if st_line:
                    portfolio_text += f"\n{st_line}"
                # Sprint 42: comparative simulation (1 restrained line, 2+ comparisons with recurring/confirmed)
                cmp_line = _comparison_line(active)
                if cmp_line:
                    portfolio_text += f"\n{cmp_line}"
                # Sprint 43: strategy commitment (fragmented/stabilizing only — 1 operational note)
                comm_line = _commitment_line(active)
                if comm_line:
                    portfolio_text += f"\n{comm_line}"
                # Sprint 44: observability recovery (fragmented/reset_required or recovering + conf >= 70)
                obs_line = _observability_recovery_line(active)
                if obs_line:
                    portfolio_text += f"\n{obs_line}"
                # Sprint 48: intervention timing (narrowing/structurally_late recurring or 2+ obs fragmented)
                tmg_line = _timing_line(active)
                if tmg_line:
                    portfolio_text += f"\n{tmg_line}"
                # Sprint 49: intervention reversal (overextended/structurally_locked + prob >= 70 + recurring)
                rev_line = _reversal_line(active)
                if rev_line:
                    portfolio_text += f"\n{rev_line}"
                # Sprint 50: secondary pressure cascade (coupled/systemic + prob >= 60 + recurring)
                casc_line = _cascade_line(active)
                if casc_line:
                    portfolio_text += f"\n{casc_line}"
                # Sprint 51: resilience snapshot (brittle/collapsing/exhausted + score <= 30 + recurring)
                res_line = _resilience_line(active)
                if res_line:
                    portfolio_text += f"\n{res_line}"
                # Sprint 52: resilience trajectory (degrading recurring OR structurally_degrading + confidence >= 70)
                res_traj_line = _resilience_trajectory_line(active)
                if res_traj_line:
                    portfolio_text += f"\n{res_traj_line}"
                # Sprint 53: adaptive capacity (deteriorating/strengthening recurring + confidence >= 70)
                adapt_line = _adaptive_capacity_line(active)
                if adapt_line:
                    portfolio_text += f"\n{adapt_line}"
                # Sprint 54: strategic memory drift (compounding/disconnected/fragmented recurring + confidence >= 70)
                strat_mem_line = _strategic_memory_line(active)
                if strat_mem_line:
                    portfolio_text += f"\n{strat_mem_line}"
                # Sprint 55: operational regime (containment/constrained/defensive recurring + confidence >= 70)
                reg_line = _regime_line(active)
                if reg_line:
                    portfolio_text += f"\n{reg_line}"
                # Sprint 56: decision energy (disruptive/structurally_exhausting/draining recurring + confidence >= 70)
                en_line = _energy_line(active)
                if en_line:
                    portfolio_text += f"\n{en_line}"
                # Sprint 57: phase transition (structural_pressure_formation/resilience_fragmentation/constrained_operation + confidence >= 72)
                phase_line = _phase_transition_line(active)
                if phase_line:
                    portfolio_text += f"\n{phase_line}"
                # Sprint 58: stability topology (structurally_unbalanced/collapsing_compensation/fragmented_stability recurring + confidence >= 74)
                topo_line = _topology_line(active)
                if topo_line:
                    portfolio_text += f"\n{topo_line}"
                # Sprint 59: operational doctrine (structurally_embedded/rigid/stabilization_dependency recurring + confidence >= 76)
                doctrine_line = _doctrine_line(active)
                if doctrine_line:
                    portfolio_text += f"\n{doctrine_line}"
                # Sprint 60: institutional inertia (structural_inertia/locked/freeze + confidence >= 78)
                inertia_line = _inertia_line(active)
                if inertia_line:
                    portfolio_text += f"\n{inertia_line}"
                # Sprint 61: structural recovery capacity (restructuring_dependent/continuity_without_recovery/exhausted + confidence >= 80)
                recovery_cap_line = _recovery_capacity_line(active)
                if recovery_cap_line:
                    portfolio_text += f"\n{recovery_cap_line}"
                keyboard_pp = [[
                    {"text": "🔍 Разведка", "url": f"{_FE}/dashboard/action-engine"},
                    {"text": "🏠 Дашборд",  "url": f"{_FE}/dashboard"},
                ]]
                ok = await send_message_with_keyboard(chat_id, portfolio_text, keyboard_pp)
                if ok:
                    await _log_sent(db, uid, portfolio_key)
                    alerts_today += 1
                    sent += 1

    # ── Operational intelligence daily digest (once/24h) ────────────────────────
    digest_key = "op_summary:daily"
    if alerts_today < _MAX_ALERTS_PER_DAY and not await _was_sent_recently(db, uid, digest_key, 24):
        pp_for_summary = detect_portfolio_patterns([insight_to_summary(i) for i in active])
        fatigue_score  = min(1.0, len([i for i in active if getattr(i, "signal_lifecycle_stage", None) == "recurring"]) / max(len(active), 1))
        stability_credit = len([i for i in active if getattr(i, "signal_lifecycle_stage", None) == "stabilized"]) / max(len(active), 1)
        try:
            op = _build_op_summary(
                insights=active,
                portfolio_patterns=pp_for_summary,
                resolved_history=resolved_history,
                fatigue_score=fatigue_score,
                stability_credit=stability_credit,
            )
            _meaningful = (
                op.portfolio_direction in ("unstable", "expanding_pressure")
                or len(op.recurring_patterns) > 0
            )
            if _meaningful and op.narrative_lines:
                digest_lines = op.narrative_lines[:3]
                digest_text = "📍 <b>Операционное резюме — Бизнес-Пульт</b>\n\n" + "\n".join(
                    f"📍 {line}" for line in digest_lines
                )
                keyboard_digest = [[
                    {"text": "📊 Дашборд", "url": f"{_FE}/dashboard"},
                    {"text": "⚡ Детали",  "url": f"{_FE}/dashboard/action-engine"},
                ]]
                ok = await send_message_with_keyboard(chat_id, digest_text, keyboard_digest)
                if ok:
                    await _log_sent(db, uid, digest_key)
                    alerts_today += 1
                    sent += 1
                    logger.info("intelligence_loop: sent op_summary digest to user %s", uid)
        except Exception:
            logger.exception("intelligence_loop: op_summary digest failed for user %s", uid)

    for group_key, ins_list in groups.items():
        # Filter by per-key cooldowns AND marketplace maturity delay
        fresh = []
        for ins in ins_list:
            # Sprint 27: stale (historical) signals auto-suppressed
            if getattr(ins, "signal_decay_state", None) == "stale":
                continue
            # Sprint 30: slowing (fading) signals are digest-only, not individual alerts
            if getattr(ins, "signal_decay_state", None) == "fading":
                continue
            prefix    = ins.key.split(":")[0]
            cooldown  = _COOLDOWN.get(prefix, 24)
            notif_key = f"insight:{ins.key}"
            if await _was_sent_recently(db, uid, notif_key, cooldown):
                continue
            # Respect marketplace attribution delay (e.g. Ozon 48h)
            mp = ins.marketplace or ""
            if not await _signal_is_mature(db, uid, ins.key, mp):
                continue
            fresh.append(ins)

        if not fresh:
            continue

        # Check daily cap (applies to any insight message)
        if alerts_today >= _MAX_ALERTS_PER_DAY:
            continue

        # Check per-type notification preferences
        def _is_enabled(ins) -> bool:
            p = ins.key.split(":")[0]
            if p in _ALERT_TYPES:
                return bool(tg_settings.notify_insights)
            if p == "seo_opportunity":
                return bool(tg_settings.notify_seo_opportunity)
            if p in _GROWTH_TYPES | _SEO_TYPES - {"seo_opportunity"}:
                return bool(tg_settings.notify_sales_growth)
            return True

        fresh = [i for i in fresh if _is_enabled(i)]
        if not fresh:
            continue

        # Build message
        ins_dicts = [_insight_to_dict(i, notif_counts) for i in fresh]

        if len(fresh) == 1:
            ins_d  = ins_dicts[0]
            prefix = ins_d["rule_type"]
            if prefix == "seo_opportunity":
                text, keyboard = _fmt_seo_opportunity(ins_d)
            elif prefix == "sales_growth":
                text, keyboard = _fmt_sales_growth(ins_d)
            elif prefix == "high_rating":
                text, keyboard = _fmt_high_rating(ins_d)
            elif prefix in _ALERT_TYPES:
                text, keyboard = _fmt_critical_alert(ins_d)
            else:
                continue
        else:
            # Digest: group all fresh insights for this product
            name = fresh[0].product_name or ""
            mp   = fresh[0].marketplace or ""
            text, keyboard = _fmt_digest(name, mp, ins_dicts)

        ok = await send_message_with_keyboard(chat_id, text, keyboard)
        if ok:
            for ins in fresh:
                await _log_sent(db, uid, f"insight:{ins.key}")
            alerts_today += 1
            sent += 1
            logger.info("intelligence_loop: sent %s insight(s) for product '%s' to user %s",
                        len(fresh), group_key, uid)

    # ── Retention nudge ─────────────────────────────────────────────────────────
    if tg_settings.notify_retention:
        ret_key    = "retention:loop"
        cooldown_h = (tg_settings.retention_inactive_days or 3) * 24
        if not await _was_sent_recently(db, uid, ret_key, cooldown_h):
            count = len(active)
            if count > 0:
                text, keyboard = _fmt_retention(user.name, count)
                ok = await send_message_with_keyboard(chat_id, text, keyboard)
                if ok:
                    await _log_sent(db, uid, ret_key)
                    sent += 1
                    logger.info("intelligence_loop: sent retention to user %s", uid)

    return sent


# ── Main loop ──────────────────────────────────────────────────────────────────

async def run_intelligence_loop() -> None:
    """Runs every 30 minutes."""
    logger.info("Telegram Intelligence Loop started")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(User, TelegramSettings)
                    .join(TelegramSettings, TelegramSettings.user_id == User.id)
                    .where(
                        User.telegram_chat_id.isnot(None),
                        User.deleted_at.is_(None),
                    )
                )
                rows = result.all()

            total_sent = 0
            for user, tg_settings in rows:
                async with AsyncSessionLocal() as db:
                    try:
                        n = await _process_user(user, tg_settings, db)
                        total_sent += n
                    except Exception:
                        logger.exception("intelligence_loop: error processing user %s", user.id)

            if total_sent:
                logger.info("intelligence_loop: sent %d notifications this cycle", total_sent)

        except Exception:
            logger.exception("intelligence_loop: cycle error")

        await asyncio.sleep(30 * 60)
