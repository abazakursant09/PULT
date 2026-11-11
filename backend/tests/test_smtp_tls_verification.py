"""Network-free TLS regressions against the real transactional mail functions."""

import asyncio
import smtplib
import ssl
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from services import email as mail


@pytest.fixture
def smtp(monkeypatch):
    settings = SimpleNamespace(
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_user="sender@example.test",
        smtp_password="synthetic-password-only",
        smtp_from="sender@example.test",
        smtp_starttls=True,
    )
    state = SimpleNamespace(
        settings=settings, events=[], tls_args=None, tls_kwargs=None,
        tls_error=None, credentials=None, messages=[], log=Mock(),
    )

    class FakeSMTP:
        def __init__(self, host, port, *, timeout):
            assert (host, port, timeout) == (settings.smtp_host, settings.smtp_port, 15)
            state.events.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            state.events.append("close")
            return False

        def starttls(self, *args, **kwargs):
            state.events.append("tls")
            state.tls_args, state.tls_kwargs = args, kwargs
            if state.tls_error is not None:
                raise state.tls_error
            return 220, b"ready"

        def login(self, user, password):
            state.events.append("auth")
            state.credentials = (user, password)
            return 235, b"authenticated"

        def send_message(self, message):
            state.events.append("send")
            state.messages.append(message)
            return {}

    def forbidden_ssl_fallback(*args, **kwargs):
        pytest.fail("Unexpected SMTP_SSL fallback; real network is forbidden")

    monkeypatch.setattr(mail, "settings", settings)
    monkeypatch.setattr(mail, "log", state.log)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", forbidden_ssl_fallback)
    return state


def test_verified_context_is_keyword_only_and_tls_precedes_auth_and_send(smtp):
    mail._send_sync("recipient@example.test", "Synthetic subject", "Synthetic body")

    assert smtp.tls_args == ()
    assert set(smtp.tls_kwargs) == {"context"}
    context = smtp.tls_kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert smtp.events == ["connect", "tls", "auth", "send", "close"]
    assert smtp.credentials == (smtp.settings.smtp_user, smtp.settings.smtp_password)
    assert len(smtp.messages) == 1
    message = smtp.messages[0]
    assert str(message["From"]) == smtp.settings.smtp_from
    assert str(message["To"]) == "recipient@example.test"
    assert str(message["Subject"]) == "Synthetic subject"
    assert message.get_content() == "Synthetic body\n"


def test_certificate_failure_never_authenticates_or_sends_and_logs_safe_category(smtp):
    recipient = "private-recipient@example.test"
    subject, body = "SYNTHETIC-PRIVATE-SUBJECT", "SYNTHETIC-PRIVATE-TOKEN"
    smtp.tls_error = ssl.SSLCertVerificationError(
        " ".join((recipient, subject, body, smtp.settings.smtp_password))
    )

    assert asyncio.run(mail.send_email(recipient, subject, body)) is False

    assert smtp.events == ["connect", "tls", "close"]
    assert smtp.credentials is None
    assert smtp.messages == []
    assert smtp.log.method_calls == [
        call.error("email_send_failed category=%s", "smtp_tls")
    ]


def test_unsupported_starttls_never_authenticates_sends_or_falls_back(smtp):
    smtp.tls_error = smtplib.SMTPNotSupportedError("synthetic STARTTLS unavailable")

    assert asyncio.run(mail.send_email("recipient@example.test", "Subject", "Body")) is False

    assert smtp.events == ["connect", "tls", "close"]
    assert smtp.credentials is None
    assert smtp.messages == []
    assert smtp.log.method_calls == [
        call.error("email_send_failed category=%s", "smtp_protocol")
    ]


def test_explicitly_disabled_tls_preserves_development_send(smtp):
    smtp.settings.smtp_starttls = False
    smtp.tls_error = AssertionError("STARTTLS must not run when explicitly disabled")

    assert asyncio.run(mail.send_email("recipient@example.test", "Subject", "Body")) is True

    assert smtp.tls_args is None and smtp.tls_kwargs is None
    assert smtp.events == ["connect", "auth", "send", "close"]
    assert smtp.credentials == (smtp.settings.smtp_user, smtp.settings.smtp_password)
    assert len(smtp.messages) == 1
    assert str(smtp.messages[0]["To"]) == "recipient@example.test"
    assert smtp.log.method_calls == [call.info("email_sent")]
