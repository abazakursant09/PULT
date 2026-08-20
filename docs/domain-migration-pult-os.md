# Domain migration — biznes-pult.ru → pult-os.ru (DORMANT preparation)

Status: **DORMANT. Nothing is switched.** This document only *prepares* the move to the primary domain
`pult-os.ru`. No DNS record is created, no production config is changed, no mailbox exists, no live page
is edited. Base: master `f924fd5a2868a9855d9b8bb06dd6a4c0ac554f52`.

## Brand / positioning (for legal drafts and future pages)
- Brand: **ПУЛЬТ**
- Positioning: **PULT OS — операционная система для продавцов на маркетплейсах**
- Future canonical production domain: **pult-os.ru**
- Future contacts (**NOT ACTIVE** until mail is separately connected):
  `support@pult-os.ru`, `privacy@pult-os.ru`, `security@pult-os.ru`

## Fail-closed activation condition
No live page, config, DNS, or email swap happens until Inal confirms **both** gates:
1. Domain gate — the exact phrase: `DOMAIN VERIFIED: pult-os.ru зарегистрирован; идентификация подтверждена.`
2. Mail gate — the mailboxes `support@ / privacy@ / security@ pult-os.ru` actually exist and receive mail.
Until then the current `biznes-pult.ru` / `hello@biznes-pult.ru` references stay LIVE (a working contact
is safer than switching users to a dead domain/mailbox).

## Audit result — where the old references live (categorised)
| Category | Files | Old value | New value | Activation status |
|---|---|---|---|---|
| A. runtime/config | none hardcoded | app origin via `settings.frontend_url` env (`FRONTEND_URL`) / `NEXT_PUBLIC_API_URL` (default localhost) | prod: `FRONTEND_URL=https://pult-os.ru` (env only) | **gate 1** (env at deploy; not in repo) |
| B. legal pages | `frontend/app/{privacy,offer,terms,agreement,rules}.tsx` | `biznes-pult.ru/<page>`, `hello@biznes-pult.ru` | `pult-os.ru/<page>`, `privacy@pult-os.ru` | **gate 1 + gate 2** |
| C. brand/contact | `frontend/app/{support,dashboard/account}.tsx` | `hello@biznes-pult.ru` | `support@pult-os.ru` | **gate 1 + gate 2** |
| D. tests | `backend/tests/test_cors_prod_origins.py` (example `app.biznes-pult.ru`), `frontend/tests/{legalConsistency,notificationHonesty}.test.tsx` | old contact/domain | new contact/domain | **change together with B/C at activation** |
| E. immutable | none | — | — | never mechanically edited (no audit/SHA-pin/evidence/snapshot references the domain) |

**This PR changes NONE of A–E.** It adds only this document + the DNS runbook + a dormancy guard.

## What changes AT activation (post gate 1 + gate 2 — separate Inal-gated PR)
1. Frontend legal/brand pages (B, C): swap `biznes-pult.ru` → `pult-os.ru`, `hello@biznes-pult.ru` →
   `support@/privacy@pult-os.ru`. Update the D tests in the same PR so they stay green.
2. Production env (A): set `FRONTEND_URL=https://pult-os.ru` (or the chosen app origin) and
   `NEXT_PUBLIC_API_URL` — **env only, never committed**. Localhost/dev/test defaults stay unchanged.
3. Apply DNS per `docs/dns-runbook-pult-os.md`.
Historical audits, canary SHA-pins, evidence, and immutable snapshots are **never** find/replaced.

## Not touched
DNS, registrar panel, Selectel/API/S3, server purchase, deploy, production activation, mailboxes, real
banking/passport/registration data, the `codex/legal-prelaunch-drafts` worktree (`docs/legal/` drafts).
152-ФЗ compliance is not asserted here; the 152-ФЗ product-class decision (3C3-B-CORRECTION) remains open.
