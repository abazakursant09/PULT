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

# Publish only after a successful backup. The parent MUST be pre-provisioned,
# trusted and writable by the publisher, with appropriate access for the reader.
# These checks are not protection against an attacker who can replace parent paths.
# Subshell isolates umask, variables and cleanup traps from every caller. Explicit
# checks are required even when this helper is itself used as an `if` condition.
pitr_publish_heartbeat() (
    _hb="${PITR_HEARTBEAT_FILE:-/var/lib/pitr/last-success.epoch}"
    case "$_hb" in
        /*) : ;;
        *) return 1 ;;
    esac
    case "$_hb" in
        */|*//*|*/../*|*/./*) return 1 ;;
    esac
    _hb_name="${_hb##*/}"
    case "$_hb_name" in
        ''|.|..|*[!a-zA-Z0-9._-]*) return 1 ;;
    esac
    _hb_parent="${_hb%/*}"
    [ -n "$_hb_parent" ] || _hb_parent=/
    [ -d "$_hb_parent" ] || return 1
    [ ! -L "$_hb" ] || return 1
    if [ -e "$_hb" ] && [ ! -f "$_hb" ]; then return 1; fi

    _hb_epoch="$(date -u +%s 2>/dev/null)" || return 1
    # Canonical positive decimal Unix seconds, at most 10 digits (no arithmetic
    # evaluation, signs, whitespace, embedded records or octal interpretation).
    case "$_hb_epoch" in ''|0*|*[!0-9]*) return 1 ;; esac
    [ "${#_hb_epoch}" -le 10 ] || return 1

    umask 077
    _hb_tmp=''
    trap 'if [ -n "$_hb_tmp" ]; then rm -f "$_hb_tmp" >/dev/null 2>&1 || exit 1; fi' 0
    trap 'exit 1' HUP INT TERM
    _hb_tmp="$(mktemp "${_hb}.tmp.XXXXXX" 2>/dev/null)" || return 1
    [ -n "$_hb_tmp" ] || return 1
    printf '%s\n' "$_hb_epoch" >"$_hb_tmp" || return 1
    # Recheck supported target types immediately before same-directory rename.
    [ ! -L "$_hb" ] || return 1
    if [ -e "$_hb" ] && [ ! -f "$_hb" ]; then return 1; fi
    mv -f "$_hb_tmp" "$_hb" >/dev/null 2>&1 || return 1
    _hb_tmp=''
    return 0
)

# Allowlisted, secret-free log line to stdout (journald/file capture is configured at activation).
pitr_log() { printf '%s\n' "$*"; }
