# DNS runbook — pult-os.ru (website records NOT APPLIED; mail records applied)

Status: **Website records NOT APPLIED.** No apex/www/TLS record for the site is created by this document —
it is a checklist to run manually at Inal-approved activation, **after** the domain gate
`DOMAIN VERIFIED: pult-os.ru зарегистрирован; идентификация подтверждена.` and after the target IP is known.
Website placeholder values (`<APP_IP>`) are never real IPs in the repo. **NS are not delegated** (zone edits
happen at the registrar).

The **mail** records have since been applied by Inal (Mail Gate PASS): MX / SPF / DKIM / DMARC resolve and
send+receive is verified. The website (apex/www/TLS) is still NOT applied and no live-domain swap has been
performed.

## Applied mail state (Mail Gate PASS — for the record, do not re-apply verbatim)
- MX / SPF / DKIM / DMARC = PASS.
- **DMARC applied:** `v=DMARC1; p=none; rua=mailto:dmarc@pult-os.ru`.
- quarantine/reject **NOT ENABLED**.
- monitoring **≥ 7 days** before any separate Inal-gated decision to raise the policy.
- `dmarc@pult-os.ru` is a **technical DMARC alias only** — aggregate reports, never a public/user contact.

DMARC MONITORING PERIOD: MIN 7 DAYS BEFORE ANY RAISE

> **Correction:** an earlier draft of this runbook prescribed an initial DMARC policy of `p=quarantine`
> with the report address pointed at `security@` — both **wrong** and now superseded. The actual applied
> policy is `p=none` with `rua=mailto:dmarc@pult-os.ru`. Do **not** apply the old value verbatim. Raising to
> quarantine/reject is a **separate future Inal-gated step** after the monitoring period, and requires a new
> review of aggregate reports first.

## Preconditions for the WEBSITE records (all manual, Inal)
- Domain gate confirmed; registrar admin identification via Госуслуги complete.
- Server purchased + hardened (separate gate); app reachable at `<APP_IP>` over HTTPS.

## Website records to create (at activation only — NOT APPLIED)
| Record | Name | Type | Value | Note |
|---|---|---|---|---|
| apex | `pult-os.ru` | A | `<APP_IP>` | primary origin |
| www | `www.pult-os.ru` | CNAME or A | `pult-os.ru` / `<APP_IP>` | redirect to apex (or apex to www — pick one canonical) |
| TLS | — | — | ACME/Let's Encrypt for apex + www | issue AFTER A record resolves; HSTS only after verified |

## Future mail-policy step (separate, NOT part of website activation)
| Record | Name | Type | Value | Note |
|---|---|---|---|---|
| DMARC (future raise) | `_dmarc.pult-os.ru` | TXT | `v=DMARC1; p=quarantine; ...` | **only after ≥7-day monitoring + separate Inal gate + report review**; not applied now |

## Hard checks before/after (website activation)
- **No wildcard** `*.pult-os.ru` record. No unexpected/extra records. No dangling CNAME.
- TLS issued only after the A record resolves; verify cert chain + auto-renew.
- Verify apex, www resolve as intended.
- Confirm no legacy `biznes-pult.ru` records are copied over by mistake.

## Rollback
Remove the added website records (apex/www/TLS); revert `FRONTEND_URL` to the previous origin; the previous
domain/contact stays live meanwhile. DNS rollback touches only the registrar zone — it does not affect the
database, the backup bucket, or any Selectel storage resource.

## Gates unchanged
PUBLIC WEBSITE ACTIVATION: NOT PERFORMED; LIVE DOMAIN SWAP: NOT PERFORMED; legal documents NOT PUBLISHED;
application SMTP OFF; production/deploy OFF; launch gate NOT READY; live-facing pages remain `biznes-pult.ru`.

## Fail-closed
If any precondition or check fails → STOP, do not proceed, keep the current live domain/contact. This
runbook grants no automatic execution; every step is a manual Inal-approved action.
