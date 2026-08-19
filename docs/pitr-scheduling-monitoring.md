# PITR scheduling + monitoring — DORMANT foundation (SECURITY-2D-3E1B-3C3-A)

Status: **DORMANT. Nothing is scheduled, monitored, or alerting.** This unit only *delivers* host-level
scheduler/monitor templates and fail-closed scripts under `ops/pitr/scheduler/`. It activates nothing.
Base: master `24662baf501cf5afe98593b40bc3da1f8c2dd34e` (F6 complete, Selectel canary resources = 0,
Object Lock Governance PROVEN). See the activation state machine in `docs/pitr-operations-policy.md` §9
and the requirements in §10.

## What this is NOT (3C3-A)
Not an active backup schedule; not a running monitor; not a real alert channel; not a systemd/cron
install; not a production PITR enablement; not a purchased/configured server; no feature-flag change.

## What each future timer runs (all `.example`, all dormant)
| Unit template | Runs | Purpose |
|---|---|---|
| `pitr-full.{service,timer}.example` | `run-backup.sh full` | full base backup |
| `pitr-diff.{service,timer}.example` | `run-backup.sh diff` | differential backup |
| `pitr-wal-check.{service,timer}.example` | `wal-check.sh` → `../status.sh` | WAL continuity check (offsite-confirmed) |
| `pitr-retention.{service,timer}.example` | `retention-enforce.sh` | retention **dry-run by default** (real expire = Role C only) |
| `pitr-deadman.{service,timer}.example` | `deadman.sh` → `health.sh` → `notify.sh` | health + dead-man switch |

Units are `Type=oneshot`, hardened (`NoNewPrivileges`/`ProtectSystem=strict`/`ProtectHome`/`PrivateTmp`),
bounded (`TimeoutStartSec`), **no `Restart=`** (no auto-retry loop), UTC `OnCalendar`, and timers use
`Persistent=true` for missed-run/reboot handling. The single-instance `flock` in the scripts prevents
overlap. The `.example` suffix means systemd never loads them from the repo.

## How each failure is detected (`health.sh`, fail-closed)
- **Overdue backup** — `pitr_backup_health=alarm` unless `PITR_BACKUP_MAX_AGE_SEC` is a number AND the
  last base backup epoch (from `status.sh`) is within it.
- **Stopped WAL archive** — `pitr_wal_health=alarm` unless `status.sh` reports `archive_lag=ok` AND
  `continuity=intact` (offsite-confirmed).
- **Disk fill** — `pitr_disk_health=alarm` unless free %% on the PGDATA filesystem is measurable AND
  `>= PITR_DISK_MIN_FREE_PCT`.
- **Scheduler death (dead-man)** — `pitr_scheduler_health=alarm` unless a fresh success heartbeat
  (`PITR_HEARTBEAT_FILE`) exists within `PITR_DEADMAN_WINDOW_SEC`.
Any missing threshold or missing signal ⇒ **alarm**, never a silent pass. `pitr_overall_health=alarm`
if any component is alarm.

## Where the dead-man alert goes
**3C3-A sends NO real alert.** `notify.sh` is log-only: it prints an allowlisted
`pitr_alert severity=… signal=… transport=log-only` line and refuses (exit 5) if `ALERT_WEBHOOK` is set
to any real (non-placeholder, non-`.invalid`) endpoint. The real destination — an ops channel
(e.g. Telegram/e-mail/webhook off the application VDS) — is chosen and wired in a **separate,
Inal-approved** activation PR. Until then the channel is **UNKNOWN**.

## Parameters UNKNOWN until the server is bought/sized
Backup/diff cadence (`OnCalendar` are placeholders), `PITR_BACKUP_MAX_AGE_SEC`, `PITR_WAL_MAX_LAG_SEC`,
`PITR_DISK_MIN_FREE_PCT`, `PITR_DEADMAN_WINDOW_SEC`, retention period, disk reserve, and the alert
channel. All are `REPLACE` placeholders in `scheduler.env.example`; the scripts fail closed while unset.

## Why production stays OFF
Three independent stops, all required and none satisfied here: (1) the scripts refuse unless
`PITR_SCHEDULER_ENABLED=1` (default 0 in repo/CI); (2) the units are `.example` and are never copied to
`/etc/systemd/system/` or enabled; (3) activation follows `docs/pitr-operations-policy.md` §9
(Phases 0→4) and needs a purchased/hardened server, a real bucket + canary GREEN (done), delivered
secrets, measured disk reserve, a monitoring channel, and **explicit Inal approval per phase**. No
feature flag is touched.

## Activation runbook (future, Inal-approved only — do NOT run now)
1. On the hardened host, copy `ops/pitr/scheduler/*.service.example` / `*.timer.example` to
   `/etc/systemd/system/` (drop the `.example` suffix); copy `scheduler.env.example` to
   `/etc/pitr/scheduler.env` and fill the measured thresholds + `PITR_SCHEDULER_ENABLED=1`.
2. Wire the real alert transport in the separate activation PR (replaces the log-only stub).
3. `systemctl daemon-reload`; run each `*.service` once by hand and confirm allowlisted signals.
4. Only then `systemctl enable --now pitr-*.timer`.

## Rollback
`systemctl disable --now pitr-*.timer`; remove the unit files from `/etc/systemd/system/`;
`systemctl daemon-reload`. This stops all scheduling/monitoring. It does not touch the database, the
bucket, or any Selectel resource. Reverting the repo PR removes the templates entirely.

## Launch gate
Still **NOT READY**. 3C3-A delivers dormant units only; activation (3C3 enablement), plus 3C4/3C5,
remain future Inal-gated units.
