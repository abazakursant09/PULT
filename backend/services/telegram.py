from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

_TG_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def _post(chat_id: str, payload: dict) -> bool:
    if not BOT_TOKEN or not chat_id:
        logger.warning("Telegram send skipped: BOT_TOKEN=%s chat_id=%s", bool(BOT_TOKEN), bool(chat_id))
        return False
    url = _TG_URL.format(token=BOT_TOKEN)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"chat_id": chat_id, **payload})
            if r.status_code != 200:
                logger.warning("Telegram API %s: %s", r.status_code, r.text[:300])
            return r.status_code == 200
    except Exception as exc:
        logger.exception("Telegram send failed: %s", exc)
        return False


async def send_message(chat_id: str, text: str) -> bool:
    return await _post(chat_id, {
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


async def send_message_with_keyboard(
    chat_id: str,
    text: str,
    inline_keyboard: Optional[list[list[dict]]] = None,
) -> bool:
    """Send a Telegram message with optional URL inline keyboard buttons."""
    payload: dict = {
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
    return await _post(chat_id, payload)
