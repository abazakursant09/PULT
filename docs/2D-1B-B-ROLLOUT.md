# SECURITY-2D-1B-B — deployment / rollout contract (claim-before-dispatch)

This migration changes the executor identity model (partial-UNIQUE claim on `v1:` operation keys) and
switches every caller + the frontend to the new key contract in ONE atomic release. The alias guard
protects **committed** legacy `review:`/`decision:` rows, but it **cannot** see an old request that is
still executing in-process during the cutover (the old executor committed a `pending` row BEFORE calling
the provider, then wrote the terminal status only after — so a request that has dispatched but not yet
recorded its outcome is invisible to any DB check). Therefore a **drain is mandatory**.

## Mandatory ordered steps

1. **Stop** the scheduler and all background workers.
2. **Drain / maintenance window**: stop accepting new executable requests and wait for all in-process
   execution requests (old protocol, manual content-key requests, and in-flight traffic) to finish, so
   no old request is mid-dispatch when the new code comes up.
3. **Backup** PostgreSQL (`pg_dump`).
4. **Migrate + preflight**: run `alembic upgrade head`. The `uqc1a2b3c4d01` migration fails closed (with a
   numeric duplicate count only — never user/key/payload) if any two rows already share a `v1:` key.
5. **Deploy backend and frontend together** — never a half-state where one sends the header and the other
   ignores it.
6. **Purge** the old frontend from CDN / edge cache so no client keeps sending `body.idempotency_key`
   (now a 422) or omitting the `Idempotency-Key` header (now a 422 on executable writes).
7. **Only then** re-open executable traffic.
8. `automation_enabled` stays **OFF**; **no** provider live-smoke is part of this migration.

## Why the drain, restated

`alias guard` = at execute time, a new `v1:review:<id>` / `v1:decision:<id>` claim is refused (409,
0 dispatch) if any committed legacy row for that provable identity exists — regardless of status. That
closes cross-protocol double-dispatch for *saved* rows. It does **not** cover a legacy request that is
mid-flight during the deploy; only the drain does.
