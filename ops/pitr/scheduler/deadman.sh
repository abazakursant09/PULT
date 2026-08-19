#!/bin/sh
# SECURITY-2D-3E1B-3C3-A — DORMANT dead-man switch. FAIL-CLOSED: the ABSENCE of a fresh success is itself
# the alarm. Evaluates health, and on any alarm routes an allowlisted signal to the (log-only) notifier.
. "$(dirname "$0")/_lib.sh"
pitr_require_dormant_gate
D="$(dirname "$0")"
if sh "$D/health.sh"; then
    pitr_log "pitr_deadman=ok"
else
    sh "$D/notify.sh" emergency pitr_health_alarm || true
    pitr_log "pitr_deadman=alarm"
    exit 1
fi
