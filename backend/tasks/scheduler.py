"""
Планировщик Telegram-отчётов.
Запускается как фоновая задача. Проверяет каждую минуту, отправляет по расписанию.
"""
import asyncio
import logging
import time
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


# ── Top action from the canonical Today service (One Morning Truth, A18) ──────
# Reads the SAME canonical source as the Dashboard feed (top_action over build_feed),
# NOT legacy _compute_insights — so Telegram and the web answer "что делать сегодня"
# identically. Honest empty state when nothing is live.

_NO_TOP_ACTION = "Сегодня критичных действий нет"


def _format_top_action(a) -> str:
    """One-line 'Главное сейчас' from a canonical TodayAction. Uses the doctrine
    recommended_action verbatim (+ product context) — never re-derives an action."""
    text = (a.recommended_action or a.title or "").strip()
    if not text:
        return "Проверить дашборд"
    ctx = (a.sku or "").strip()
    if ctx and ctx not in text:
        return f"{text} — «{ctx[:30]}»"
    return text


async def _get_top_action(user_id: str) -> str:
    """Telegram daily 'Главное сейчас' — canonical Today service only. Returns the
    honest empty-state line when there is nothing live (or on any read error)."""
    try:
        from services.decision_feed.today import top_action
        async with AsyncSessionLocal() as db:
            top = await top_action(db, user_id=user_id)
        if top is None:
            return _NO_TOP_ACTION
        return _format_top_action(top)
    except Exception:
        return _NO_TOP_ACTION


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


# ── observation retention (PULT-LAUNCH-2.5E-3) ──────────────────────────────────
# The retention sweep is NOT awaited inline (a full sweep can take minutes and would delay every other
# tick). Each hourly tick spawns ONE tracked asyncio.Task and returns immediately; the scheduler owns
# that task's whole lifecycle (single loop — no second scheduler). Feature OFF by default.
_RETENTION_INTERVAL_SECONDS = 60 * 60
_RETENTION_ALERT_AFTER = 3                       # one Sentry alert once N consecutive runs have failed
_last_retention_at: float | None = None          # set at run_scheduler START (not import) -> first run in 1h
_retention_task: asyncio.Task | None = None
_retention_consecutive_failures = 0

# ── SECURITY-2D-1C-D — recovery reconciliation + safe re-own wiring ──────────────────────────────────
# Both sweeps already exist, are OFF by default, take their own DISTINCT PostgreSQL advisory-lock op-code
# and NEVER touch a provider write. Here they are wired into the SINGLE scheduler with the same tracked-
# task pattern as observation retention: each minute tick only checks a gate + a monotonic cadence
# deadline and, if due, spawns ONE tracked task — the sweep is never awaited inline. Reconciliation and
# re-own have fully independent schedules and separate consecutive-failure counters. The scheduler contour
# only ever reaches the read-only classification / ownership-transfer sweeps; the real operator resume
# contour (C3C2) is unreachable from here — no import, no call.
_RECOVERY_ALERT_AFTER = 3                         # one Sentry alert once N consecutive runs have failed
# Cadence uses MONOTONIC DEADLINES (not a last-run + interval check) so the SHORT initial delay is honoured
# on the very first run instead of being swallowed by the full steady-state interval. Set at each
# run_scheduler start -> a process restart re-applies the full initial delay.
_reconcile_next_due_at: float | None = None
_reown_next_due_at: float | None = None
_reconcile_task: asyncio.Task | None = None
_reown_task: asyncio.Task | None = None
_reconcile_consecutive_failures = 0
_reown_consecutive_failures = 0


async def run_scheduler() -> None:
    """Main scheduler loop — checks every minute. Also drives the L4 automation
    tick (Marketplace Execution Layer), gated by settings.automation_enabled."""
    global _last_retention_at, _reconcile_next_due_at, _reown_next_due_at
    logger.info("Scheduler started (reports + L4 automation tick + measurement close)")
    _last_retention_at = time.monotonic()         # first observation-retention run waits a FULL hour
    # First recovery runs wait their (short) initial delay from THIS start; a restart re-applies it.
    _now0 = time.monotonic()
    _reconcile_next_due_at = _now0 + settings.recovery_reconcile_initial_delay_seconds
    _reown_next_due_at = _now0 + settings.recovery_reown_initial_delay_seconds
    try:
        while True:
            try:
                await _send_daily_reports()
                await _send_weekly_reports()
                await _review_ingest_tick()
                await _automation_tick()
                await _measurement_close_tick()
                await _advisory_runtime_tick()
                await _uploads_cleanup_tick()
                _observation_retention_tick()     # spawns/tracks a task; never awaits the sweep inline
                _recovery_reconcile_tick()        # spawns/tracks a task; never awaits the sweep inline
                _reown_tick()                     # spawns/tracks a task; never awaits the sweep inline
            except Exception:
                logger.exception("Scheduler iteration error")
            now = datetime.now()
            await asyncio.sleep(60 - now.second)
    finally:
        await _shutdown_retention()               # on cancel/shutdown: cancel + await the retention task
        await _shutdown_reconcile()               # cancel + await the reconciliation task
        await _shutdown_reown()                   # cancel + await the re-own task


def _observation_retention_tick() -> None:
    """Hourly, non-blocking. Feature OFF -> return (no task/lock/SQL). If a sweep is still running or the
    hour has not elapsed -> return (never a second task). Otherwise spawn ONE tracked task and return."""
    global _last_retention_at, _retention_task
    if not settings.observation_retention_enabled:
        return
    if _retention_task is not None and not _retention_task.done():
        return
    now = time.monotonic()
    if _last_retention_at is not None and now - _last_retention_at < _RETENTION_INTERVAL_SECONDS:
        return
    _last_retention_at = now
    _retention_task = asyncio.create_task(_retention_run())
    _retention_task.add_done_callback(_retention_done)


async def _retention_run():
    from services.marketplace.retention.observation_sweep import (
        DEFAULT_MAX_DURATION_SECONDS, run_observation_retention)
    # dry_run from the operator config; now=None -> production UTC; bounded to one hourly slot.
    return await run_observation_retention(
        dry_run=settings.observation_retention_dry_run, now=None,
        max_duration=DEFAULT_MAX_DURATION_SECONDS)


def _retention_done(task: "asyncio.Task") -> None:
    """Consume the finished task's result/exception (so it is never 'never retrieved'), log SAFE numbers,
    maintain the consecutive-failure counter, and clear the reference for the task that actually ended.
    A shutdown cancellation is NOT a failed run; a busy advisory lock / disabled feature do not touch the
    counter."""
    global _retention_task, _retention_consecutive_failures
    if task is _retention_task:
        _retention_task = None
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("observation retention: run error (details suppressed)")   # no exc/SQL/params/ids
        failed = True
    else:
        res = task.result()
        if not res.enabled or not res.lock_acquired:
            logger.info("observation retention: skipped (disabled or another run active)")
            return                                # neither success nor failure -> counter unchanged
        if res.dry_run:
            logger.info("observation retention dry-run: candidates %d price, %d promotion; duration %d ms",
                        res.price_candidates, res.promotion_candidates, res.duration_ms)
        else:
            logger.info("observation retention: removed %d price, %d promotion; batches %d; failed %d; "
                        "timed_out %s; duration %d ms", res.price_removed, res.promotion_removed,
                        res.batches, res.failed_batches, res.timed_out, res.duration_ms)
        failed = res.failed_batches > 0 or res.timed_out
    if failed:
        _retention_consecutive_failures += 1
        if _retention_consecutive_failures == _RETENTION_ALERT_AFTER:   # exactly at N -> ONE alert
            logger.error("observation retention: %d consecutive failed runs", _retention_consecutive_failures)
            _retention_sentry_alert(_retention_consecutive_failures)
    else:
        _retention_consecutive_failures = 0       # a fully successful run resets the streak


def _retention_sentry_alert(count: int) -> None:
    """One explicit Sentry event (the existing channel). No-op without a DSN / sentry-sdk; never carries
    account/store/product/SKU/external/promotion ids, exception objects, SQL, or SQL params."""
    try:
        import sentry_sdk
        sentry_sdk.capture_message(
            "observation retention: %d consecutive failed runs" % count, level="error")
    except Exception:
        pass                                      # missing sdk / no DSN -> scheduler keeps running


async def _shutdown_retention() -> None:
    """On scheduler/application shutdown: cancel a running retention task and await it so its current
    uncommitted batch rolls back (committed batches stay). Never leaves a background task alive."""
    task = _retention_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# ── SECURITY-2D-1C-D — recovery reconciliation tick (READ-ONLY classification) ───────────────────────

def _recovery_reconcile_tick() -> None:
    """Non-blocking. Feature OFF -> return (no task/lock/SQL/provider). Not yet due (monotonic deadline)
    -> return. A run still active -> return (never a second task). Otherwise set the next deadline from
    NOW + interval (so even a lock-skipped run waits a full interval — no tight loop) and spawn ONE tracked
    task. The reconciliation sweep only ever READs the provider and writes reconciliation_status + the three
    scheduling columns; it never dispatches."""
    global _reconcile_next_due_at, _reconcile_task
    if settings.recovery_reaper_enabled is not True:      # fail-closed master gate, before create_task
        return
    if _reconcile_next_due_at is None or time.monotonic() < _reconcile_next_due_at:
        return
    if _reconcile_task is not None and not _reconcile_task.done():
        return
    _reconcile_next_due_at = time.monotonic() + settings.recovery_reconcile_interval_seconds
    _reconcile_task = asyncio.create_task(_reconcile_run())
    _reconcile_task.add_done_callback(_reconcile_done)


async def _reconcile_run():
    from services.marketplace.recovery.recovery_sweep import (
        DEFAULT_MAX_DURATION_SECONDS, run_recovery_sweep)
    # dry_run from the operator config; now=None -> production UTC; bounded to one scheduler slot.
    return await run_recovery_sweep(
        dry_run=settings.recovery_reaper_dry_run, now=None,
        max_duration=DEFAULT_MAX_DURATION_SECONDS)


def _reconcile_done(task: "asyncio.Task") -> None:
    """Consume the finished task's result/exception (never 'never retrieved'), log SAFE numbers only, and
    maintain the reconciliation consecutive-failure counter (separate from re-own). A shutdown cancellation
    is NOT a failed run; a disabled feature / busy advisory lock leave the counter unchanged."""
    global _reconcile_task, _reconcile_consecutive_failures
    if task is _reconcile_task:
        _reconcile_task = None
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("recovery reconcile: run error (details suppressed)")   # no exc/SQL/params/ids
        failed = True
    else:
        res = task.result()
        if not res.enabled or not res.lock_acquired:
            logger.info("recovery reconcile: skipped (disabled or another run active)")
            return                                # neither success nor failure -> counter unchanged
        logger.info("recovery reconcile%s: candidates %d; reconciled %d; failed_users %d; timed_out %s; "
                    "duration %d ms", " dry-run" if res.dry_run else "", res.candidates, res.reconciled,
                    res.failed_users, res.timed_out, res.duration_ms)
        failed = res.failed_users > 0 or res.timed_out
    if failed:
        _reconcile_consecutive_failures += 1
        if _reconcile_consecutive_failures == _RECOVERY_ALERT_AFTER:   # exactly at N -> ONE alert
            logger.error("recovery reconcile: %d consecutive failed runs", _reconcile_consecutive_failures)
            _recovery_sentry_alert("recovery reconcile", _reconcile_consecutive_failures)
    else:
        _reconcile_consecutive_failures = 0       # a fully successful run resets the streak


async def _shutdown_reconcile() -> None:
    """On shutdown: cancel a running reconciliation task and await it so its current uncommitted batch rolls
    back (committed batches stay). Never leaves a background task alive."""
    task = _reconcile_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# ── SECURITY-2D-1C-D — safe re-own tick (ownership transfer only, stays pending) ─────────────────────

def _reown_tick() -> None:
    """Non-blocking. Feature OFF -> return (no task/lock/SQL/provider/executor). Not yet due (monotonic
    deadline) -> return. A run still active -> return (never a second task). Otherwise set the next deadline
    from NOW + interval and spawn ONE tracked task. The re-own sweep only bumps claim_generation/reown_count/
    last_reowned_at on a stuck SAFE pending claim; it leaves status='pending' and dispatch_started_at NULL,
    never dispatches and never calls the executor."""
    global _reown_next_due_at, _reown_task
    if settings.recovery_reown_enabled is not True:       # fail-closed master gate, before create_task
        return
    if _reown_next_due_at is None or time.monotonic() < _reown_next_due_at:
        return
    if _reown_task is not None and not _reown_task.done():
        return
    _reown_next_due_at = time.monotonic() + settings.recovery_reown_interval_seconds
    _reown_task = asyncio.create_task(_reown_run())
    _reown_task.add_done_callback(_reown_done)


async def _reown_run():
    from services.marketplace.recovery.reown_sweep import (
        DEFAULT_MAX_DURATION_SECONDS, run_reown_sweep)
    # dry_run from the operator config; now=None -> production UTC; bounded to one scheduler slot.
    return await run_reown_sweep(
        dry_run=settings.recovery_reown_dry_run, now=None,
        max_duration=DEFAULT_MAX_DURATION_SECONDS)


def _reown_done(task: "asyncio.Task") -> None:
    """Consume the finished task's result/exception (never 'never retrieved'), log SAFE numbers only, and
    maintain the re-own consecutive-failure counter (separate from reconciliation). A shutdown cancellation
    is NOT a failed run; a disabled feature / busy advisory lock leave the counter unchanged."""
    global _reown_task, _reown_consecutive_failures
    if task is _reown_task:
        _reown_task = None
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("recovery re-own: run error (details suppressed)")   # no exc/SQL/params/ids
        failed = True
    else:
        res = task.result()
        if not res.enabled or not res.lock_acquired:
            logger.info("recovery re-own: skipped (disabled or another run active)")
            return                                # neither success nor failure -> counter unchanged
        logger.info("recovery re-own%s: candidates %d; eligible %d; reowned %d; skipped_invalid %d; "
                    "skipped_race %d; failed_batches %d; timed_out %s; duration %d ms",
                    " dry-run" if res.dry_run else "", res.candidates, res.eligible, res.reowned,
                    res.skipped_invalid, res.skipped_race, res.failed_batches, res.timed_out,
                    res.duration_ms)
        failed = res.failed_batches > 0 or res.timed_out
    if failed:
        _reown_consecutive_failures += 1
        if _reown_consecutive_failures == _RECOVERY_ALERT_AFTER:   # exactly at N -> ONE alert
            logger.error("recovery re-own: %d consecutive failed runs", _reown_consecutive_failures)
            _recovery_sentry_alert("recovery re-own", _reown_consecutive_failures)
    else:
        _reown_consecutive_failures = 0           # a fully successful run resets the streak


async def _shutdown_reown() -> None:
    """On shutdown: cancel a running re-own task and await it so its current uncommitted batch rolls back
    (committed batches stay). Never leaves a background task alive."""
    task = _reown_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _recovery_sentry_alert(label: str, count: int) -> None:
    """One explicit Sentry event (the existing channel). No-op without a DSN / sentry-sdk; never carries
    account/store/product/SKU/external ids, operation keys, fingerprints, payloads, tokens, exception
    objects, SQL, or SQL params — only the safe label and the failure count."""
    try:
        import sentry_sdk
        sentry_sdk.capture_message("%s: %d consecutive failed runs" % (label, count), level="error")
    except Exception:
        pass                                      # missing sdk / no DSN -> scheduler keeps running


async def _uploads_cleanup_tick() -> None:
    """Delete uploaded CSVs that were never confirmed. Confirm cleans up after itself, but only
    for a seller who confirms something — a seller who previews a file and walks away leaves it
    behind for good. Touches no database and never raises into the scheduler."""
    from tasks.uploads_cleanup import run_uploads_cleanup
    try:
        # Called on every tick, but sweeps on its own 15-minute interval — the scheduler does not
        # need to know when, and no second scheduler exists to tell it.
        files, dirs = await run_uploads_cleanup()
        if files or dirs:
            # Counts only. A filename is a seller's upload and a directory name is their user id;
            # neither belongs in a log line, and the CSV contents certainly do not.
            logger.info("uploads cleanup: removed %d file(s), %d empty directory(ies)", files, dirs)
    except Exception:
        logger.exception("uploads_cleanup tick error")


async def _measurement_close_tick() -> None:
    """Decision Outcome auto-close. Reuses the existing close path, window-gated:
    closes only observations whose measurement window has elapsed. Read-only over
    the marketplace (no writes), idempotent; surfaces the proven effect in the
    outcome API + Daily Decision Feed. Never raises into the scheduler."""
    from tasks.measurement_close import run_measurement_close
    try:
        n = await run_measurement_close()
        if n:
            logger.info("measurement close: %d observation(s) closed", n)
    except Exception:
        logger.exception("measurement_close tick error")


async def _advisory_runtime_tick() -> None:
    """Advisory Runtime tick. SHADOW-SAFE: while every ProducerSpec is enabled=False
    (today) run_due_producers returns immediately, touching no user and creating no
    AdvisoryRun. Owns its own session; never raises into the scheduler."""
    from database import AsyncSessionLocal
    from services.advisory_runtime.runtime import AdvisoryRuntime
    try:
        async with AsyncSessionLocal() as db:
            res = await AdvisoryRuntime().run_due_producers(db)
        if res.ran or res.errors:
            logger.info("advisory runtime: ran=%d skipped=%d errors=%d",
                        res.ran, res.skipped, res.errors)
    except Exception:
        logger.exception("advisory_runtime tick error")


async def _review_ingest_tick() -> None:
    """AR-AUTO-FILL: auto-sync + auto-draft. Runs for enabled+consented review rules in BOTH modes
    and is deliberately NOT gated by the kill switch — a confirm-mode seller keeps receiving reviews
    and drafts even when automatic publishing is off. Only publishing (below) is kill-switch gated.
    Never raises into the scheduler."""
    from tasks.auto_review_pipeline import (
        run_auto_sync_reviews, run_auto_draft_reviews, run_reconcile_moderation,
    )
    try:
        await run_auto_sync_reviews()
        await run_auto_draft_reviews()
        # Replies a moderating marketplace accepted but had not yet shown must not sit in
        # "awaiting moderation" forever. Read-only: this sweep never publishes anything.
        await run_reconcile_moderation()
    except Exception:
        logger.exception("review ingest tick error")


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
