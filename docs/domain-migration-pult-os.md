# Domain migration — biznes-pult.ru → pult-os.ru (DORMANT preparation)

Status: **DORMANT. Nothing is switched in the app.** This document only *prepares* the move to the
primary domain `pult-os.ru`. No apex/www DNS record for the website is created, no production config is
changed, no live page is edited, no live-domain swap is performed. The mail side has since been set up by
Inal (see the mail-state block below), but that is mail infrastructure only — it does **not** activate the
website, the legal documents, the application, or any public product contact.

## Mail / domain state (updated — dormant, do not misread as launch)

- **Mail Gate: PASS.** One Yandex 360 organization; exactly one paid mailbox/employee. `support@pult-os.ru`
  is the primary mailbox; `privacy@`, `security@`, `dmarc@` are aliases. Send + receive verified via
  support/privacy/security; mail not flagged as spam; mailbox TOTP enabled.
- `support@pult-os.ru`, `privacy@pult-os.ru`, `security@pult-os.ru` = **TECHNICALLY ACTIVE — DOCUMENT NOT
  PUBLISHED — NOT A PUBLIC PRODUCT CONTACT YET.** The addresses work at the mail layer; as **public product
  contacts** they remain **NOT ACTIVE** until the separate activation PR — they are not yet published
  anywhere as product contacts and must not be presented as such until activation.
- `dmarc@pult-os.ru` = **technical DMARC alias only** (aggregate DMARC reports). It must never appear in a
  publishable legal document or a user-facing page.
- **DMARC applied:** `v=DMARC1; p=none; rua=mailto:dmarc@pult-os.ru`. quarantine/reject **NOT ENABLED**;
  monitoring **≥ 7 days** before any separate Inal-gated raise to quarantine.
- DMARC MONITORING PERIOD: MIN 7 DAYS BEFORE ANY RAISE
- Inventory: domains=1; Yandex 360 organizations=1; paid mailboxes/employees=1; public website DNS/TLS
  activation=0; application SMTP activation=0; production deployment=0.

## Contact routing (for the FUTURE activation swap — not applied yet)

- `support@` — пользовательская поддержка;
- `privacy@` — вопросы ПДн и конфиденциальности;
- `security@` — сообщения об уязвимостях и безопасности (the security-vulnerability contact routes to
  **security@**, NOT support@);
- `dmarc@` — только агрегированные DMARC-отчёты; **не пользовательский контакт**.

SECURITY-VULN CONTACT: security@pult-os.ru — NOT A PUBLIC PRODUCT CONTACT YET

## Brand / positioning (for legal drafts and future pages)
- Brand: **ПУЛЬТ** / **Пульт OS**
- Positioning: **PULT OS — операционная система для продавцов на маркетплейсах**
- Future canonical production domain: **pult-os.ru**

## Fail-closed activation condition
No live page, config, website DNS, TLS, or contact publication happens until Inal confirms **both** gates:
1. Domain gate — the exact phrase: `DOMAIN VERIFIED: pult-os.ru зарегистрирован; идентификация подтверждена.`
2. Mail gate — the mailboxes `support@ / privacy@ / security@ pult-os.ru` exist and receive mail (**now
   PASS at the mail layer**).
Both gates being satisfied at the infrastructure level does **not** by itself flip anything: the live-page
swap, contact publication, and website DNS/TLS remain a separate Inal-gated activation PR. Until then the
current `biznes-pult.ru` / `hello@biznes-pult.ru` references stay LIVE (a working contact is safer than
switching users to an unpublished address).

## Audit result — where the old references live (categorised)
| Category | Files | Old value | New value | Activation status |
|---|---|---|---|---|
| A. runtime/config | none hardcoded | app origin via `settings.frontend_url` env (`FRONTEND_URL`) / `NEXT_PUBLIC_API_URL` (default localhost) | prod: `FRONTEND_URL=https://pult-os.ru` (env only) | **gate 1** (env at deploy; not in repo) |
| B. legal pages | `frontend/app/{privacy,offer,terms,agreement,rules}/page.tsx` | `biznes-pult.ru/<page>`, `hello@biznes-pult.ru` | `pult-os.ru/<page>`, `privacy@pult-os.ru` | **gate 1 + gate 2 + legal review** |
| C. brand/contact | `frontend/app/{support,dashboard/account}/page.tsx`, `frontend/app/rules/page.tsx` (security report) | `hello@biznes-pult.ru` | `support@pult-os.ru`; **security-vuln → `security@pult-os.ru`** | **gate 1 + gate 2 + legal review** |
| D. tests/guards | `backend/tests/{test_cors_prod_origins,test_cookie_truth_guard,test_domain_migration_prep_guard,test_legal_prelaunch_drafts_guard}.py` | examples and dormancy assertions for the old contact/domain | update deliberately together with B/C; never hide the transition by weakening guards | **activation PR only** |
| E. immutable historical snapshot | `docs/legal/attorney-review-request-23.md` | records the then-current old-domain/mail state | **do not mechanically replace**; it is a SHA-pinned historical review package | immutable |
| F. dormant planning/evidence | this file, DNS runbook, legal README/checklist/source-evidence | truthfully record that live pages still use the old domain | update facts only when the corresponding gate actually passes | docs-only |

The preparation remains dormant: it changes no runtime origin, live page, public DNS/TLS, contact
publication, immutable attorney snapshot, SMTP or deploy state. Documentation corrections must preserve
that separation and must not simulate activation.

## What changes AT activation (post gate 1 + gate 2 + legal review — separate Inal-gated PR)
1. Frontend legal/brand pages (B, C): swap `biznes-pult.ru` → `pult-os.ru`, `hello@biznes-pult.ru` →
   `support@/privacy@/security@pult-os.ru` (per the contact routing above). Update the D tests in the same PR.
2. Production env (A): set `FRONTEND_URL=https://pult-os.ru` (or the chosen app origin) and
   `NEXT_PUBLIC_API_URL` — **env only, never committed**. Localhost/dev/test defaults stay unchanged.
3. Apply website DNS (apex/www/TLS) per `docs/dns-runbook-pult-os.md`.
Historical audits, canary SHA-pins, evidence, and immutable snapshots are **never** find/replaced.

## Gates unchanged
PUBLIC WEBSITE ACTIVATION: NOT PERFORMED; LIVE DOMAIN SWAP: NOT PERFORMED; legal documents NOT PUBLISHED;
application SMTP OFF; production/deploy OFF; launch gate NOT READY; live-facing pages remain
`biznes-pult.ru`; runtime config app-origin stays localhost.

## Not touched
Website DNS (apex/www/TLS), registrar delegation (NS not delegated), Selectel/API/S3, server purchase,
deploy, production activation, real banking/passport/registration data, the `codex/legal-prelaunch-drafts`
worktree. 152-ФЗ compliance is not asserted here; the 152-ФЗ product-class decision remains open.
