import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

ADMIN_CHAT_ID    = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
CHECK_INTERVAL   = 300  # seconds (5 min)
HEALTH_URL       = os.getenv("HEALTH_CHECK_URL", "http://localhost:8000/api/health")

_last_healthy = True   # optimistic start — avoid false alarm on first boot


async def _notify_admin(text: str) -> None:
    from services.telegram import send_message
    if ADMIN_CHAT_ID:
        await send_message(ADMIN_CHAT_ID, text)
    else:
        logger.warning("TELEGRAM_ADMIN_CHAT_ID not set — skipping admin alert")


async def check_once() -> bool:
    global _last_healthy
    healthy = False
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(HEALTH_URL)
            healthy = r.status_code == 200
    except Exception as exc:
        logger.warning("Health check request failed: %s", exc)
        healthy = False

    if not healthy and _last_healthy:
        logger.error("Health check FAILED — alerting admin")
        await _notify_admin(
            "🚨 <b>Бизнес-Пульт: сайт не отвечает!</b>\n\n"
            f"Health-check <code>{HEALTH_URL}</code> вернул ошибку.\n"
            "Проверьте сервер и перезапустите при необходимости."
        )
    elif healthy and not _last_healthy:
        logger.info("Health check OK — site recovered, alerting admin")
        await _notify_admin(
            "✅ <b>Бизнес-Пульт: сайт восстановлен</b>\n\n"
            "Health-check снова в норме."
        )

    _last_healthy = healthy
    return healthy


async def run_health_monitor() -> None:
    """Infinite loop: sleep CHECK_INTERVAL seconds, then check health."""
    logger.info("Health monitor started (interval=%ds, url=%s)", CHECK_INTERVAL, HEALTH_URL)
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            await check_once()
        except Exception:
            logger.exception("Unexpected error in health monitor")
