#!/bin/sh
# SECURITY-2D-3E1B-3B1 — machine-readable PITR status (STRICT allowlist output). Prints only
# non-sensitive operational signals; NEVER env/credentials/SQL/object-listing/PII. Alerts are
# NOT wired here (3C). Thresholds are NOT hardcoded — 3C sets them from measured volume/WAL rate.
set -eu
STANZA="${PGBACKREST_STANZA:-pult}"
PGWAL="${PGWAL_DIR:-/var/lib/postgresql/data/pg_wal}"
SPOOL="${PGBACKREST_SPOOL:-/var/spool/pgbackrest}"

# pgbackrest info as JSON is parsed for only allowlisted numeric/status fields.
INFO="$(pgbackrest --stanza="$STANZA" --output=json info 2>/dev/null || echo '[]')"
last_backup="$(printf '%s' "$INFO" | sed -n 's/.*"backup".*"timestamp".*"stop":\([0-9]*\).*/\1/p' | tail -1)"
check_status="$(pgbackrest --stanza="$STANZA" check >/dev/null 2>&1 && echo ok || echo failed)"
pgwal_bytes="$(du -sb "$PGWAL" 2>/dev/null | awk '{print $1}' || echo 0)"
spool_bytes="$(du -sb "$SPOOL" 2>/dev/null | awk '{print $1}' || echo 0)"

# Allowlisted output only.
printf 'pitr_check_status=%s\n' "$check_status"
printf 'pitr_last_base_backup_epoch=%s\n' "${last_backup:-unknown}"
printf 'pitr_pg_wal_bytes=%s\n' "${pgwal_bytes:-0}"
printf 'pitr_spool_bytes=%s\n' "${spool_bytes:-0}"
printf 'pitr_continuity=%s\n' "$([ "$check_status" = ok ] && echo intact || echo unknown)"
