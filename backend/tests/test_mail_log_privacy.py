"""LEGAL-PRELAUNCH-D (blocker #22) — runtime proof that the transactional mailer never logs the
recipient address, subject, body, live token, or any raw exception text.

No real SMTP: `services.email._send_sync` is patched, and an autouse guard makes any real SMTP client
construction fail the test. All log capture is via `caplog`. Hostile synthetic values are pushed
through every path and asserted absent from the captured logs.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl

import pytest

import services.email as email
from services.email import _smtp_error_category, _SMTP_ERROR_CATEGORIES, send_email

# Hostile, unique markers. If any of these ever reaches a log record, the mailer leaked it.
H_TO = "secret.person+unique@example.test"
H_SUBJECT = "RESET TOKEN ORDER-SECRET"
H_BODY = "CANARY-BODY-TOKEN-SECRET"
H_EXC = "CANARY-EXCEPTION-SECRET"

_ALL_HOSTILE = (H_TO, H_SUBJECT, H_BODY, H_EXC, "secret", "SECRET", "CANARY")


@pytest.fixture(autouse=True)
def _ban_smtp(monkeypatch):
    """Any attempt to open a real SMTP connection is a bug — fail loudly. (We ban the SMTP client
    rather than sockets outright, because asyncio's event loop legitimately uses sockets internally.)"""
    def _boom(*a, **k):  # pragma: no cover - only fires on a real SMTP attempt
        raise AssertionError("real SMTP connection is banned in mail-log tests")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _boom, raising=False)


def _run(coro):
    return asyncio.run(coro)


def _assert_clean(caplog):
    blob = caplog.text
    for token in _ALL_HOSTILE:
        assert token not in blob, f"hostile marker leaked into logs: {token!r}"


# --- send_email: smtp not configured -------------------------------------------------------------
def test_not_configured_logs_no_pii(monkeypatch, caplog):
    monkeypatch.setattr(email.settings, "smtp_host", "", raising=False)
    with caplog.at_level(logging.DEBUG, logger="services.email"):
        result = _run(send_email(H_TO, H_SUBJECT, H_BODY))
    assert result is False  # return contract unchanged
    _assert_clean(caplog)
    assert "email_not_sent" in caplog.text
    assert "smtp_not_configured" in caplog.text


# --- send_email: success -------------------------------------------------------------------------
def test_success_logs_no_recipient_subject_body(monkeypatch, caplog):
    monkeypatch.setattr(email.settings, "smtp_host", "smtp.example.test", raising=False)

    def _ok(to, subject, body):  # patched send: no network
        return None

    monkeypatch.setattr(email, "_send_sync", _ok)
    with caplog.at_level(logging.DEBUG, logger="services.email"):
        result = _run(send_email(H_TO, H_SUBJECT, H_BODY))
    assert result is True  # return contract unchanged
    _assert_clean(caplog)
    assert "email_sent" in caplog.text


# --- send_email: failure -> category, never raw exception ----------------------------------------
@pytest.mark.parametrize(
    "exc, expected",
    [
        (smtplib.SMTPAuthenticationError(535, H_EXC), "smtp_auth"),
        (smtplib.SMTPRecipientsRefused({H_TO: (550, H_EXC.encode())}), "smtp_recipient_rejected"),
        (smtplib.SMTPConnectError(421, H_EXC), "smtp_connect"),
        (smtplib.SMTPServerDisconnected(H_EXC), "smtp_disconnected"),
        (ssl.SSLError(H_EXC), "smtp_tls"),
        (TimeoutError(H_EXC), "smtp_timeout"),
        (smtplib.SMTPDataError(554, H_EXC), "smtp_protocol"),
        (smtplib.SMTPException(H_EXC), "smtp_protocol"),
        (ConnectionResetError(H_EXC), "network"),
        (OSError(H_EXC), "network"),
        (ValueError(H_EXC), "unknown"),
    ],
)
def test_failure_logs_category_not_raw_exception(monkeypatch, caplog, exc, expected):
    monkeypatch.setattr(email.settings, "smtp_host", "smtp.example.test", raising=False)

    def _raise(to, subject, body):
        raise exc

    monkeypatch.setattr(email, "_send_sync", _raise)
    with caplog.at_level(logging.DEBUG, logger="services.email"):
        result = _run(send_email(H_TO, H_SUBJECT, H_BODY))
    assert result is False  # never raises, contract unchanged
    _assert_clean(caplog)
    assert "email_send_failed" in caplog.text
    assert f"category={expected}" in caplog.text


# --- classifier: pure, type-only, frozen allowlist -----------------------------------------------
def test_classifier_returns_only_allowlisted_values():
    samples = [
        smtplib.SMTPAuthenticationError(535, H_EXC),
        smtplib.SMTPRecipientsRefused({H_TO: (550, b"x")}),
        smtplib.SMTPConnectError(421, H_EXC),
        smtplib.SMTPServerDisconnected(H_EXC),
        ssl.SSLError(H_EXC),
        TimeoutError(H_EXC),
        smtplib.SMTPException(H_EXC),
        ConnectionError(H_EXC),
        OSError(H_EXC),
        ValueError(H_EXC),
        RuntimeError(H_EXC),
    ]
    for exc in samples:
        cat = _smtp_error_category(exc)
        assert cat in _SMTP_ERROR_CATEGORIES, f"category {cat!r} not in frozen allowlist"


def test_classifier_unknown_for_unmapped_type():
    assert _smtp_error_category(ValueError(H_EXC)) == "unknown"
    assert _smtp_error_category(KeyError(H_EXC)) == "unknown"


def test_classifier_does_not_leak_message():
    # The returned category must not embed the (hostile) exception text.
    cat = _smtp_error_category(smtplib.SMTPException(H_EXC))
    assert H_EXC not in cat


def test_classifier_subclass_before_superclass():
    # SMTPAuthenticationError subclasses SMTPException; must resolve to the specific bucket.
    assert _smtp_error_category(smtplib.SMTPAuthenticationError(535, H_EXC)) == "smtp_auth"
    # A plain SMTPException falls through to the generic protocol bucket, not "network"/"unknown".
    assert _smtp_error_category(smtplib.SMTPException(H_EXC)) == "smtp_protocol"
