#!/bin/sh
# SECURITY-2D-3E1B-3C3-A — DORMANT WAL-continuity check wrapper around ops/pitr/status.sh.
# Emits status.sh's allowlisted signals; adds NOTHING sensitive. Alert dispatch is NOT wired here.
. "$(dirname "$0")/_lib.sh"
pitr_require_dormant_gate
STATUS="$(dirname "$0")/../status.sh"
if [ ! -f "$STATUS" ]; then pitr_log "pitr_walcheck=refuse reason=no_status_sh"; exit 2; fi
sh "$STATUS"
