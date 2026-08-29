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

# A mail-contact occurrence is honest if its line carries EITHER the legacy pre-Mail-Gate marker
# ("NOT ACTIVE", still used by the out-of-scope DRAFT docs) OR a post-Mail-Gate marker in EN/RU.
# Keep one shared, case-insensitive predicate so the legacy and follow-up guards cannot disagree.
_LEGACY_CONTACT_STATUS_MARKERS = ("NOT ACTIVE",)
_POST_MAIL_GATE_CONTACT_STATUS_MARKERS = (
    "NOT A PUBLIC PRODUCT CONTACT YET",
    "не активирован как публичный контакт",
)
CONTACT_STATUS_MARKERS = _LEGACY_CONTACT_STATUS_MARKERS + _POST_MAIL_GATE_CONTACT_STATUS_MARKERS


def _contains_casefold(text: str, markers: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in markers)


def _has_truthful_contact_status(text: str, *, allow_legacy: bool = True) -> bool:
    markers = _POST_MAIL_GATE_CONTACT_STATUS_MARKERS
    if allow_legacy:
        markers = _LEGACY_CONTACT_STATUS_MARKERS + markers
    return _contains_casefold(text, markers)


_PUBLIC_ACTIVATION_CLAIMS = (
    re.compile(r"\bpublicly\s+activ(?:e|ated)\b", re.IGNORECASE),
    re.compile(r"\bpublic\s+product\s+contact\s+is\s+active\b", re.IGNORECASE),
    re.compile(r"\bпублично\s+активирован\w*\b", re.IGNORECASE),
    re.compile(r"\bактивирован\w*\s+как\s+публичн\w*\s+контакт\w*\b", re.IGNORECASE),
)
_PUBLIC_ACTIVATION_NEGATIONS = ("not ", "not yet ", "не ", "ещё не ")


def _has_public_activation_overclaim(text: str) -> bool:
    folded = " ".join(text.casefold().split())
    for pattern in _PUBLIC_ACTIVATION_CLAIMS:
        for match in pattern.finditer(folded):
            prefix = folded[max(0, match.start() - 12):match.start()]
            if any(prefix.endswith(negation) for negation in _PUBLIC_ACTIVATION_NEGATIONS):
                continue
            return True
    return False


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
    # Any doc that names a pult-os.ru mail contact must also flag its non-public status
    # (legacy "NOT ACTIVE" or post-Mail-Gate "NOT A PUBLIC PRODUCT CONTACT YET").
    for name in ALL_DOCS:
        body = _r(name)
        if any(c in body for c in MAIL_CONTACTS):
            assert _has_truthful_contact_status(body), (
                f"{name} names a mail contact without a non-public status marker"
            )


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
    for tok in ("#12", "#13", "#14", "#22", "#23"):
        assert tok in tail, f"minimal-blocker chain must include {tok}"
    assert "NOT READY" in chk, "checklist must keep launch gate NOT READY"


# --- CORRECTION-2 / gap M7: blocker #23 must survive as a real, unfinished blocker ---

def _table_row(name: str, num: int) -> list[str] | None:
    """Return the parsed cells of the markdown table row whose first cell is `num`."""
    prefix = f"| {num} |"
    for line in _r(name).splitlines():
        if line.strip().startswith(prefix):
            return [c.strip() for c in line.strip().strip("|").split("|")]
    return None


def test_blocker_23_official_source_verification_is_a_live_blocker():
    chk = _r("launch-legal-checklist.md")
    cells = _table_row("launch-legal-checklist.md", 23)
    assert cells is not None, "blocker #23 row missing from the full table"
    # cells: [#, требование, доказательство, статус, владелец, staging, production, оплата]
    assert len(cells) >= 4, f"#23 row is malformed: {cells}"
    req = cells[1].lower()
    status = cells[3].upper()
    # (2) #23 is about official-source / line-by-line verification
    assert ("verification" in req or "сверк" in req) and (
        "источник" in req or "первоисточник" in req or "official" in req
    ), f"#23 must be the official-source verification blocker, got requirement: {cells[1]!r}"
    # (3) status is NOT ready/pass/done — it stays an open blocker
    assert status in {"BLOCKED", "UNKNOWN", "PARTIAL"}, f"#23 status must stay a blocker, got {cells[3]!r}"
    for bad in ("DONE", "PASS", "READY", "VERIFIED"):
        assert bad not in status, f"#23 must not be marked {bad} without real counsel verification"
    # (4) #23 present in the minimal-blocker summary chain
    tail = chk.split("Минимальные блокеры до первой оплаты", 1)[1]
    assert "#23" in tail, "#23 must appear in the minimal-blocker chain"


# --- CORRECTION-2 / gap M8: EVERY future-email occurrence must carry NOT ACTIVE on its own line ---

def test_every_future_email_line_marked_not_active():
    # A file-level marker is not enough: a second, unmarked address must not hide behind it.
    # Each contact line must carry a non-public status marker (legacy or post-Mail-Gate) on its own line.
    for name in ALL_DOCS:
        for i, line in enumerate(_r(name).splitlines(), 1):
            if any(c in line for c in MAIL_CONTACTS):
                assert _has_truthful_contact_status(line), (
                    f"{name}:{i} names a future pult-os.ru email without a non-public status marker "
                    f"on the same line: {line!r}"
                )


# ============================================================================
# LEGAL-PRELAUNCH-E2 (blocker #12) — the unproven "logs ≤ 90 days" retention
# promise is removed from the live privacy page; drafts reflect the proven state
# (logs → stdout/stderr, no enforced rotation/deletion; real retention = #25).
# ============================================================================

PRIVACY_PAGE = REPO / "frontend" / "app" / "privacy" / "page.tsx"

# A line is "about logs" if it mentions a log/journal term. Retention promise = such a line ALSO
# stating a concrete duration. We scope the duration check to log-lines so unrelated durations
# (e.g. the offer's "90 дней" subscription window, the "30 дней" deletion SLA) stay legal.
_LOG_TERMS = ("журнал", "лог входа", "логи входа", "лог событий", "security log", "login log")
# Concrete retention duration: a number followed by a day/month/hour unit (covers a CSV-TTL-style
# "1 час" mixed onto a log line as well as day/month figures).
_DURATION_RE = re.compile(r"\d+\s*(?:дн|дня|дней|сут|нед|мес|час|hour|hr|month|week|day)", re.IGNORECASE)


def _privacy_log_lines() -> list[str]:
    if not PRIVACY_PAGE.is_file():
        return []
    return [ln for ln in PRIVACY_PAGE.read_text(encoding="utf-8").splitlines()
            if any(t in ln.lower() for t in _LOG_TERMS)]


def test_e2_privacy_page_has_no_90day_log_promise():
    # (1) No "≤90 days" (or any concrete duration) promise attached to a log line on the LIVE page.
    for ln in _privacy_log_lines():
        assert "90" not in ln, f"log-retention line must not promise 90 days: {ln!r}"


def test_e2_privacy_page_has_no_other_concrete_log_duration():
    # (4) No NEW unproven concrete retention figure smuggled onto a log line.
    for ln in _privacy_log_lines():
        m = _DURATION_RE.search(ln)
        assert m is None, f"log-retention line must not state a concrete duration: {m.group(0)!r} in {ln!r}"


def test_e2_unrelated_durations_still_allowed():
    # (2) The check is scoped: it must NOT wipe legitimate non-log durations elsewhere on the page.
    if PRIVACY_PAGE.is_file():
        body = PRIVACY_PAGE.read_text(encoding="utf-8")
        # the manual deletion SLA (30 дней) is a real, separate statement and must survive
        assert "30 дней" in body, "unrelated deletion SLA must remain on the privacy page"


def test_e2_privacy_page_keeps_security_log_purpose():
    # (3) The truthful PURPOSE of security logs must remain (security + incident investigation).
    lines = _privacy_log_lines()
    assert lines, "privacy page must still describe security/login logs"
    joined = " ".join(lines).lower()
    assert "безопасност" in joined, "security-log purpose (security) must remain"
    assert "инцидент" in joined, "security-log purpose (incident investigation) must remain"


def test_e2_privacy_page_still_old_domain_and_no_prod_flip():
    # dormancy: E2 is a copy correction, not a live-domain flip.
    if PRIVACY_PAGE.is_file():
        assert "biznes-pult.ru" in PRIVACY_PAGE.read_text(encoding="utf-8"), (
            "E2 must not flip the live domain"
        )


def test_e2_blocker_12_partial_not_done():
    # (5) #12 stays PARTIAL — promise removed, but enforced retention NOT implemented.
    cells = _table_row("launch-legal-checklist.md", 12)
    assert cells is not None and len(cells) >= 4, "blocker #12 row missing/malformed"
    joined = " ".join(cells).lower()
    assert "лог" in joined or "log" in joined, "#12 must be the log-retention blocker"
    status = cells[3].upper()
    assert "PARTIAL" in status, "#12 must be PARTIAL (copy corrected, enforcement still open)"
    for bad in ("DONE", "READY", "VERIFIED", "CLOSED", "PASS"):
        assert bad not in status, f"#12 must not be marked {bad} — enforced TTL is not implemented"


def test_e2_blocker_25_still_open_and_separate():
    # (6) #25 remains a separate OPEN operational blocker (retention/access/storage/deletion).
    cells = _table_row("launch-legal-checklist.md", 25)
    assert cells is not None and len(cells) >= 4, "blocker #25 row missing"
    status = cells[3].upper()
    assert "OPEN" in status or "BLOCKED" in status, "#25 must stay OPEN/BLOCKED"
    for bad in ("DONE", "READY", "VERIFIED", "CLOSED", "PASS"):
        assert bad not in status, f"#25 must not be marked {bad}"


def test_e2_stdout_stderr_and_future_infra_documented():
    # (7) Honest current logging state must be reflected in the evidence docs.
    for name in ("personal-data-register.md", "source-evidence.md"):
        body = _r(name)
        assert "stdout/stderr" in body, f"{name} must state the real log destination (stdout/stderr)"
        assert "#25" in body, f"{name} must point production retention to blocker #25"
    chk = _r("launch-legal-checklist.md")
    assert "FUTURE-INFRA" in chk, "checklist #12 must mark enforced retention as FUTURE-INFRA"


def test_e2_launch_gate_still_not_ready():
    # (8) Launch gate untouched.
    assert "NOT READY" in _r("launch-legal-checklist.md"), "launch gate must stay NOT READY"


def test_e2_draft_gate_preserved():
    # (9) The corrected DRAFT still carries the publish gate.
    assert "НЕ ПУБЛИКОВАТЬ" in _r("privacy-policy.DRAFT.md"), "privacy DRAFT must keep publish gate"


def test_e2_stale_email_refs_removed_and_post302_contract_reflected():
    # (10) Stale pre-#302 mailer line refs gone; post-#302 category-only contract reflected.
    body = _r("privacy-policy.DRAFT.md")
    assert "email.py:49,53,56" not in body, "stale pre-#302 email.py:49,53,56 reference must be removed"
    assert "email_send_failed" in body, "post-#302 mail-log contract (category events) must be reflected"
    # the false pre-#302 claim that the mailer logs recipient/subject must not remain as a present fact
    assert "логирует адрес получателя (`to=`)" not in body, "pre-#302 'mailer logs recipient' fact is stale"


# ============================================================================
# LEGAL-PRELAUNCH-G1 (blocker #23, developer slice) — every 149-ФЗ mention in
# docs/legal carries an UNVERIFIED / counsel-review caveat; no doc asserts the
# law's applicability; #23 stays UNKNOWN; source-evidence stays the status source.
# ============================================================================

_149_RE = re.compile(r"149-?\s?ФЗ|Федеральный закон[^\n]{0,6}149", re.IGNORECASE)
# A file mentioning 149-ФЗ must ALSO carry a deferral/caveat marker (local or a pointer
# to the single source of truth) — semantic, not line-pinned.
_149_CAVEAT_MARKERS = ("UNVERIFIED", "official-source verification pending",
                       "REQUIRES RUSSIAN COUNSEL", "юрист", "source-evidence", "#23")
# Positive applicability / compliance claims that must NEVER appear for 149-ФЗ.
_149_FORBIDDEN = (
    "149-ФЗ применяется", "подпадает под 149", "подпадает под действие 149",
    "соответствует требованиям 149", "соответствует 149-ФЗ",
    "является организатором распространения", "не подпадает под 149",
    "149-ФЗ не применяется",
)


def test_g1_149fz_every_mention_near_a_caveat():
    # Per-occurrence, not per-file: a bare 149-ФЗ mention dropped far from any caveat must fail,
    # even in a file that carries an unrelated marker elsewhere. Window ±3 lines is semantic,
    # not a line-number contract.
    found_any = False
    for name in ALL_DOCS:
        lines = _r(name).splitlines()
        for i, ln in enumerate(lines):
            if _149_RE.search(ln):
                found_any = True
                window = "\n".join(lines[max(0, i - 3):i + 4])
                assert any(m in window for m in _149_CAVEAT_MARKERS), (
                    f"{name}:{i + 1} — 149-ФЗ mention without a caveat marker within ±3 lines: {ln!r}"
                )
    assert found_any, "expected at least one 149-ФЗ mention in docs/legal"


def test_g1_readme_149fz_caveat_has_all_markers():
    # README lists 149-ФЗ among "Ключевые" acts, so it must carry the full caveat — checked WITHIN
    # the caveat window (not anywhere in the body), so removing a marker from the caveat fails even
    # if the same phrase survives elsewhere in README.
    lines = _r("README.md").splitlines()
    idx = next((i for i, ln in enumerate(lines)
                if _149_RE.search(ln) and "UNVERIFIED" in "\n".join(lines[max(0, i - 3):i + 4])), None)
    assert idx is not None, "README must carry a dedicated 149-ФЗ UNVERIFIED caveat"
    window = "\n".join(lines[max(0, idx - 3):idx + 5])
    assert "UNVERIFIED" in window, "README 149-ФЗ caveat must say UNVERIFIED"
    assert ("official-source verification pending" in window or "не подтверждены" in window), (
        "README 149-ФЗ caveat must state the source/edition is not confirmed"
    )
    assert "REQUIRES RUSSIAN COUNSEL REVIEW" in window, "README 149-ФЗ caveat must require RF counsel"


def test_g1_source_evidence_keeps_149fz_unverified_status():
    body = _r("source-evidence.md")
    assert "149-ФЗ" in body
    assert "UNVERIFIED" in body and "official-source verification pending" in body, (
        "source-evidence must keep the single-source 149-ФЗ UNVERIFIED status"
    )


def test_g1_no_149fz_applicability_conclusion_anywhere():
    for name in ALL_DOCS:
        body = _r(name)
        for bad in _149_FORBIDDEN:
            assert bad not in body, f"{name} must not draw a 149-ФЗ applicability conclusion: {bad!r}"


def test_g1_blocker_23_not_closed_and_gate_open():
    chk = _r("launch-legal-checklist.md")
    row = _table_row("launch-legal-checklist.md", 23)
    assert row is not None and len(row) >= 4, "#23 row missing"
    status = row[3].upper()
    for bad in ("CLOSED", "DONE", "FIXED", "PASS", "VERIFIED", "READY"):
        assert bad not in status, f"#23 must stay open — not {bad}"
    assert "UNKNOWN" in status or "BLOCKED" in status or "PARTIAL" in status, "#23 must remain a live blocker"
    assert "NOT READY" in chk, "launch gate must stay NOT READY"


# ============================================================================
# LEGAL-PRELAUNCH-#23-COUNSEL — attorney-review request package. Questions only,
# no legal conclusions; #23 stays UNKNOWN; DRAFT / launch gate preserved.
# ============================================================================

_ATTORNEY = "attorney-review-request-23.md"
_ATTORNEY_SECTIONS = (
    "## 3. 152-ФЗ",
    "Постановление Правительства РФ № 1119",
    "Приказ ФСТЭК России № 21",
    "## 7. Роскомнадзор и статья 22",
    "## 8. 149-ФЗ",
    "## 9. 54-ФЗ",
    "защите прав потребителей",
    "## 14. Retention",
)
# --- forbidden legal conclusions: literal classes + SEMANTIC per-line classes -----------------
# Literal conclusions (149-ФЗ / 54-ФЗ applicability, publish-readiness, #23 closure). These forms
# never appear as questions in the package, so a plain substring check is safe.
_ATTORNEY_LITERAL_FORBIDDEN = (
    "149-ФЗ применяется", "149-ФЗ не применяется",
    "54-ФЗ применяется", "54-ФЗ не применяется",
    "готов к публикации", "готова к публикации", "готовы к публикации",
    "#23 CLOSED", "#23 DONE", "#23 FIXED", "blocker #23 закрыт",
)

# A CLAUSE is a QUESTION or a META-disclaimer (not a conclusion) if it matches this. Applied PER
# CLAUSE (not per line), so a meta/question word in a neighbouring clause cannot hide an assertion in
# the next clause. Covers questions (?/ли/какой/…), obligation/future (должн/будет/необходимо/нужно),
# infinitives (определить/обеспечить), negated/undecided/open, and "документ не утверждает …".
_ATTORNEY_CLAUSE_META = re.compile(
    r"\?|\bли\b|\bкак(?:ой|ая|ие|ое)\b|долж(?:ен|на|но|ны)\b|\bбуд(?:ет|ут)\b|необходимо|нужно обеспечить|"
    r"определить|обеспечить|реализовать|исполнить|соблюсти|"
    r"не утвержда|не заявля|не делает|не трактуется|не принят|не устанавливается|не определяет|"
    r"не решён|не решен|остаётся открыт|остается открыт|не подтвержд|"
    r"не выполнен|не соблюд|не обеспечен|не реализован|не исполнен|не соответств|"
    # negated DETERMINATION (participle) — "УЗ не установлено/не определён/…" is a disclaimer, not a verdict.
    r"не установлен\w*|не определ[её]н\w*|не присвоен\w*|не утвержд[её]н\w*|"
    # EN question / modal / negation — so EN duties/obligations questions & disclaimers stay allowed.
    r"\bmust\b|\bshould\b|\bwhether\b|not fulfilled|not implemented|not completed|not satisfied|"
    r"\bhave not\b|\bhas not\b|\bnot been\b|\bnot yet\b|"
    r"попрос|просьба|запрос(?!ов)|вопрос",
    re.IGNORECASE,
)
# Split a line into clauses on strong separators so each clause is judged on its own.
_ATTORNEY_CLAUSE_SPLIT = re.compile(r"([.;:?!]|\s[—–-]\s)")

_UZ_SUBJECT = re.compile(r"УЗ|уровень защищённост|уровень защищенност|\bрешени\w*", re.IGNORECASE)
# Completed PARTICIPLE forms only (определён/…), NOT the noun «определения» or infinitive «определить».
_UZ_DONE = re.compile(
    r"определ[её]н(?:а|о|ы)?\b|установлен(?:а|о|ы)?\b|присвоен(?:а|о|ы)?\b|утвержд[её]н(?:а|о|ы)?\b",
    re.IGNORECASE,
)
_RKN_SUBJECT = re.compile(r"РКН|Роскомнадзор", re.IGNORECASE)
_RKN_NOTIF = re.compile(r"уведомл", re.IGNORECASE)
_RKN_VERDICT = re.compile(
    r"обязательн\w*|обязан[аоы]?\b|требуется|не требуется|не нужно|освобожд\w*|подан[оа]|направлен[оа]|подача",
    re.IGNORECASE)
_ATT_SUBJECT = re.compile(r"аттестаци", re.IGNORECASE)
_ATT_VERDICT = re.compile(r"обязательн\w*|требуется|не требуется|не нужн\w*|освобожд\w*", re.IGNORECASE)
# Compliance-fulfilled conclusion: a subject + a COMPLETED assertive verdict. Subject now also covers
# оператора's DUTIES (обязанность/обязанности/duties); done also covers EN completion verbs.
_COMPLY_SUBJECT = re.compile(
    r"требовани\w*|соответстви\w*|\bмер[аы]\b|меры\b|compliance|соблюд\w*|обязанност\w*|duties|obligation\w*",
    re.IGNORECASE)
_COMPLY_DONE = re.compile(
    r"выполнен\w*|соблюден\w*|обеспечен\w*|реализован\w*|исполнен\w*|соответствует\b|"
    r"fulfilled|completed|implemented|satisfied",
    re.IGNORECASE)


def _attorney_conclusion_hits(body: str) -> list[str]:
    """Forbidden legal-conclusion hits: literal (body-wide) + semantic (per CLAUSE). Question / meta /
    negated / future / obligation clauses are excluded, but only the clause they occur in — a meta or
    question word in one clause cannot shield an assertion in another clause of the same line."""
    hits: list[str] = []
    for lit in _ATTORNEY_LITERAL_FORBIDDEN:
        if lit in body:
            hits.append(lit)
    for ln in body.splitlines():
        # Split into clauses but KEEP the separators, so a clause that ENDS with "?" is still seen as a
        # question (a "?" that were merely a split boundary would otherwise strip the interrogative signal).
        parts = _ATTORNEY_CLAUSE_SPLIT.split(ln)
        for idx in range(0, len(parts), 2):
            clause = parts[idx]
            trailing = parts[idx + 1] if idx + 1 < len(parts) else ""
            if not clause.strip() or _ATTORNEY_CLAUSE_META.search(clause + trailing):
                continue
            if _UZ_SUBJECT.search(clause) and _UZ_DONE.search(clause):
                hits.append(f"УЗ determined: {clause.strip()[:70]}")
            if _RKN_SUBJECT.search(clause) and _RKN_NOTIF.search(clause) and _RKN_VERDICT.search(clause):
                hits.append(f"РКН verdict: {clause.strip()[:70]}")
            if _ATT_SUBJECT.search(clause) and _ATT_VERDICT.search(clause):
                hits.append(f"аттестация verdict: {clause.strip()[:70]}")
            if _COMPLY_SUBJECT.search(clause) and _COMPLY_DONE.search(clause):
                hits.append(f"compliance-done: {clause.strip()[:70]}")
    return hits


def test_attorney_package_exists_with_gates():
    body = _r(_ATTORNEY)
    assert body.strip(), "attorney package must exist and be non-empty"
    assert "DRAFT" in body, "attorney package must carry DRAFT"
    assert "НЕ ПУБЛИКОВАТЬ" in body, "attorney package must carry НЕ ПУБЛИКОВАТЬ"
    assert "ATTORNEY REVIEW REQUIRED" in body, "attorney package must be marked ATTORNEY REVIEW REQUIRED"
    assert "NOT READY" in body, "attorney package must keep launch gate NOT READY"
    assert "#23 = UNKNOWN" in body or "blocker #23 = UNKNOWN" in body, "#23 must stay UNKNOWN"


def test_attorney_package_is_questions_not_conclusions():
    body = _r(_ATTORNEY)
    assert ("не юридическое заключение" in body or "НЕ юридическое заключение" in body
            or "перечень ВОПРОСОВ" in body), "package must state it is questions, not a legal conclusion"
    # honest record that primary-source verification failed from the environment
    assert ("certificate/socket" in body or "certificate" in body.lower()), (
        "package must honestly record the primary-source verification failure"
    )
    assert "REQUIRES RUSSIAN COUNSEL REVIEW" in body


def test_attorney_package_has_all_required_sections():
    body = _r(_ATTORNEY)
    for sec in _ATTORNEY_SECTIONS:
        assert sec in body, f"attorney package missing required section marker: {sec!r}"
    assert "Формат ответа юриста" in body, "package must define the lawyer response format"
    assert "Exit criteria" in body or "Exit-criteria" in body, "package must define #23 exit criteria"


def test_attorney_package_draws_no_legal_conclusion():
    hits = _attorney_conclusion_hits(_r(_ATTORNEY))
    assert hits == [], f"attorney package must not state legal conclusions: {hits}"


def test_attorney_conclusion_detector_flags_assertions():
    # Positive controls: assertive conclusions the detector MUST flag (guard-hardening regression).
    for s in (
        "УЗ ПДн определён.",
        "для ИСПДн установлен уровень защищённости.",
        "для ИСПДн установлен УЗ-2.",
        "уведомление Роскомнадзора не требуется.",
        "уведомление Роскомнадзора обязательно.",
        "уведомлять РКН не нужно.",
        "подача уведомления в Роскомнадзор обязательна.",
        "аттестация обязательна.",
        "аттестация не требуется.",
        "система освобождена от аттестации.",
        "149-ФЗ применяется",
        "54-ФЗ не применяется",
        # mixed-context: assertion in a clause after a question/meta clause (GUARDFIX-2 regression)
        "Вопрос рассмотрен: УЗ ПДн определён",
        "Какой статус? УЗ ПДн определён",
        "Документ не утверждает обратного: уведомление РКН обязательно",
        "Требуется ли уведомление? Уведомление Роскомнадзора не требуется",
        "Юрист должен определить статус; аттестация не требуется",
        "Вопрос об УЗ открыт — уровень защищённости установлен",
        "Применим ли закон? 149-ФЗ применяется к Пульт OS",
        "Документ не утверждает обратного. 54-ФЗ не применяется",
        "Какой статус? уведомление Роскомнадзора обязательно",
        "Вопрос рассмотрен — аттестация обязательна",
        "Юрист уточнит: подача уведомления в Роскомнадзор обязательна",
        "Открытый вопрос: УЗ информационной системы установлен",
        # compliance-done conclusions
        "требования законодательства выполнены",
        "все требования законодательства выполнены",
        "Пульт OS соответствует требованиям законодательства",
        "сервис соответствует требованиям 152-ФЗ",
        "полностью соответствует требованиям закона",
        "соответствие законодательству обеспечено",
        "compliance выполнен",
        "правовые требования соблюдены",
        "все обязательные меры реализованы",
        "требования регулятора исполнены",
        # GUARDFIX-3: operator DUTIES fulfilled + negated-determination boundary + more verdicts
        "обязанности оператора исполнены",
        "обязанности исполнены",
        "обязанность выполнена",
        "обязанности соблюдены",
        "все обязанности исполнены",
        "legal duties fulfilled",
        "compliance duties completed",
        "решение утверждено",
        "аттестация не нужна",
        # GUARDFIX-4: EN «obligations» completion (parity with «duties»)
        "legal obligations implemented",
        "legal obligations fulfilled",
        "compliance obligations completed",
        "operator obligations satisfied",
    ):
        assert _attorney_conclusion_hits(s), f"detector must flag conclusion: {s!r}"


def test_attorney_conclusion_detector_allows_questions_and_disclaimers():
    # Negative controls: questions / not-decided / future / obligation / meta-disclaimers stay allowed.
    for s in (
        "какой УЗ должен быть определён?",
        "требуется ли уведомление Роскомнадзора?",
        "обязательна ли аттестация?",
        "решение по УЗ не принято",
        "УЗ не устанавливается этим документом",
        "документ не утверждает, что уведомление обязательно",
        "требуется определить УЗ",
        "юрист должен определить, требуется ли уведомление",
        "вопрос об аттестации не решён",
        # compliance negative controls
        "выполнены ли требования законодательства?",
        "какие требования должны быть выполнены?",
        "требования пока не выполнены",
        "требования будут выполнены до запуска",
        "необходимо обеспечить соответствие",
        "юрист должен определить применимые требования",
        "документ не утверждает, что требования выполнены",
        "соответствие не подтверждено",
        "вопрос соответствия остаётся открытым",
        # GUARDFIX-3: negated determination + masc «должен» + duties-not-confirmed must stay allowed
        "УЗ не установлено окончательно, юрист должен подтвердить",
        "УЗ не определён, юрист должен определить его",
        "уровень не присвоен",
        "решение не утверждено",
        "требования не установлены",
        "обязанности оператора не подтверждены",
        "исполнение обязанностей будет проверено юристом",
        "должен ли оператор уведомлять РКН?",
        "какие обязанности должен подтвердить юрист?",
        # GUARDFIX-4: EN obligations questions / modal / negation stay allowed
        "are legal obligations fulfilled?",
        "legal obligations must be fulfilled",
        "legal obligations are not fulfilled",
        "legal obligations have not been implemented",
        "whether legal obligations are fulfilled",
        "the lawyer must determine whether obligations are fulfilled",
    ):
        assert _attorney_conclusion_hits(s) == [], f"detector must NOT flag a question/disclaimer: {s!r}"


def test_attorney_package_carries_no_requisites():
    # No real ИНН/ОГРН/account (11+ digit run), no keys — requisites go to counsel outside Git.
    body = _r(_ATTORNEY)
    assert not re.search(r"\d{11,}", body), "no requisite-like long digit run in the attorney package"
    assert "AKIA" not in body and "-----BEGIN" not in body, "no secret material in the attorney package"


# ============================================================================
# DOMAIN-MAIL-DOCS-GUARD-FIRST — dormant mail-state correction fence.
#
# Mail Gate is PASS at the infrastructure layer, but nothing about the website,
# the legal documents, or the public product contacts is activated. This fence
# proves the dormant planning/evidence docs tell that exact truth and that the
# technical `dmarc@` alias never leaks into a publishable document or a live page.
#
# Explicit allowlisted / forbidden FILE SETS (no repo-wide grep that would
# collide with the legitimate planning docs).
# ============================================================================

# Planning / evidence docs where `dmarc@` and the applied DMARC state may appear.
DMARC_ALLOWLISTED = (
    REPO / "docs" / "dns-runbook-pult-os.md",
    REPO / "docs" / "domain-migration-pult-os.md",
    LEGAL / "README.md",
    LEGAL / "source-evidence.md",
    LEGAL / "personal-data-register.md",
)

# The two migration/DNS planning docs that must state the applied DMARC state + gates.
MIGRATION_DOCS = (
    REPO / "docs" / "dns-runbook-pult-os.md",
    REPO / "docs" / "domain-migration-pult-os.md",
)

# Live-facing frontend pages — the technical dmarc@ alias must never appear here.
LIVE_FRONTEND_PAGES = tuple(
    REPO / "frontend" / "app" / p for p in (
        "privacy/page.tsx", "terms/page.tsx", "offer/page.tsx", "agreement/page.tsx",
        "rules/page.tsx", "support/page.tsx", "dashboard/account/page.tsx",
    )
)

# `dmarc@` is FORBIDDEN in every publishable legal doc and every live-facing page.
DMARC_FORBIDDEN = tuple(LEGAL / n for n in PUBLIC_DOCS) + LIVE_FRONTEND_PAGES

DMARC_ALIAS = "dmarc@pult-os.ru"

# Applied/current-state DMARC anchor + policy tokens (structural, not line-pinned). The APPLIED
# policy line is the one asserting the state that is live now ("DMARC applied"); a p=none sitting
# elsewhere in the doc must never mask a wrong applied policy, and a quarantine/reject value is only
# legal on a clearly FUTURE / NOT-APPLIED / Inal-gated line.
_DMARC_APPLIED_RE = re.compile(r"DMARC\s+applied", re.IGNORECASE)
_P_NONE_RE = re.compile(r"p\s*=\s*none", re.IGNORECASE)
_P_QUARANTINE_RE = re.compile(r"p\s*=\s*quarantine", re.IGNORECASE)
_P_REJECT_RE = re.compile(r"p\s*=\s*reject", re.IGNORECASE)
# Markers that make a NON-applied quarantine/reject mention legal (future/planned, gated, superseded).
_DMARC_FUTURE_MARKERS = ("future", "not applied", "not enabled", "inal-gated", "inal gate",
                         "superseded", "only after", "separate")


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


# --- 1/2. dmarc@ alias containment ------------------------------------------

def test_dmarc_alias_absent_from_publishable_and_live_facing():
    # (1) dmarc@ must not surface in any publishable legal doc or live-facing page.
    for path in DMARC_FORBIDDEN:
        body = _read(path)
        if body is not None:
            assert DMARC_ALIAS not in body, f"dmarc@ technical alias leaked into {path.name} ({path})"


def test_dmarc_alias_only_in_allowlisted_dormant_docs():
    # (2) dmarc@ is allowed ONLY in the explicitly allowlisted dormant planning/evidence files,
    #     and it must actually be documented there as a technical alias.
    documented = False
    for path in DMARC_ALLOWLISTED:
        body = _read(path)
        if body and DMARC_ALIAS in body:
            documented = True
    assert documented, "dmarc@ must be documented as a technical alias in an allowlisted dormant doc"


def test_dmarc_alias_marked_technical_only():
    # dmarc@ where present in allowlisted docs must be marked technical / not a user contact.
    for path in DMARC_ALLOWLISTED:
        body = _read(path)
        if body and DMARC_ALIAS in body:
            low = body.lower()
            assert ("технический" in low or "technical" in low) and (
                "не пользовательский" in low or "not a public" in low or "never a public" in low
                or "not a user" in low
            ), f"{path.name} must mark dmarc@ as a technical-only, non-user contact"


def test_attorney_document_has_no_dmarc_alias():
    # CROSS-BYPASS fix (proven MISS, not a merge loss): the attorney package is a legal-review
    # document, NOT a DMARC runbook — the technical dmarc@ aggregate alias must never appear in it.
    # Narrow and fail-closed: only the attorney file is fenced here (infra/runbook docs keep their
    # allowlist), and a missing file is RED. Not line-number pinned.
    path = LEGAL / _ATTORNEY
    assert path.is_file(), f"attorney package missing: {path}"
    assert DMARC_ALIAS not in path.read_text(encoding="utf-8"), (
        "technical dmarc@ alias must not leak into the attorney package"
    )


# --- 3. applied DMARC state in the planning docs ----------------------------

def test_planning_docs_state_actual_dmarc_policy():
    # (3) The planning docs must carry the ACTUAL applied DMARC state, not the stale runbook value.
    for path in MIGRATION_DOCS:
        body = _read(path)
        assert body is not None, f"missing planning doc {path}"
        assert "p=none" in body, f"{path.name} must state the applied DMARC policy p=none"
        assert "rua=mailto:dmarc@pult-os.ru" in body, f"{path.name} must state rua=dmarc@ (not security@)"
        assert "quarantine/reject" in body and "NOT ENABLED" in body, (
            f"{path.name} must state quarantine/reject NOT ENABLED"
        )
        # unambiguous, mutation-resistant token (plain "monitoring"/"7" recur elsewhere in prose)
        assert "DMARC MONITORING PERIOD: MIN 7 DAYS BEFORE ANY RAISE" in body, (
            f"{path.name} must carry the canonical ≥7-day DMARC monitoring token"
        )


def test_planning_docs_reject_stale_dmarc_value():
    # The exact stale applied value (p=quarantine + rua=security@) must not be presented as applied.
    stale = "p=quarantine; rua=mailto:security@pult-os.ru"
    for path in MIGRATION_DOCS:
        body = _read(path) or ""
        assert stale not in body, f"{path.name} must not carry the stale applied DMARC value {stale!r}"


def test_applied_dmarc_policy_is_none_not_quarantine_or_reject():
    # CROSS-BYPASS fix (proven MISS, not a merge loss): the earlier check was presence-only
    # ("p=none" in body), so an applied policy flipped to p=quarantine/p=reject slipped through as
    # long as a decoy p=none survived somewhere else. This locates the APPLIED/current-state line
    # structurally and enforces the policy on THAT line; a stray p=none elsewhere cannot mask it.
    # Future/planned quarantine stays legal only on a clearly FUTURE / NOT-APPLIED / gated line.
    for path in MIGRATION_DOCS:
        body = _read(path)
        assert body is not None, f"missing planning doc {path}"        # fail-closed
        lines = body.splitlines()
        applied = [ln for ln in lines if _DMARC_APPLIED_RE.search(ln)]
        assert applied, f"{path.name} must carry an explicit 'DMARC applied' policy line"  # fail-closed
        for ln in applied:
            assert _P_NONE_RE.search(ln), f"{path.name} applied DMARC policy must be p=none: {ln!r}"
            assert not _P_QUARANTINE_RE.search(ln), (
                f"{path.name} applied DMARC policy must NOT be p=quarantine: {ln!r}"
            )
            assert not _P_REJECT_RE.search(ln), (
                f"{path.name} applied DMARC policy must NOT be p=reject: {ln!r}"
            )
        # A quarantine/reject value OFF the applied line is legal only when explicitly marked
        # future / not-applied / gated / superseded within a small window (±3 lines).
        for i, ln in enumerate(lines):
            if _DMARC_APPLIED_RE.search(ln):
                continue
            if _P_QUARANTINE_RE.search(ln) or _P_REJECT_RE.search(ln):
                window = " ".join(lines[max(0, i - 3):i + 4]).lower()
                assert any(mk in window for mk in _DMARC_FUTURE_MARKERS), (
                    f"{path.name}:{i + 1} quarantine/reject without a FUTURE/NOT-APPLIED/gated "
                    f"marker within ±3 lines: {ln!r}"
                )


# --- 4. no false public-activation claim ------------------------------------

def test_migration_docs_keep_not_performed_gates():
    # (4) Planning docs must not imply the website / live swap / launch happened.
    for path in MIGRATION_DOCS:
        body = _read(path)
        assert body is not None
        assert "PUBLIC WEBSITE ACTIVATION: NOT PERFORMED" in body, f"{path.name} website-activation gate missing"
        assert "LIVE DOMAIN SWAP: NOT PERFORMED" in body, f"{path.name} live-swap gate missing"
        assert "legal documents NOT PUBLISHED" in body, f"{path.name} legal-not-published gate missing"
        assert "application SMTP OFF" in body, f"{path.name} app-SMTP-OFF gate missing"
        assert "launch gate NOT READY" in body, f"{path.name} launch-gate-not-ready gate missing"


# --- 5. contacts technically active but not publicly activated ---------------

CONTACT_STATUS_DOCS = (
    REPO / "docs" / "domain-migration-pult-os.md",
    LEGAL / "README.md",
    LEGAL / "personal-data-register.md",
)


def test_contacts_marked_technically_active_not_publicly_activated():
    # (5) support/privacy/security must be shown as technically active but NOT publicly activated.
    for path in CONTACT_STATUS_DOCS:
        body = _read(path)
        assert body is not None, f"missing contact-status doc {path}"
        assert "TECHNICALLY ACTIVE" in body, f"{path.name} must state contacts are TECHNICALLY ACTIVE"
        assert ("NOT A PUBLIC PRODUCT CONTACT YET" in body or "NOT PUBLICLY ACTIVATED" in body), (
            f"{path.name} must state contacts are NOT publicly activated"
        )


def test_no_stale_not_active_until_mail_gate_in_edited_docs():
    # The stale "NOT ACTIVE до Mail Gate" phrasing must be gone from the docs corrected by this slice
    # (it is only allowed to survive in the out-of-scope DRAFT public docs, not here).
    for path in (LEGAL / "README.md", LEGAL / "personal-data-register.md",
                 REPO / "docs" / "domain-migration-pult-os.md"):
        body = _read(path) or ""
        assert "NOT ACTIVE до Mail Gate" not in body, f"{path.name} must not keep the stale NOT-ACTIVE phrasing"


# --- 6/7. launch gate + live domain ----------------------------------------

def test_launch_gate_still_not_ready_in_readme():
    # (6)
    assert "NOT READY" in _r("README.md"), "README must keep launch gate NOT READY"


def test_live_facing_pages_stay_old_domain():
    # (7) live-facing pages remain on biznes-pult.ru (no live-domain swap performed).
    checked = False
    for path in LIVE_FRONTEND_PAGES:
        body = _read(path)
        if body is not None:
            checked = True
            assert "biznes-pult.ru" in body, f"{path.name} must stay on biznes-pult.ru (no swap)"
            assert "pult-os.ru" not in body, f"{path.name} must not adopt pult-os.ru before activation"
    assert checked, "expected at least one live-facing page to verify"


# --- 8/9/10. runtime config untouched + app SMTP OFF ------------------------

def test_runtime_config_localhost_no_pultos_and_smtp_off():
    # (8) frontend_url localhost, (9) no pult-os.ru hardcoded, (10) application SMTP OFF.
    cfg = CONFIG.read_text(encoding="utf-8")
    assert 'frontend_url: str = "http://localhost:3000"' in cfg, "app origin must stay localhost"
    assert "pult-os.ru" not in cfg, "no production domain hardcoded in config"
    assert 'smtp_host: str = ""' in cfg, "application SMTP must stay OFF (empty smtp_host default)"


# --- 12. security-vulnerability contact routes to security@, never support@ ---

def test_security_vuln_contact_routes_to_security_not_support():
    for path in (REPO / "docs" / "domain-migration-pult-os.md", LEGAL / "README.md"):
        body = _read(path)
        assert body is not None, f"missing routing doc {path}"
        assert "SECURITY-VULN CONTACT: security@pult-os.ru" in body, (
            f"{path.name} must route the security-vulnerability contact to security@"
        )
        assert "SECURITY-VULN CONTACT: support@" not in body, (
            f"{path.name} must not route the security-vulnerability contact to support@"
        )


# ============================================================================
# DOMAIN-MAIL-ACTIVATION-FOLLOWUP — the publishable DRAFT contact lines are
# corrected from the stale pre-Mail-Gate "NOT ACTIVE до Mail Gate" wording to
# the truthful post-Mail-Gate status: the mailbox is technically active, but the
# DOCUMENT is NOT published and the address is NOT yet a public product contact.
#
# Per-file / per-contact-line — no repo-wide grep, no line-number pinning. A
# correct token in a neighbouring file must NOT mask a defect in another file,
# so every check reads each file on its own.
# ============================================================================

# Publishable DRAFT docs that carry a pult-os.ru mail-contact line.
FOLLOWUP_CONTACT_DOCS = (
    "privacy-policy.DRAFT.md",
    "personal-data-consent.DRAFT.md",
    "user-agreement.DRAFT.md",
)

# The truthful post-Mail-Gate semantics; each accepts the RU phrasing OR the
# closed English status token, so alternative wording that keeps the meaning
# stays GREEN.
_TECH_ACTIVE = ("TECHNICALLY ACTIVE", "Технически работает")
_NOT_PUBLISHED = ("DOCUMENT NOT PUBLISHED", "Документ не опубликован")
_NOT_PUBLIC_CONTACT = _POST_MAIL_GATE_CONTACT_STATUS_MARKERS

# The ПДн subject-rights contact in the privacy docs must stay privacy@, never support@.
PRIVACY_ROUTED_DOCS = ("privacy-policy.DRAFT.md", "personal-data-consent.DRAFT.md")


def _followup_contact_lines(name: str) -> list[str]:
    return [ln for ln in _r(name).splitlines() if any(c in ln for c in MAIL_CONTACTS)]


def test_followup_every_draft_contact_line_states_post_mail_gate_truth():
    # Each mail-contact line in each publishable DRAFT must carry all three truths ON ITS OWN LINE.
    for name in FOLLOWUP_CONTACT_DOCS:
        lines = _followup_contact_lines(name)
        assert lines, f"{name} must still carry a pult-os.ru mail-contact line"
        for ln in lines:
            assert any(m in ln for m in _TECH_ACTIVE), (
                f"{name}: contact line missing technical-active status: {ln!r}"
            )
            assert any(m in ln for m in _NOT_PUBLISHED), (
                f"{name}: contact line missing DOCUMENT-NOT-PUBLISHED status: {ln!r}"
            )
            assert _has_truthful_contact_status(ln, allow_legacy=False), (
                f"{name}: contact line missing NOT-A-PUBLIC-CONTACT status: {ln!r}"
            )


def test_followup_no_legacy_pre_mail_gate_wording_in_publishable_drafts():
    # The stale pre-Mail-Gate wording must be gone from every publishable DRAFT.
    for name in FOLLOWUP_CONTACT_DOCS:
        body = _r(name)
        assert "NOT ACTIVE до Mail Gate" not in body, f"{name} must drop the stale pre-Mail-Gate wording"
        assert "MAIL GATE PENDING" not in body, f"{name} must not carry MAIL GATE PENDING"
        assert "подставить рабочий адрес только после проверки почты" not in body, (
            f"{name} must drop the stale 'wire the address only after mail check' instruction"
        )


def test_followup_draft_contacts_do_not_overclaim_public_activation():
    # Truthful status must not tip over into a public-activation / live-site / production claim.
    for name in FOLLOWUP_CONTACT_DOCS:
        for ln in _followup_contact_lines(name):
            assert not _has_public_activation_overclaim(ln), (
                f"{name} must not claim public activation on a contact line: {ln!r}"
            )


def test_followup_public_activation_detector_is_case_and_locale_safe():
    dangerous = (
        "NOT A PUBLIC PRODUCT CONTACT YET · publicly active",
        "NOT A PUBLIC PRODUCT CONTACT YET · Publicly Activated",
        "не активирован как публичный контакт · публично активирован",
        "не активирован как публичный контакт · ПУБЛИЧНО АКТИВИРОВАНА",
        "public product contact is active",
        "активирована как публичный контакт",
    )
    for text in dangerous:
        assert _has_public_activation_overclaim(text), f"affirmative activation must be rejected: {text!r}"

    safe = (
        "NOT A PUBLIC PRODUCT CONTACT YET",
        "not publicly active",
        "not yet publicly activated",
        "не публично активирован",
        "ещё не публично активирована",
        "не активирован как публичный контакт",
        "ещё не активирована как публичный контакт",
    )
    for text in safe:
        assert not _has_public_activation_overclaim(text), f"negated status must stay valid: {text!r}"


def test_followup_contact_status_predicate_accepts_en_ru_and_scopes_legacy():
    assert _has_truthful_contact_status("NOT A PUBLIC PRODUCT CONTACT YET", allow_legacy=False)
    assert _has_truthful_contact_status("Не активирован как публичный контакт", allow_legacy=False)
    assert _has_truthful_contact_status("not a public product contact yet", allow_legacy=False)
    assert not _has_truthful_contact_status("NOT ACTIVE до Mail Gate", allow_legacy=False)
    assert _has_truthful_contact_status("NOT ACTIVE до Mail Gate", allow_legacy=True)


def test_followup_privacy_docs_route_subject_rights_to_privacy_not_support():
    # Routing not confused: the ПДн subject-rights contact stays privacy@, never support@.
    for name in PRIVACY_ROUTED_DOCS:
        for ln in _followup_contact_lines(name):
            assert "privacy@pult-os.ru" in ln, f"{name}: subject-rights contact must be privacy@: {ln!r}"
            assert "support@pult-os.ru" not in ln, f"{name}: subject-rights contact must not be support@: {ln!r}"


def test_followup_dmarc_alias_absent_from_updated_drafts():
    # The technical dmarc@ alias must never leak into a publishable DRAFT.
    for name in FOLLOWUP_CONTACT_DOCS:
        assert DMARC_ALIAS not in _r(name), f"{name} must not carry the technical dmarc@ alias"


def test_followup_draft_and_launch_gates_preserved():
    # DRAFT publish gate stays on every edited doc; launch gate stays NOT READY.
    for name in FOLLOWUP_CONTACT_DOCS:
        assert "НЕ ПУБЛИКОВАТЬ" in _r(name), f"{name} must keep the publish gate"
    assert "NOT READY" in _r("launch-legal-checklist.md"), "launch gate must stay NOT READY"


# --- GUARDFIX: pin each publishable DRAFT's contact ADDRESS and contact-line COUNT ---
#
# Two review-confirmed completeness gaps are closed here:
#   (1) routing was only pinned for the privacy docs — user-agreement could be
#       silently misrouted support@ -> privacy@/security@ without a RED;
#   (2) only "at least one truthful contact line" was required — dropping one of
#       several redundant contact lines went unnoticed.
#
# Per-file / per-contact-line, data-driven. No line-number pinning, no repo-wide
# grep. Every check reads each file on its own, so a correct value in a
# neighbouring line or another document cannot mask a defect.

# name -> (the ONLY pult-os.ru mail address allowed on that doc's contact lines,
#          exact number of contact lines that doc must carry)
FOLLOWUP_DOC_CONTRACT = {
    "privacy-policy.DRAFT.md": ("privacy@pult-os.ru", 3),
    "personal-data-consent.DRAFT.md": ("privacy@pult-os.ru", 1),
    "user-agreement.DRAFT.md": ("support@pult-os.ru", 2),
}


def test_followup_each_draft_routes_to_its_designated_address():
    # Every contact line in a doc must use that doc's designated address and must
    # NOT carry either of the other two pult-os.ru addresses (no confusion, both
    # directions — support<->privacy<->security).
    for name, (want, _count) in FOLLOWUP_DOC_CONTRACT.items():
        others = [c for c in MAIL_CONTACTS if c != want]
        lines = _followup_contact_lines(name)
        assert lines, f"{name} must carry a pult-os.ru mail-contact line"
        for ln in lines:
            assert want in ln, f"{name}: contact line must route to {want}: {ln!r}"
            for other in others:
                assert other not in ln, (
                    f"{name}: contact line must not route to {other} (expected {want}): {ln!r}"
                )


def test_followup_each_draft_keeps_its_exact_contact_line_count():
    # Dropping (or adding) a contact line in any publishable DRAFT must RED.
    for name, (_want, count) in FOLLOWUP_DOC_CONTRACT.items():
        got = len(_followup_contact_lines(name))
        assert got == count, (
            f"{name} must carry exactly {count} pult-os.ru contact line(s), found {got}"
        )


# --- POST-MERGE FACT CORRECTION: Mail Gate is complete, publication is not ---

def test_privacy_draft_separates_mail_gate_from_public_activation():
    body = _r("privacy-policy.DRAFT.md")
    assert "Mail Gate PASS" in body, "privacy DRAFT must record the completed mail gate"
    assert not re.search(r"почт\w*\s+не\s*актив", body, re.IGNORECASE), (
        "privacy DRAFT must not deny the working mail infrastructure"
    )
    assert "сайт не активирован" in body, "website activation must remain explicitly off"
    assert "документ не опубликован" in body, "publication gate must remain explicit"
    assert "не активированы как публичные контакты" in body, "contacts must not be presented as public"


def test_launch_checklist_records_mail_gate_done_without_launch_promotion():
    body = _r("launch-legal-checklist.md")
    mail_rows = [line for line in body.splitlines() if line.startswith("| 1 |")]
    assert len(mail_rows) == 1, "launch checklist must carry exactly one Mail Gate row"
    row = mail_rows[0]
    assert "Mail Gate PASS" in row and "| DONE |" in row, "Mail Gate row must be completed"
    assert not re.search(r"почт\w*\s+не\s*актив", row, re.IGNORECASE), (
        "stale pre-Mail-Gate state must be absent"
    )
    assert "| BLOCKED |" not in row, "completed Mail Gate must not remain BLOCKED"
    assert "dmarc@" in row and "технический alias" in row, "DMARC alias must remain technical-only"
    assert "не активированы публично" in row, "completed mail infrastructure must not imply public activation"
    assert "launch gate = NOT READY" in body, "launch gate must remain NOT READY"
