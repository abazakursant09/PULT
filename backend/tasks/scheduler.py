"""
Планировщик Telegram-отчётов.
Запускается как фоновая задача. Проверяет каждую минуту, отправляет по расписанию.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import AsyncSessionLocal
from models.user import User
from models.telegram_settings import TelegramSettings
from models.telegram_notification_log import TelegramNotificationLog
from services.telegram import send_message, send_message_with_keyboard

_FRONTEND = settings.frontend_url.rstrip("/")
logger = logging.getLogger(__name__)

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}

# Unicode sparkline chars
_SPARK = "▁▂▃▄▅▆▇"


# ── Format helpers ─────────────────────────────────────────────────────────────

def _fk(val: float) -> str:
    """Format float as '148k' or '1.2M'."""
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{int(val / 1000)}k"
    return f"{int(val)}"


def _delta_str(pct: float | None) -> str:
    """' ↗+12%' / ' ↘-4%' / ''"""
    if pct is None:
        return ""
    arr  = " ↗" if pct > 0 else (" ↘" if pct < 0 else " →")
    sign = "+" if pct > 0 else ""
    return f"{arr}{sign}{pct:.0f}%"


def _spark_from_delta(delta: float | None) -> str:
    """6-char unicode sparkline + arrow from a CTR delta."""
    if delta is None:
        return ""
    if delta >= 15:   bars = "▁▂▄▆▇█"
    elif delta >= 8:  bars = "▁▂▃▅▆▇"
    elif delta >= 3:  bars = "▂▃▄▄▅▆"
    elif delta >= 0:  bars = "▃▄▄▄▅▅"
    elif delta >= -5: bars = "▅▅▄▄▃▃"
    else:             bars = "▆▅▄▃▂▁"
    arrow = "↗" if delta > 0 else ("↘" if delta < 0 else "→")
    sign  = "+" if delta > 0 else ""
    return f"{bars} {arrow}{sign}{delta:.1f}%"


def _conf_label(rebuild_count: int) -> tuple[str, str]:
    if rebuild_count >= 10:
        return "High", f"основано на {rebuild_count} rebuilds"
    if rebuild_count >= 3:
        return "Medium", f"основано на {rebuild_count} rebuilds"
    return "Low", "мало данных"


# ── Weekly anti-spam ───────────────────────────────────────────────────────────

async def _weekly_report_already_sent(user_id: str) -> bool:
    cutoff = datetime.utcnow() - timedelta(days=6)
    async with AsyncSessionLocal() as db:
        q = await db.execute(
            select(TelegramNotificationLog)
            .where(
                TelegramNotificationLog.user_id == user_id,
                TelegramNotificationLog.notification_key == "weekly:intel_report",
                TelegramNotificationLog.sent_at >= cutoff,
            )
            .limit(1)
        )
        return q.scalar_one_or_none() is not None


async def _log_weekly_sent(user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        db.add(TelegramNotificationLog(
            user_id=user_id,
            notification_key="weekly:intel_report",
        ))
        await db.commit()


# ── Top action from Action Engine ─────────────────────────────────────────────

async def _get_top_action(user_id: str) -> str | None:
    try:
        from routers.action_engine import _compute_insights
        from models.insight import InsightRecord
        async with AsyncSessionLocal() as db:
            s_res = await db.execute(
                select(InsightRecord).where(InsightRecord.user_id == user_id)
            )
            statuses = {r.insight_key: (r.status, r.id) for r in s_res.scalars().all()}
            insights = await _compute_insights(user_id, db, statuses)

        active = [i for i in insights
                  if i.status not in ("resolved", "dismissed") and not i.is_demo]
        if not active:
            return None

        active.sort(key=lambda i: i.impact_score or 0, reverse=True)
        top  = active[0]
        name = top.product_name or "товар"

        return {
            "seo_opportunity": f"Пересобрать карточку «{name[:30]}»",
            "high_ad_spend":   f"Снизить ДРР для «{name[:30]}»",
            "margin_crisis":   f"Пересмотреть цену «{name[:30]}»",
            "low_stock":       f"Пополнить остаток «{name[:30]}»",
            "sales_growth":    f"Масштабировать рекламу «{name[:30]}»",
            "high_rating":     f"Пересобрать карточку с рейтингом «{name[:30]}»",
        }.get(top.key.split(":")[0], f"Проверить дашборд — {len(active)} активных инсайтов")
    except Exception:
        return None


# ── Daily report ───────────────────────────────────────────────────────────────

async def _build_daily_report(user: User) -> str:
    from services.finance_aggregator import get_daily_summary

    now     = datetime.now()
    uid     = str(user.id)
    summary = await get_daily_summary(uid)
    header  = (
        f"📊 <b>Ежедневный отчёт — Бизнес-Пульт</b>\n"
        f"<i>{now.strftime('%d.%m.%Y, %H:%M')}</i>\n\n"
        f"👤 <b>{user.name}</b>\n"
    )
    footer = f"\n🔗 <a href='{_FRONTEND}/dashboard'>Открыть дашборд</a>"

    if not summary.has_data:
        return (
            header
            + "\nℹ️ <i>DEMO DATA — импортируйте выгрузку WB/Ozon/YM для реальных отчётов</i>"
            + footer
        )

    d = summary.data

    lines = [header]

    # Period label
    lines.append(f"\n<b>{summary.period_label.capitalize()}:</b>")

    # Revenue + orders
    rev_str = f"📈 Выручка: <b>{_fk(d.revenue)} ₽</b>{_delta_str(summary.delta_revenue_pct)}"
    ord_str = f"📦 Заказов: <b>{d.orders}</b>{_delta_str(summary.delta_orders_pct)}"
    lines.append(f"{rev_str}   {ord_str}")

    # Profit + margin
    profit     = d.effective_profit
    margin_str = f" (маржа {d.margin_pct:.0f}%)" if d.margin_pct is not None else ""
    lines.append(f"💰 Прибыль: <b>{_fk(profit)} ₽</b>{margin_str}")

    # Ad spend / DRR
    if d.ad_spend > 0:
        drr_str = f" (ДРР {d.drr_pct:.1f}%)" if d.drr_pct is not None else ""
        lines.append(f"📣 Реклама: {_fk(d.ad_spend)} ₽{drr_str}")

    # Top product
    if summary.top_product:
        lines.append(f"\n🔥 Лидер: <b>{summary.top_product}</b>")

    # Rating + active products
    meta_parts = []
    if summary.avg_rating:
        meta_parts.append(f"⭐ {summary.avg_rating:.1f} ★")
    if summary.active_products:
        meta_parts.append(f"{summary.active_products} товаров")
    if meta_parts:
        lines.append("   ".join(meta_parts))

    lines.append(footer)
    return "\n".join(lines)


# ── Weekly Intelligence Report ─────────────────────────────────────────────────

async def _build_weekly_report(user: User) -> tuple[str, list[list[dict]]]:
    from routers.rebuild_tracker import get_weekly_rebuild_summary, get_top_product_this_week
    from services.finance_aggregator import get_weekly_summary

    now = datetime.now()
    uid = str(user.id)

    # Rebuild learning data
    try:
        async with AsyncSessionLocal() as db:
            rb          = await get_weekly_rebuild_summary(uid, db)
            top_product = await get_top_product_this_week(uid, db)
    except Exception:
        rb          = {"rebuild_count": 0, "avg_ctr_delta": None, "total_estimated_gain": 0,
                       "winners_count": 0, "best_style": None, "total_rebuilds": 0}
        top_product = None

    # Finance summary (real or demo)
    try:
        ws = await get_weekly_summary(uid)
    except Exception:
        ws = None

    total_rebuilds = rb.get("total_rebuilds", rb.get("rebuild_count", 0))
    conf_label, conf_footnote = _conf_label(total_rebuilds)

    # ── Build report ───────────────────────────────────────────────────────────

    # 1. Header
    if ws and ws.week_label:
        week_label = ws.week_label
    else:
        week_start = now - timedelta(days=now.weekday())
        week_label = f"{week_start.strftime('%d.%m')} – {now.strftime('%d.%m.%Y')}"
    lines = [
        f"📊 <b>Weekly Report — Бизнес-Пульт</b>",
        f"<i>Неделя {week_label}</i>",
        f"",
        f"👤 <b>{user.name}</b>",
        f"",
    ]

    # 2. Key finance metrics (real or demo)
    if ws and ws.has_data:
        d = ws.data

        orders_str = f"📦 {d.orders} шт.{_delta_str(ws.delta_orders_pct)}"
        rev_str    = f"📈 {_fk(d.revenue)} ₽{_delta_str(ws.delta_revenue_pct)} выручки"
        lines.append(f"{orders_str}   {rev_str}")

        profit      = d.effective_profit
        profit_str  = f"💰 {_fk(profit)} ₽{_delta_str(ws.delta_profit_pct)} прибыли"
        margin_str  = f" (маржа {d.margin_pct:.0f}%)" if d.margin_pct is not None else ""
        loss_str    = f"   🔴 {ws.loss_count} убыточных" if ws.loss_count > 0 else ""
        rating_str  = f"   ⭐ {ws.avg_rating:.1f} ★" if ws.avg_rating else ""
        lines.append(f"{profit_str}{margin_str}{loss_str}{rating_str}")

        # DRR (ad efficiency line)
        if d.ad_spend > 0 and d.drr_pct is not None:
            lines.append(f"📣 ДРР: {d.drr_pct:.1f}%   реклама: {_fk(d.ad_spend)} ₽")

        # Top finance product (by revenue) if different from rebuild top_product
        if ws.top_products:
            fin_top = ws.top_products[0]
            fin_name = fin_top["title"][:35]
            # Only show if rebuild tracker didn't already give us a top product
            if not top_product:
                lines.append(f"")
                lines.append(f"💼 Лидер по выручке: <b>{fin_name}</b>")
    else:
        # Demo / no data fallback
        lines += [
            f"📦 342 шт. продано   📈 1 038k ₽ выручки",
            f"⭐ 4.6 ★ рейтинг   🔴 2 убыточных позиции",
        ]
        lines.append(f"")
        lines.append(f"<i>ℹ️ DEMO — импортируйте данные для реальных метрик</i>")

    lines.append(f"")

    # 3. SEO rebuilds + sparkline
    if rb["rebuild_count"] > 0:
        spark    = _spark_from_delta(rb["avg_ctr_delta"])
        ctr_part = f"   📊 CTR: {spark}" if rb["avg_ctr_delta"] is not None else ""
        lines.append(f"🔁 SEO rebuilds: {rb['rebuild_count']}{ctr_part}")
        if rb["winners_count"] > 0:
            lines.append(f"🏆 {rb['winners_count']} победит. A/B")
        if rb["best_style"]:
            lines.append(f"✨ Лучший стиль: <b>{rb['best_style']}</b>")
        lines.append(f"")

    # 4. Top CTR product (from rebuild tracker)
    if top_product:
        pname  = top_product["name"][:35]
        pdelta = top_product.get("delta_ctr_percent")
        if pdelta is not None:
            lines.append(f"🔥 Лидер недели: <b>{pname}</b> — +{pdelta:.0f}% CTR")
            lines.append(f"")

    # 5. Potential gain (from rebuild tracker)
    if rb.get("total_estimated_gain", 0) > 0:
        k = int(rb["total_estimated_gain"] / 1000)
        lines.append(f"💰 Потенциал роста: ≈ +{k}k ₽")
        lines.append(f"")

    # 6. Learning insights — what the system learned this week
    try:
        from routers.seo_intelligence import get_weekly_learning_insights
        async with AsyncSessionLocal() as _db:
            learning = await get_weekly_learning_insights(uid, _db)
    except Exception:
        learning = None

    if learning and learning.get("bullets"):
        lines.append(f"🧠 <b>Чему научился ПУЛЬТ:</b>")
        for bullet in learning["bullets"]:
            lines.append(f"• {bullet}")
        if learning.get("best_category") and learning.get("best_category_delta"):
            lines.append(f"")
            lines.append(
                f"🏆 Лучшая категория: <b>{learning['best_category']}</b>"
                f" (+{learning['best_category_delta']}% CTR)"
            )
        lines.append(f"")

    # 7. Single highest-impact action
    top_action = await _get_top_action(uid)
    if top_action:
        lines.append(f"👉 <b>Главное сейчас:</b> {top_action}")
        lines.append(f"")

    # 7. Confidence footer
    lines.append(f"<i>Confidence: {conf_label} ({conf_footnote})</i>")

    text = "\n".join(lines)
    keyboard = [[
        {"text": "📈 Финансы",      "url": f"{_FRONTEND}/dashboard/finance"},
        {"text": "✨ SEO-карточки", "url": f"{_FRONTEND}/dashboard/seo-cards"},
        {"text": "🚀 Разведка",     "url": f"{_FRONTEND}/dashboard/action-engine"},
    ]]
    return text, keyboard


# ── Schedulers ─────────────────────────────────────────────────────────────────

async def _send_daily_reports() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User, TelegramSettings)
            .join(TelegramSettings, TelegramSettings.user_id == User.id)
            .where(TelegramSettings.daily_report == True)
        )
        rows = result.all()

    for user, ts in rows:
        if not user.telegram_chat_id:
            continue
        now_time = datetime.now().strftime("%H:%M")
        if now_time != ts.daily_report_time:
            continue
        try:
            text = await _build_daily_report(user)
            ok   = await send_message(user.telegram_chat_id, text)
            if ok:
                logger.info("Daily report sent to user %s", user.id)
        except Exception:
            logger.exception("Failed to send daily report to user %s", user.id)


async def _send_weekly_reports() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User, TelegramSettings)
            .join(TelegramSettings, TelegramSettings.user_id == User.id)
            .where(TelegramSettings.weekly_summary == True)
        )
        rows = result.all()

    for user, ts in rows:
        if not user.telegram_chat_id:
            continue
        now        = datetime.now()
        target_day = WEEKDAYS.get(ts.weekly_summary_day, 6)
        if now.weekday() != target_day:
            continue
        now_time = now.strftime("%H:%M")
        if now_time != ts.weekly_summary_time:
            continue

        if await _weekly_report_already_sent(str(user.id)):
            continue

        try:
            text, keyboard = await _build_weekly_report(user)
            ok = await send_message_with_keyboard(user.telegram_chat_id, text, keyboard)
            if ok:
                await _log_weekly_sent(str(user.id))
                logger.info("Weekly intelligence report sent to user %s", user.id)
        except Exception:
            logger.exception("Failed to send weekly report to user %s", user.id)


async def run_scheduler() -> None:
    """Main scheduler loop — checks every minute. Also drives the L4 automation
    tick (Marketplace Execution Layer), gated by settings.automation_enabled."""
    logger.info("Scheduler started (reports + L4 automation tick)")
    while True:
        try:
            await _send_daily_reports()
            await _send_weekly_reports()
            await _automation_tick()
        except Exception:
            logger.exception("Scheduler iteration error")
        now = datetime.now()
        await asyncio.sleep(60 - now.second)


async def _automation_tick() -> None:
    """L4 automation. Uses the SAME executor path as manual L3. No-op unless
    AUTOMATION_ENABLED and a user has an enabled AutomationRule."""
    if not settings.automation_enabled:
        return
    from tasks.auto_publish_reviews import run_auto_publish_reviews
    try:
        await run_auto_publish_reviews()
    except Exception:
        logger.exception("auto_publish_reviews tick error")


async def send_critical_alert_to_user(user_id: str, message: str) -> bool:
    """Send a critical alert. Called from other routers."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user   = result.scalar_one_or_none()
    if not user or not user.telegram_chat_id:
        return False
    return await send_message(
        user.telegram_chat_id,
        f"🚨 <b>Критический алерт — Бизнес-Пульт</b>\n\n{message}\n\n"
        f"🔗 <a href='{_FRONTEND}/dashboard'>Открыть дашборд</a>",
    )
