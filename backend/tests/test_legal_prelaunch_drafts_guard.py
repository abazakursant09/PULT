"""LEGAL-PRELAUNCH-B — offline guard for the DORMANT docs/legal draft package (Пульт OS).

Proves the legal package is a non-publishable draft and switches NOTHING in the runtime:
  * every public document carries the "НЕ ПУБЛИКОВАТЬ" gate;
  * no real operator requisites (ИНН/ОГРНИП/bank account) or secrets are embedded;
  * every future pult-os.ru mail contact is marked NOT ACTIVE;
  * the old biznes-pult.ru contact is not reintroduced into any *publishable* document;
  * no document declares LAUNCH/PRODUCTION READY;
  * the runtime is untouched (config app-origin stays localhost, no pult-os.ru hardcoded,
    the live privacy page still carries the OLD domain).

No network, no DNS, no shell. Pure file reads.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
LEGAL = REPO / "docs" / "legal"
CONFIG = BACKEND / "config.py"

# Files intended for eventual public/user-facing use — must carry the strong gate.
PUBLIC_DOCS = (
    "privacy-policy.DRAFT.md",
    "personal-data-consent.DRAFT.md",
    "user-agreement.DRAFT.md",
    "public-offer.DRAFT.md",
    "cookie-notice.DRAFT.md",
)
# Internal working docs — draft/internal, may cite the old domain as factual evidence.
INTERNAL_DOCS = (
    "README.md",
    "personal-data-register.md",
    "source-evidence.md",
    "launch-legal-checklist.md",
)
ALL_DOCS = PUBLIC_DOCS + INTERNAL_DOCS

MAIL_CONTACTS = ("support@pult-os.ru", "privacy@pult-os.ru", "security@pult-os.ru")


def _r(name: str) -> str:
    return (LEGAL / name).read_text(encoding="utf-8")


def test_all_docs_present():
    for name in ALL_DOCS:
        assert (LEGAL / name).is_file(), f"missing legal doc {name}"


def test_public_docs_carry_publish_gate():
    for name in PUBLIC_DOCS:
        assert "НЕ ПУБЛИКОВАТЬ" in _r(name), f"{name} missing publish gate"


def test_no_real_requisites_or_secrets():
    # No 11+ digit run (covers 12-digit ИНН, 15-digit ОГРНИП, 20-digit account),
    # no card-like 16-digit group, no AWS key, no private key block.
    for name in ALL_DOCS:
        body = _r(name)
        assert not re.search(r"\d{11,}", body), f"long digit run (possible requisite) in {name}"
        assert not re.search(r"AKIA[0-9A-Z]{16}", body), f"AWS key in {name}"
        assert "-----BEGIN" not in body, f"private key block in {name}"


def test_mail_contacts_marked_not_active():
    # Any doc that names a pult-os.ru mail contact must also flag NOT ACTIVE.
    for name in ALL_DOCS:
        body = _r(name)
        if any(c in body for c in MAIL_CONTACTS):
            assert "NOT ACTIVE" in body, f"{name} names a mail contact without NOT ACTIVE"


def test_old_domain_not_reintroduced_in_public_docs():
    # Publishable docs must not leak the old biznes-pult.ru brand/contact.
    for name in PUBLIC_DOCS:
        assert "biznes-pult" not in _r(name), f"old domain leaked into public doc {name}"


def test_no_launch_ready_claim():
    for name in ALL_DOCS:
        body = _r(name)
        # exact phrases only — must not match the legitimate "NOT READY".
        assert "LAUNCH READY" not in body, f"LAUNCH READY claim in {name}"
        assert "PRODUCTION READY" not in body, f"PRODUCTION READY claim in {name}"
        assert "READY FOR PRODUCTION" not in body, f"READY FOR PRODUCTION claim in {name}"


def test_brand_is_pult_os():
    assert "Пульт OS" in _r("README.md"), "README must use the Пульт OS brand"


def test_runtime_untouched_config_still_localhost():
    cfg = CONFIG.read_text(encoding="utf-8")
    assert 'frontend_url: str = "http://localhost:3000"' in cfg, "app origin must stay localhost"
    assert "pult-os.ru" not in cfg, "no production domain hardcoded in config"


def test_live_privacy_page_still_old_domain():
    # dormancy proof: the legal package does NOT flip the live pages.
    priv = REPO / "frontend" / "app" / "privacy" / "page.tsx"
    if priv.is_file():
        assert "biznes-pult.ru" in priv.read_text(encoding="utf-8"), (
            "live pages must remain unchanged by the docs-only legal package"
        )


# --- CORRECTION-1: no false global "no email in logs" claim ---

def test_no_global_no_email_in_logs_claim():
    # The SMTP mailer (services/email.py) DOES log recipient email + subject. The docs must not
    # reintroduce the false global invariant that removed docs once carried.
    forbidden = ("без записи email/IP/токенов", "логи пишут только")
    for name in ALL_DOCS:
        body = _r(name)
        for phrase in forbidden:
            assert phrase not in body, f"false global no-email-in-logs claim '{phrase}' in {name}"


def test_smtp_log_gap_is_documented():
    # The honest SMTP-log fact must be present where logging is discussed.
    for name in ("privacy-policy.DRAFT.md", "personal-data-register.md", "source-evidence.md"):
        assert "email.py" in _r(name), f"{name} must cite services/email.py logging fact"
    for name in ("privacy-policy.DRAFT.md", "personal-data-register.md"):
        assert "адрес получателя" in _r(name), f"{name} must acknowledge mailer logs recipient"


# --- CORRECTION-2: localization is a future obligation, not a present fact ---

def test_localization_not_stated_as_present_fact():
    priv = _r("privacy-policy.DRAFT.md")
    assert "осуществляются в базах данных на территории Российской Федерации" not in priv, (
        "localization must not be stated as an already-operating fact"
    )
    assert "до начала production-обработки" in priv, "localization must be a future fail-closed obligation"


# --- CORRECTION-1/4: SMTP/application-log blocker present in checklist ---

def test_checklist_has_smtp_log_blocker():
    chk = _r("launch-legal-checklist.md")
    assert "application-log" in chk, "checklist must carry the SMTP/application-log blocker"
    assert "services/email.py" in chk, "checklist SMTP blocker must cite the evidence"


# --- CORRECTION-4: minimal blocker chain must include #12/#13/#14 (and the new SMTP-log one) ---

def test_minimal_chain_includes_late_blockers():
    chk = _r("launch-legal-checklist.md")
    head = "Минимальные блокеры до первой оплаты"
    assert head in chk, "minimal-blocker section heading missing"
    tail = chk.split(head, 1)[1]
    for tok in ("#12", "#13", "#14", "#22"):
        assert tok in tail, f"minimal-blocker chain must include {tok}"
    assert "NOT READY" in chk, "checklist must keep launch gate NOT READY"
