#!/bin/sh
# SECURITY-2D-3E1B-3C3-A — DORMANT scheduler/monitor shared library (NON-SECRET; nothing activated).
# Every scheduler/monitor entrypoint sources this and calls pitr_require_dormant_gate FIRST, so an
# accidental invocation with the default environment refuses to run (fail-closed). No systemd unit here
# is installed/enabled; no cron is written; no real alert is ever sent. Output is a STRICT allowlist of
# non-sensitive operational signals — never env/credentials/endpoint/bucket/SQL/object-listing/PII.
set -eu
export TZ=UTC   # all timestamps/scheduling reasoning in UTC

# Fail-closed dormant gate: refuse unless the operator has EXPLICITLY opted in for THIS host at
# activation time (Phase 4, Inal-approved). Default (unset/0) => exit 3 "dormant". This PR ships the
# units/scripts dormant; it never sets this variable anywhere in the repo or CI.
pitr_require_dormant_gate() {
    if [ "${PITR_SCHEDULER_ENABLED:-0}" != "1" ]; then
        printf 'pitr_scheduler=dormant reason=PITR_SCHEDULER_ENABLED_not_1 action=refuse\n'
        exit 3
    fi
}

# Single-instance lock (no overlapping runs). Non-blocking: a busy lock => exit 4, never a pile-up.
pitr_lock() {
    _lock="${1:-/var/lock/pitr-scheduler.lock}"
    exec 9>"$_lock" 2>/dev/null || { printf 'pitr_lock=uncreatable action=refuse\n'; exit 4; }
    if command -v flock >/dev/null 2>&1; then
        flock -n 9 || { printf 'pitr_lock=busy action=refuse\n'; exit 4; }
    fi
}

# Bounded run of pgBackRest (or any cmd) — never hang a timer; a hang degrades to a failure signal.
pitr_bounded() {
    _tmo="${PITR_CMD_TIMEOUT:-900}"
    timeout "$_tmo" "$@"
}

# Allowlisted, secret-free log line to stdout (journald/file capture is configured at activation).
pitr_log() { printf '%s\n' "$*"; }
