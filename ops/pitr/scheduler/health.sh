#!/bin/sh
# SECURITY-2D-3E1B-3C3-A — DORMANT health evaluator. Consumes wal-check/status.sh signals + a heartbeat,
# then classifies overdue-backup / stopped-WAL / disk-fill / scheduler-death. FAIL-CLOSED: any missing
# threshold or missing signal => NOT ok (alarm), never a silent pass. Thresholds come from placeholders
# in scheduler.env.example and are UNKNOWN until the server is sized. NO real alert is sent here.
. "$(dirname "$0")/_lib.sh"
pitr_require_dormant_gate
now="$(date -u +%s)"

_st="$(sh "$(dirname "$0")/wal-check.sh" 2>/dev/null || true)"
_get() { printf '%s\n' "$_st" | sed -n "s/^$1=//p" | head -1; }
continuity="$(_get pitr_continuity)"; [ -n "$continuity" ] || continuity=unknown
last_base="$(_get pitr_last_base_backup_epoch)"
archive_lag="$(_get pitr_archive_lag)"

# Fail-closed numeric reader: unset/non-numeric => empty => downstream classifies as alarm.
_num() { printf '%s' "${1:-}" | tr -cd '0-9'; }
max_age="$(_num "${PITR_BACKUP_MAX_AGE_SEC:-}")"
min_free_pct="$(_num "${PITR_DISK_MIN_FREE_PCT:-}")"
deadman_window="$(_num "${PITR_DEADMAN_WINDOW_SEC:-}")"
hb="${PITR_HEARTBEAT_FILE:-/var/lib/pitr/last-success.epoch}"

# overdue-backup: alarm unless numeric threshold AND a fresh-enough last base backup.
if [ -z "$max_age" ] || [ -z "$last_base" ] || [ "$last_base" = unknown ]; then backup_health=alarm
elif [ "$(( now - last_base ))" -gt "$max_age" ]; then backup_health=alarm
else backup_health=ok; fi

# stopped-WAL: alarm unless archive_lag==ok AND continuity==intact.
if [ "$archive_lag" = ok ] && [ "$continuity" = intact ]; then wal_health=ok; else wal_health=alarm; fi

# disk-fill: alarm unless free% measurable AND >= threshold.
free_pct="$(df -P /var/lib/postgresql 2>/dev/null | awk 'NR==2{gsub("%","",$5); print 100-$5}')"
if [ -z "$min_free_pct" ] || [ -z "$free_pct" ]; then disk_health=alarm
elif [ "$free_pct" -lt "$min_free_pct" ]; then disk_health=alarm
else disk_health=ok; fi

# scheduler-death (dead-man): alarm unless a fresh heartbeat exists within the window.
if [ -z "$deadman_window" ] || [ ! -f "$hb" ]; then sched_health=alarm
else
    hb_epoch="$(_num "$(cat "$hb" 2>/dev/null)")"
    if [ -z "$hb_epoch" ] || [ "$(( now - hb_epoch ))" -gt "$deadman_window" ]; then sched_health=alarm
    else sched_health=ok; fi
fi

if [ "$backup_health" = ok ] && [ "$wal_health" = ok ] && [ "$disk_health" = ok ] && [ "$sched_health" = ok ]; then
    overall=ok
else
    overall=alarm
fi

pitr_log "pitr_backup_health=$backup_health"
pitr_log "pitr_wal_health=$wal_health"
pitr_log "pitr_disk_health=$disk_health"
pitr_log "pitr_scheduler_health=$sched_health"
pitr_log "pitr_overall_health=$overall"
[ "$overall" = ok ] || exit 1
