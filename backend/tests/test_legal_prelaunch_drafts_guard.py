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
    # A file-level NOT ACTIVE is not enough: a second, unmarked address must not hide behind it.
    for name in ALL_DOCS:
        for i, line in enumerate(_r(name).splitlines(), 1):
            if any(c in line for c in MAIL_CONTACTS):
                assert "NOT ACTIVE" in line, (
                    f"{name}:{i} names a future pult-os.ru email without NOT ACTIVE on the same line: {line!r}"
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
