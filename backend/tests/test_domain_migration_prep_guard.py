"""SECURITY/LEGAL DOMAIN-PREP — offline guard for the DORMANT pult-os.ru migration preparation.

Proves the prep is dormant: docs exist, carry the two fail-closed gates, the DNS runbook is NOT applied
and forbids a wildcard, future emails are marked NOT ACTIVE, and NOTHING in the runtime was switched
(backend app-origin stays env-driven with a localhost default; no real DNS record / secret in the docs).
No network, no DNS, no shell.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
MIG = REPO / "docs" / "domain-migration-pult-os.md"
DNS = REPO / "docs" / "dns-runbook-pult-os.md"
CONFIG = BACKEND / "config.py"


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_docs_present():
    assert MIG.is_file() and DNS.is_file()


def test_fail_closed_gates_present():
    m = _r(MIG)
    assert "DOMAIN VERIFIED: pult-os.ru" in m, "domain gate phrase required"
    assert "NOT ACTIVE" in m, "future emails must be marked NOT ACTIVE"
    assert "DORMANT" in m and "changes NONE of A" in m, "must state nothing is switched"
    for c in ("support@pult-os.ru", "privacy@pult-os.ru", "security@pult-os.ru"):
        assert c in m, f"future contact {c} must be documented"


def test_dns_runbook_not_applied_and_no_wildcard():
    d = _r(DNS)
    assert "NOT APPLIED" in d, "runbook must declare it is not applied"
    assert "No wildcard" in d or "no wildcard" in d.lower(), "must forbid a wildcard record"
    for rec in ("apex", "www", "TLS", "MX", "SPF", "DKIM", "DMARC", "Rollback", "Fail-closed"):
        assert rec in d, f"runbook must cover {rec}"


def test_no_real_dns_values_or_secrets_in_docs():
    for p in (MIG, DNS):
        body = _r(p)
        assert not re.search(r"AKIA[0-9A-Z]{16}", body), f"AWS key in {p.name}"
        assert "-----BEGIN" not in body, f"private key in {p.name}"
        # no concrete public IPv4 wired in (placeholders only)
        assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", body), f"concrete IP in {p.name}"


def test_runtime_app_origin_still_dormant_localhost_default():
    # migration must NOT flip the runtime: default frontend_url stays localhost (prod is env-only).
    cfg = _r(CONFIG)
    assert 'frontend_url: str = "http://localhost:3000"' in cfg, "default app origin must stay localhost"
    assert "pult-os.ru" not in cfg, "no production domain hardcoded in config"


def test_live_pages_not_switched_yet():
    # dormancy proof: the live legal/brand pages still carry the OLD contact until gate 1+gate 2.
    priv = REPO / "frontend" / "app" / "privacy" / "page.tsx"
    if priv.is_file():
        assert "biznes-pult.ru" in _r(priv), "live pages must remain unchanged in the dormant prep PR"
