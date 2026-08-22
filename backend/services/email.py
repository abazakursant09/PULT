"""
Transactional email (P7.1) — verification + password-reset delivery.

The ONLY channel for auth tokens. Auth endpoints no longer return verification /
reset tokens in their HTTP responses; the link is delivered here instead.

Delivery: stdlib smtplib over a worker thread (non-blocking for the event loop).
If SMTP is not configured (`smtp_host` empty), the message is LOGGED instead of
sent — a development fallback so local/test flows work without a mail server. A
send failure never raises to the caller: registration/reset must still succeed
and the failure is logged for the operator.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from config import settings

log = logging.getLogger(__name__)

# LEGAL-PRELAUNCH-D (blocker #22) — mail logs must never carry the recipient address, the subject, the
# body, a live token, or any raw exception text. A send failure is logged as a single closed-vocabulary
# CATEGORY derived ONLY from the exception TYPE, so the operator still sees what broke without any PII /
# secret / attacker-controlled string landing in application logs.
#
# Frozen allowlist — the ONLY values the classifier may return. Ordered most-specific first; the classifier
# returns the category of the first type that matches (subclass before superclass). smtplib.SMTPException,
# ssl.SSLError and TimeoutError all subclass OSError, so the SMTP/TLS/timeout buckets MUST be tested before
# the generic OSError -> "network" bucket.
_SMTP_ERROR_CATEGORIES: frozenset[str] = frozenset(
    {
        "smtp_auth",
        "smtp_recipient_rejected",
        "smtp_connect",
        "smtp_disconnected",
        "smtp_tls",
        "smtp_timeout",
        "smtp_protocol",
        "network",
        "unknown",
    }
)

# (exception type, category) pairs, evaluated top-to-bottom. Types only — never the exception's value.
_SMTP_ERROR_RULES: tuple[tuple[type[BaseException], str], ...] = (
    (smtplib.SMTPAuthenticationError, "smtp_auth"),
    (smtplib.SMTPRecipientsRefused, "smtp_recipient_rejected"),
    (smtplib.SMTPConnectError, "smtp_connect"),
    (smtplib.SMTPServerDisconnected, "smtp_disconnected"),
    (ssl.SSLError, "smtp_tls"),
    (TimeoutError, "smtp_timeout"),
    (smtplib.SMTPException, "smtp_protocol"),
    (ConnectionError, "network"),
    (OSError, "network"),
)


def _smtp_error_category(exc: BaseException) -> str:
    """Map an exception to ONE frozen category, by TYPE only.

    Pure: no logging, no I/O, no network. Dispatches on the exception TYPE alone and never reads the
    exception's value, so nothing attacker- or PII-bearing can leak through the category. Unknown types
    collapse to "unknown"."""
    for exc_type, category in _SMTP_ERROR_RULES:
        if isinstance(exc, exc_type):
            return category
    return "unknown"


def _send_sync(to: str, subject: str, body: str) -> None:
    """Blocking SMTP send. Runs in a worker thread via send_email()."""
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user or "no-reply@pult.local"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


async def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email. Returns True if handed to SMTP, False if only logged/failed.
    Never raises — auth flows must complete regardless of mail-server state."""
    if not settings.smtp_host:
        # Development fallback: no mail server configured. Log ONLY that a mail would have gone out — never
        # the recipient, subject, body, or token (the body carries the reset/verification link with a live
        # raw token — LEGAL-PRELAUNCH-D / SECURITY-2C-3B). Tests obtain the token through the mailer seam
        # (they patch send_password_reset_email / send_verification_email), never from this log.
        log.warning("email_not_sent category=%s", "smtp_not_configured")
        return False
    try:
        await asyncio.to_thread(_send_sync, to, subject, body)
        # No recipient / subject / body — a bare success event only.
        log.info("email_sent")
        return True
    except Exception as exc:  # pragma: no cover - operator-visible, non-fatal
        # Closed-vocabulary category by exception TYPE only — no recipient/subject/body/token, and no raw
        # exception value or stack of any kind.
        log.error("email_send_failed category=%s", _smtp_error_category(exc))
        return False


async def send_verification_email(to: str, name: str | None, token: str) -> bool:
    link = f"{settings.frontend_url.rstrip('/')}/verify-email?token={token}"
    body = (
        f"Здравствуйте{', ' + name if name else ''}!\n\n"
        f"Подтвердите email, чтобы войти в Бизнес-Пульт:\n{link}\n\n"
        f"Если вы не регистрировались — просто игнорируйте это письмо."
    )
    return await send_email(to, "Подтверждение email · Бизнес-Пульт", body)


async def send_password_reset_email(to: str, name: str | None, token: str) -> bool:
    # SECURITY-2C-3B — the token rides in the URL FRAGMENT (#), which the browser never sends to any
    # server: it stays out of reverse-proxy access logs and Referer headers. The reset page reads it
    # in JS and strips it from the address bar immediately.
    link = f"{settings.frontend_url.rstrip('/')}/reset-password#token={token}"
    body = (
        f"Здравствуйте{', ' + name if name else ''}!\n\n"
        f"Вы запросили сброс пароля в Бизнес-Пульт. Перейдите по ссылке "
        f"(действует 24 часа):\n{link}\n\n"
        f"Если вы этого не делали — просто игнорируйте это письмо, пароль не изменится."
    )
    return await send_email(to, "Сброс пароля · Бизнес-Пульт", body)
