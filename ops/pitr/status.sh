#!/bin/sh
# SECURITY-2D-3E1B-3B1 — machine-readable PITR status (STRICT allowlist output).
# Explicitly separates LOCAL spool acceptance (same-VDS backlog, informational only) from
# OFFSITE (S3 repository) durability. A foreground archive-push exit=0 or a non-empty local
# spool NEVER by themselves yield continuity=intact — that requires CONFIRMED remote continuity.
# Emits only non-sensitive operational signals; NEVER env/credentials/endpoint/SQL/object-listing/
# PII. Alerts/thresholds are NOT wired here (3C). Every network probe is timeout-bounded so an S3
# outage degrades the signal instead of hanging the caller.
set -eu
STANZA="${PGBACKREST_STANZA:-pult}"
PGWAL="${PGWAL_DIR:-/var/lib/postgresql/data/pg_wal}"
SPOOL="${PGBACKREST_SPOOL:-/var/spool/pgbackrest}"
TMO="${PITR_STATUS_TIMEOUT:-15}"

# Remote repository probes (S3) — timeout-bounded; failure => degraded signal, never a hang.
INFO="$(timeout "$TMO" pgbackrest --stanza="$STANZA" --output=json info 2>/dev/null || echo '[]')"
if timeout "$TMO" pgbackrest --stanza="$STANZA" check >/dev/null 2>&1; then check_status=ok; else check_status=failed; fi

# OFFSITE (in-repository) WAL range — what S3 actually holds. Empty when the repo is unreachable.
offsite_min="$(printf '%s' "$INFO" | sed -n 's/.*"archive":\[[^]]*"min":"\([0-9A-F]*\)".*/\1/p' | head -1)"
offsite_max="$(printf '%s' "$INFO" | sed -n 's/.*"archive":\[[^]]*"max":"\([0-9A-F]*\)".*/\1/p' | head -1)"
last_backup="$(printf '%s' "$INFO" | sed -n 's/.*"backup".*"timestamp".*"stop":\([0-9]*\).*/\1/p' | tail -1)"

# LOCAL signals (same-VDS backlog) — INFORMATIONAL ONLY; never imply offsite durability.
pgwal_bytes="$(du -sb "$PGWAL" 2>/dev/null | awk '{print $1}' || echo 0)"
spool_bytes="$(du -sb "$SPOOL" 2>/dev/null | awk '{print $1}' || echo 0)"

# archive_lag STATUS (not a raw count that could leak topology): ok when the repo is reachable AND
# holds WAL; lagging when a local backlog exists but the repo is unconfirmed; unknown otherwise.
if [ "$check_status" = ok ] && [ -n "$offsite_max" ]; then archive_lag=ok
elif [ "${spool_bytes:-0}" -gt 0 ] 2>/dev/null && [ "$check_status" != ok ]; then archive_lag=lagging
else archive_lag=unknown; fi

# continuity is INTACT only on CONFIRMED remote continuity (check ok AND offsite WAL present).
if [ "$check_status" = ok ] && [ -n "$offsite_max" ]; then continuity=intact; else continuity=unknown; fi

# Allowlisted output only.
printf 'pitr_check_status=%s\n' "$check_status"
printf 'pitr_last_base_backup_epoch=%s\n' "${last_backup:-unknown}"
printf 'pitr_offsite_archive_min=%s\n' "${offsite_min:-unknown}"
printf 'pitr_offsite_archive_max=%s\n' "${offsite_max:-unknown}"
printf 'pitr_archive_lag=%s\n' "$archive_lag"
printf 'pitr_pg_wal_bytes=%s\n' "${pgwal_bytes:-0}"
printf 'pitr_local_spool_bytes=%s\n' "${spool_bytes:-0}"
printf 'pitr_continuity=%s\n' "$continuity"
