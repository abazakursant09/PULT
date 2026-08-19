#!/bin/sh
# SECURITY-2D-3E1B-3C3-A — DORMANT full/differential backup runner (fail-closed; nothing runs by default).
# Usage (only at Inal-approved activation): PITR_SCHEDULER_ENABLED=1 run-backup.sh full|diff
. "$(dirname "$0")/_lib.sh"
pitr_require_dormant_gate
pitr_lock "/var/lock/pitr-backup.lock"
STANZA="${PGBACKREST_STANZA:-pult}"
TYPE="${1:-}"
case "$TYPE" in
    full|diff) : ;;
    *) pitr_log "pitr_backup=refuse reason=bad_type"; exit 2 ;;
esac
# Single foreground, bounded, no auto-retry loop. expire (deletion) is NOT done here (Role C only).
if pitr_bounded pgbackrest --stanza="$STANZA" --type="$TYPE" backup; then
    pitr_log "pitr_backup=ok type=$TYPE"
else
    pitr_log "pitr_backup=failed type=$TYPE"; exit 1
fi
