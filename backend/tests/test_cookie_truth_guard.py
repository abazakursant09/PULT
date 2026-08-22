"""LEGAL-PRELAUNCH-C2 — offline guard: cookie claims must match runtime (blocker #13).

Text-only (no node, no network, no browser). Proves:
  * the CookieBanner sets NO cookie and names no fictional bp_session / bp_analytics, and makes no
    active-analytics claim;
  * the frontend never references the real session-cookie names (they are HttpOnly, backend-only);
  * no behavioural-analytics client is present in the frontend;
  * the backend session cookie keeps its names + HttpOnly / Secure / SameSite contract;
  * the cookie notice makes no active-analytics claim (outside the historical "fixed" note) and
    lists only localStorage keys that actually exist in the frontend source;
  * blocker #13 is not marked DONE, DRAFT markers stay, and the live domain/runtime is unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FRONTEND = REPO / "frontend"
BANNER = FRONTEND / "components" / "CookieBanner.tsx"
AUTHCK = BACKEND / "auth_cookie.py"
CONFIG = BACKEND / "config.py"
LEGAL = REPO / "docs" / "legal"
FE_DIRS = ["app", "components", "lib", "hooks"]


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _fe_sources() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for d in FE_DIRS:
        base = FRONTEND / d
        if not base.is_dir():
            continue
        for p in base.rglob("*.ts*"):
            if p.suffix not in (".ts", ".tsx"):
                continue
            if p.name.endswith((".test.ts", ".test.tsx")):
                continue
            if "node_modules" in p.parts or ".next" in p.parts:
                continue
            out.append((p.as_posix(), _r(p)))
    return out


def test_banner_sets_no_cookie_and_no_fake_names():
    b = _r(BANNER)
    assert "bp_session" not in b, "fictional bp_session must not appear in the banner"
    assert "bp_analytics" not in b, "fictional bp_analytics must not appear in the banner"
    assert "document.cookie" not in b, "the banner must not set or read any cookie via document.cookie"
    assert "аналитические cookie помога" not in b.lower(), "banner must not claim analytics cookies are active"


def test_frontend_never_names_session_cookie():
    for rel, txt in _fe_sources():
        assert "__Host-pult_session" not in txt, f"session cookie name leaked into frontend: {rel}"
        assert "pult_session_dev" not in txt, f"dev session cookie name leaked into frontend: {rel}"


def test_no_behavioural_analytics_client_in_frontend():
    bad = re.compile(r"\b(gtag|dataLayer|posthog|mixpanel|getVisitorId|trackEvent)\b|/api/events")
    for rel, txt in _fe_sources():
        assert not bad.search(txt), f"behavioural-analytics client/reference in {rel}"


def test_backend_session_cookie_contract_intact():
    a = _r(AUTHCK)
    assert '_NAME_PROD = "__Host-pult_session"' in a, "prod session cookie name must be unchanged"
    assert '_NAME_DEV = "pult_session_dev"' in a, "dev session cookie name must be unchanged"
    assert '_SAMESITE = "lax"' in a, "SameSite must stay lax"
    assert "response.set_cookie(" in a, "session must be delivered as a Set-Cookie, not localStorage"
    assert a.count("httponly=True") >= 2, "HttpOnly must stay on both set and clear"
    assert "secure=is_secure()" in a, "Secure(prod) contract must be intact"
    assert "samesite=_SAMESITE" in a, "SameSite attribute must be intact"


def test_cookie_notice_no_active_analytics_claim():
    n = _r(LEGAL / "cookie-notice.DRAFT.md")
    # Drop the historical "Исправлено" paragraph, which quotes the OLD false claim on purpose.
    lines = n.splitlines()
    kept = [ln for ln in lines if not ln.strip().startswith("**Исправлено")]
    body = "\n".join(kept).lower()
    for claim in ("аналитические cookie помогают улучшать продукт", "используем аналитические cookie"):
        assert claim not in body, f"cookie notice must not claim analytics is active: {claim!r}"
    assert "аналитика сейчас не используется" in n.lower(), "notice must state analytics is not used"


def test_cookie_notice_localstorage_keys_exist_in_frontend():
    # Scope to the localStorage section only — other sections legitimately mention the backend
    # cookie names and (historically) the removed bp_* cookies.
    n = _r(LEGAL / "cookie-notice.DRAFT.md")
    m = re.search(r"##\s*Локальное хранилище.*?(?=\n##\s)", n, re.S)
    section = m.group(0) if m else ""
    assert section, "localStorage section not found in cookie notice"
    fe_blob = "\n".join(txt for _, txt in _fe_sources())
    key_re = re.compile(r"bp_[a-z0-9_]+|pult_(?:theme|cabinet_mode|onboarded)|chist_|ae_active_count|copilot_dismissed|cookie_consent")
    keys = sorted(set(key_re.findall(section)))
    assert keys, "no localStorage keys parsed from the notice section"
    for key in keys:
        assert key in fe_blob, f"cookie notice lists a localStorage key not found in frontend source: {key!r}"


def _table_row(name: str, num: int) -> list[str] | None:
    for line in _r(LEGAL / name).splitlines():
        if line.strip().startswith(f"| {num} |"):
            return [c.strip() for c in line.strip().strip("|").split("|")]
    return None


def test_blocker_13_not_falsely_done_and_drafts_intact():
    row = _table_row("launch-legal-checklist.md", 13)
    assert row is not None and len(row) >= 4, "blocker #13 row missing/malformed"
    joined = " ".join(row).lower()
    assert "cookie" in joined, "#13 must be the cookie-claims blocker"
    status = row[3].upper()
    for bad in ("DONE", "PASS", "READY", "VERIFIED", "CLOSED"):
        assert bad not in status, f"#13 must not be marked {bad} (legal classification still open)"
    assert "НЕ ПУБЛИКОВАТЬ" in _r(LEGAL / "cookie-notice.DRAFT.md"), "cookie notice must keep the DRAFT gate"
    assert "NOT READY" in _r(LEGAL / "launch-legal-checklist.md"), "launch gate must stay NOT READY"


# --- cookie_consent localStorage payload shape (Correction-1) --------------------------------
# These pin the exact object serialized into localStorage.cookie_consent, independently of any
# frontend test run. The check is scoped to the object literal actually passed to
# JSON.stringify(...) for the `cookie_consent` key (resolving the one-hop `const <id> = {...}`
# indirection) plus its TS type alias — NOT a global word search over the component, so the
# localStorage KEY name `cookie_consent` cannot itself trip the forbidden-field check.

# A consent-shaped field would (falsely) imply the acknowledgement toggles optional processing.
_CONSENT_FORBIDDEN_KEYS = (
    "analytics",
    "marketing",
    "tracking",
    "advertising",
    "personalization",
    "consent",
    "consented",
    "optin",
    "opt_in",
)


def _object_keys(literal_body: str) -> list[str]:
    """Top-level object keys from a small `{ a: 1, b: x }` literal / type body (no nesting here)."""
    return re.findall(r"(?:^|[{,;])\s*([A-Za-z_$][\w$]*)\s*[:?]", literal_body)


def _consent_payload_and_type(banner: str) -> tuple[str, str]:
    """Return (runtime object literal body, TS type body) for the cookie_consent payload.

    Resolves `localStorage.setItem('cookie_consent', JSON.stringify(<id>))` -> `const <id>: <T> = {..}`
    and, when the value is typed, the `type <T> = { .. }` alias. Fails loudly if the write is not a
    JSON.stringify of a resolvable object literal (e.g. a raw string or a spread hides the shape)."""
    m = re.search(
        r"localStorage\.setItem\(\s*['\"]cookie_consent['\"]\s*,\s*JSON\.stringify\(\s*([A-Za-z_$][\w$]*)\s*\)\s*\)",
        banner,
    )
    assert m, "cookie_consent must be written via localStorage.setItem('cookie_consent', JSON.stringify(<obj>))"
    ident = m.group(1)
    decl = re.search(
        rf"\b(?:const|let|var)\s+{re.escape(ident)}\s*(?::\s*([A-Za-z_$][\w$]*))?\s*=\s*(\{{[^{{}}]*\}})",
        banner,
    )
    assert decl, f"could not resolve the object literal bound to cookie_consent payload ({ident!r})"
    type_name = decl.group(1)
    literal_body = decl.group(2)
    type_body = ""
    if type_name:
        tm = re.search(rf"\btype\s+{re.escape(type_name)}\s*=\s*(\{{[^{{}}]*\}})", banner)
        type_body = tm.group(1) if tm else ""
    return literal_body, type_body


def test_cookie_consent_payload_shape_pinned():
    banner = _r(BANNER)
    literal_body, type_body = _consent_payload_and_type(banner)

    literal_keys = _object_keys(literal_body)
    assert "acknowledged" in literal_keys, "cookie_consent payload must keep the `acknowledged` field"
    assert "timestamp" in literal_keys, "cookie_consent payload must keep the `timestamp` field"

    # `acknowledged` is a plain acknowledgement flag, set true when the notice is dismissed.
    assert re.search(r"\backnowledged\s*:\s*true\b", literal_body), (
        "cookie_consent.acknowledged must be the literal `true` (acknowledgement, not a stored toggle)"
    )

    # No field may imply analytics / tracking / marketing / (fictional) consent processing.
    for scope, keys in (("literal", literal_keys), ("type", _object_keys(type_body))):
        lowered = {k.lower() for k in keys}
        for bad in _CONSENT_FORBIDDEN_KEYS:
            assert bad not in lowered, f"cookie_consent payload ({scope}) must not carry a `{bad}` field"

    # When the value is typed, the type alias must agree with the runtime literal (no drift).
    if type_body:
        type_keys = _object_keys(type_body)
        assert "acknowledged" in type_keys and "timestamp" in type_keys, (
            "cookie_consent type alias must declare exactly the acknowledged/timestamp contract"
        )


def test_cookie_notice_matches_consent_payload():
    """The notice's cookie_consent description must name the real fields and no invented ones."""
    n = _r(LEGAL / "cookie-notice.DRAFT.md")
    line = next((ln for ln in n.splitlines() if "`cookie_consent`" in ln), "")
    assert line, "cookie notice must document the cookie_consent localStorage key"
    low = line.lower()
    assert "acknowledged" in low, "notice must describe the cookie_consent `acknowledged` field"
    assert "timestamp" in low, "notice must describe the cookie_consent `timestamp` field"
    for bad in ("analytics", "marketing", "tracking", "advertising", "personalization"):
        assert bad not in low, f"notice must not describe a `{bad}` field on cookie_consent"


def test_live_domain_and_runtime_unchanged():
    cfg = _r(CONFIG)
    assert 'frontend_url: str = "http://localhost:3000"' in cfg, "app origin must stay localhost"
    assert "pult-os.ru" not in cfg, "no production domain hardcoded in config"
    priv = FRONTEND / "app" / "privacy" / "page.tsx"
    if priv.is_file():
        assert "biznes-pult.ru" in _r(priv), "live pages must stay on the old domain in this dormant unit"
