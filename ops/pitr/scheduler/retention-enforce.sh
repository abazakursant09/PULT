#!/bin/sh
# SECURITY-2D-3E1B-3C3-A — DORMANT retention enforcement, FAIL-CLOSED. Default = DRY-RUN only (reports
# what WOULD expire; deletes NOTHING). A real expire is the separate Role C (retention-admin) principal's
# job (NOT the application VDS) and requires an explicit confirm token; even then this PR neither runs it
# nor grants it. A governance retention bypass is NEVER used.
. "$(dirname "$0")/_lib.sh"
pitr_require_dormant_gate
pitr_lock "/var/lock/pitr-retention.lock"
STANZA="${PGBACKREST_STANZA:-pult}"
if [ "${PITR_RETENTION_ENFORCE:-dry-run}" = "confirm" ]; then
    # Role C only, at activation; guarded so it cannot fire by default. Never a bypass flag.
    pitr_log "pitr_retention=enforce-requested owner=role-c"
    if pitr_bounded pgbackrest --stanza="$STANZA" expire; then
        pitr_log "pitr_retention=enforced"
    else
        pitr_log "pitr_retention=failed"; exit 1
    fi
else
    if pitr_bounded pgbackrest --stanza="$STANZA" --dry-run expire; then
        pitr_log "pitr_retention=dry-run-ok"
    else
        pitr_log "pitr_retention=dry-run-failed"; exit 1
    fi
fi
