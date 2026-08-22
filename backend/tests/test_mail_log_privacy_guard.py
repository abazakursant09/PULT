"""LEGAL-PRELAUNCH-D (blocker #22) — offline source guard for the transactional mailer's logging.

Text-only (no import of the module under network conditions, no smtp, no socket, no network). Proves
the mail logging layer cannot regress into leaking PII / secrets / raw exception text:

  * every ``log.*`` statement in services/email.py is free of the recipient, subject, body, token, and
    of ``str(exc)`` / ``repr(exc)`` / ``exc.args`` / ``exc_info=`` / ``logger.exception``;
  * a frozen closed-vocabulary allowlist exists and the classifier is a pure type-only function;
  * the failure log emits ONLY the classifier category, never the exception value.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
EMAIL = BACKEND / "services" / "email.py"
LEGAL = REPO / "docs" / "legal"
CHECKLIST = LEGAL / "launch-legal-checklist.md"


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# A logical `log.<level>( ... )` / `logger.<level>( ... )` statement, arguments possibly spanning lines.
_LOG_STMT_RE = re.compile(r"\b(?:log|logger|lg)\s*\.\s*\w+\s*\((?:[^()]|\([^()]*\))*\)", re.S)

# Tokens that must NEVER appear inside a mail log statement.
_FORBIDDEN_IN_LOG = (
    "to=%s",
    "subject=%s",
    "err=%s",
    "str(exc)",
    "repr(exc)",
    "exc.args",
    "exc_info",
    "%(to)s",
    "%(subject)s",
)


def _log_statements(src: str) -> list[str]:
    return _LOG_STMT_RE.findall(src)


def test_email_has_log_statements_to_check():
    stmts = _log_statements(_r(EMAIL))
    assert stmts, "no log statements parsed from services/email.py"


def test_no_recipient_subject_body_or_raw_exception_in_logs():
    for stmt in _log_statements(_r(EMAIL)):
        low = stmt
        for bad in _FORBIDDEN_IN_LOG:
            assert bad not in low, f"forbidden token {bad!r} in mail log statement: {stmt!r}"
        # No bare recipient / subject / body / token identifiers passed to a logger. (Event names use an
        # `email_` prefix, so `\bemail\b` never matches them; a standalone `email` var would still fail.)
        for ident in ("to", "subject", "body", "token", "recipient", "email"):
            assert not re.search(rf"\b{ident}\b", stmt), (
                f"identifier {ident!r} must not be logged by the mailer: {stmt!r}"
            )


def test_no_logger_exception_or_exc_info_anywhere():
    src = _r(EMAIL)
    assert ".exception(" not in src, "the mailer must not use logger.exception (leaks traceback)"
    assert "exc_info" not in src, "the mailer must not pass exc_info (leaks traceback)"
    assert "traceback" not in src.lower(), "the mailer must not log a traceback"


def test_frozen_allowlist_present_and_closed():
    src = _r(EMAIL)
    assert "_SMTP_ERROR_CATEGORIES" in src, "a frozen category allowlist must exist"
    assert "frozenset" in src, "the category allowlist must be a frozenset (closed vocabulary)"
    # Core categories that the closed vocabulary must define.
    for cat in ("smtp_auth", "smtp_connect", "smtp_timeout", "network", "unknown"):
        assert f'"{cat}"' in src, f"closed vocabulary must define category {cat!r}"


def test_classifier_is_pure_type_only():
    src = _r(EMAIL)
    m = re.search(r"def _smtp_error_category\(.*?\n(?=\S|def )", src, re.S)
    # Fallback: capture until the next top-level def if the above lookahead misses.
    if not m:
        m = re.search(r"def _smtp_error_category\(.*?(?=\ndef |\nasync def )", src, re.S)
    assert m, "classifier _smtp_error_category not found"
    body = m.group(0)
    assert "isinstance(" in body, "classifier must dispatch on exception TYPE (isinstance)"
    for bad in ("str(exc)", "repr(exc)", "exc.args", ".response", ".smtp_error", "log.", "logger."):
        assert bad not in body, f"classifier must be pure/type-only — found {bad!r}"


def test_failure_log_uses_classifier_category_only():
    src = _r(EMAIL)
    fail = next((s for s in _log_statements(src) if "email_send_failed" in s), "")
    assert fail, "failure log statement 'email_send_failed' not found"
    assert "_smtp_error_category(exc)" in fail, "failure log must use the classifier category"
    assert "category=%s" in fail, "failure log must format only a category field"


def test_success_and_notconfigured_logs_are_bare():
    src = _r(EMAIL)
    ok = next((s for s in _log_statements(src) if "email_sent" in s), "")
    nc = next((s for s in _log_statements(src) if "email_not_sent" in s), "")
    assert ok, "success log 'email_sent' not found"
    assert nc, "not-configured log 'email_not_sent' not found"
    # success carries no formatting args at all
    assert "%s" not in ok, "success log must not format any value"
    # not-configured carries only a constant category, never the recipient/subject
    assert "smtp_not_configured" in nc
    for bad in ("to", "subject", "body", "token"):
        assert not re.search(rf"\b{bad}\b", nc), f"not-configured log must not carry {bad!r}"


def test_real_send_path_unchanged_contract():
    """The mailer keeps its send seam + non-raising bool contract (logging change only)."""
    src = _r(EMAIL)
    assert "async def send_email(to: str, subject: str, body: str) -> bool:" in src
    assert "def _send_sync(to: str, subject: str, body: str) -> None:" in src
    assert "await asyncio.to_thread(_send_sync, to, subject, body)" in src
    assert "smtp.send_message(msg)" in src, "real SMTP send must be unchanged"


# --- docs honesty: #22 stays PARTIAL, #25 exists/open, DRAFT + launch gate intact -----------------
_BAD_STATUS = ("DONE", "PASS", "READY", "VERIFIED", "CLOSED")


def _table_row(name: str, num: int) -> list[str] | None:
    for line in _r(LEGAL / name).splitlines():
        if line.strip().startswith(f"| {num} |"):
            return [c.strip() for c in line.strip().strip("|").split("|")]
    return None


def test_blocker_22_partial_not_done():
    row = _table_row("launch-legal-checklist.md", 22)
    assert row is not None and len(row) >= 4, "blocker #22 row missing/malformed"
    joined = " ".join(row).lower()
    assert "log" in joined or "лог" in joined, "#22 must be the mail/application-log blocker"
    status = row[3].upper()
    for bad in _BAD_STATUS:
        assert bad not in status, f"#22 must not be marked {bad} (retention/access still open via #25)"
    assert "PARTIAL" in status, "#22 technical fix is IMPLEMENTED but overall status stays PARTIAL"


def test_blocker_25_retention_present_and_open():
    row = _table_row("launch-legal-checklist.md", 25)
    assert row is not None and len(row) >= 4, "operational blocker #25 (log retention/access) must exist"
    joined = " ".join(row).lower()
    assert "retention" in joined or "хранени" in joined or "лог" in joined, (
        "#25 must be the log retention/access operational blocker"
    )
    status = row[3].upper()
    for bad in _BAD_STATUS:
        assert bad not in status, f"#25 must stay open — not {bad}"
    assert ("OPEN" in status) or ("BLOCKED" in status), "#25 status must be OPEN/BLOCKED"


def test_draft_and_launch_gate_intact():
    assert "NOT READY" in _r(CHECKLIST), "launch gate must stay NOT READY"
    assert "НЕ ПУБЛИКОВАТЬ" in _r(LEGAL / "cookie-notice.DRAFT.md"), "DRAFT gate must remain"
