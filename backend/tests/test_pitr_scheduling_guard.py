"""SECURITY-2D-3E1B-3C3-A — offline guard for the DORMANT PITR scheduling + monitoring foundation.

Pure structural checks over ops/pitr/scheduler/* + docs/pitr-scheduling-monitoring.md. No Docker, no
network, no systemd, no shell execution. Verifies: everything is dormant (fail-closed gate, .example
units, never enabled in-repo), fail-closed monitoring, retention dry-run by default, NO real alert
transport, NO Bypass, and NO secrets/real endpoints in any template or env example.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
SCHED = REPO / "ops" / "pitr" / "scheduler"
DOC = REPO / "docs" / "pitr-scheduling-monitoring.md"

LIB = SCHED / "_lib.sh"
SCRIPTS = ["_lib.sh", "run-backup.sh", "wal-check.sh", "health.sh", "notify.sh", "deadman.sh",
           "retention-enforce.sh"]
UNITS = ["pitr-full", "pitr-diff", "pitr-wal-check", "pitr-retention", "pitr-deadman"]
ENTRYPOINTS = ["run-backup.sh", "wal-check.sh", "health.sh", "notify.sh", "deadman.sh",
               "retention-enforce.sh"]


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_all_files_present():
    for s in SCRIPTS:
        assert (SCHED / s).is_file(), f"missing script {s}"
    for u in UNITS:
        assert (SCHED / f"{u}.service.example").is_file(), f"missing {u}.service.example"
        assert (SCHED / f"{u}.timer.example").is_file(), f"missing {u}.timer.example"
    assert (SCHED / "scheduler.env.example").is_file()
    assert DOC.is_file()


def test_units_are_example_suffixed_only():
    # systemd must never load a real unit from the repo: only *.example may exist here.
    for p in SCHED.glob("*.service"):
        raise AssertionError(f"non-example unit present: {p.name}")
    for p in SCHED.glob("*.timer"):
        raise AssertionError(f"non-example unit present: {p.name}")


def test_dormant_gate_is_fail_closed():
    lib = _r(LIB)
    assert 'PITR_SCHEDULER_ENABLED:-0' in lib and '!= "1"' in lib, "gate must default-off & require ==1"
    # line-anchored: an actual `exit 3` STATEMENT (not just a mention in a comment)
    assert re.search(r'(?m)^\s*exit 3\s*$', lib), "gate must refuse via a real exit 3 statement"
    assert "export TZ=UTC" in lib, "must reason in UTC"
    assert "flock -n 9" in lib, "must take a non-blocking single-instance lock"
    assert "timeout" in lib, "must bound external commands"
    # every entrypoint sources the lib AND calls the gate FIRST.
    for e in ENTRYPOINTS:
        body = _r(SCHED / e)
        assert '. "$(dirname "$0")/_lib.sh"' in body, f"{e} must source _lib.sh"
        assert "pitr_require_dormant_gate" in body, f"{e} must call the dormant gate"


def test_no_systemctl_or_cron_in_scripts():
    for s in SCRIPTS + ["scheduler.env.example"]:
        body = _r(SCHED / s)
        for bad in ("systemctl enable", "systemctl start", "systemctl daemon-reload", "crontab"):
            assert bad not in body, f"{s} must not self-activate ({bad!r})"


def test_units_dormant_oneshot_hardened_no_restart():
    for u in UNITS:
        svc = _r(SCHED / f"{u}.service.example")
        assert "DORMANT" in svc
        # line-anchored DIRECTIVES (a comment mention must not satisfy these)
        assert re.search(r'(?m)^Type=oneshot\s*$', svc), f"{u} service must be Type=oneshot"
        assert re.search(r'(?m)^TimeoutStartSec=\d+\s*$', svc), f"{u} service needs a bounded timeout"
        assert not re.search(r'(?m)^Restart=', svc), f"{u} service must have NO Restart= directive"
        assert re.search(r'(?m)^NoNewPrivileges=true\s*$', svc), "hardening required"
        assert re.search(r'(?m)^ProtectSystem=strict\s*$', svc), "hardening required"
        tmr = _r(SCHED / f"{u}.timer.example")
        assert "DORMANT" in tmr
        assert re.search(r'(?m)^Persistent=true\s*$', tmr), "timer needs missed-run handling"
        assert re.search(rf'(?m)^Unit={re.escape(u)}\.service\s*$', tmr), "timer must bind its own service"
        assert re.search(r'(?m)^OnCalendar=.*\bUTC\s*$', tmr), "UTC schedule required"


def test_retention_dry_run_default_and_no_bypass():
    r = _r(SCHED / "retention-enforce.sh")
    assert 'PITR_RETENTION_ENFORCE:-dry-run' in r, "retention must default to dry-run"
    assert '"confirm"' in r, "real expire must be gated behind an explicit confirm token"
    assert "--dry-run expire" in r, "dry-run path must actually be a dry-run"
    for f in SCRIPTS:
        assert "BypassGovernanceRetention" not in _r(SCHED / f)
        assert "--bypass" not in _r(SCHED / f)


def test_notify_is_log_only_no_real_transport():
    n = _r(SCHED / "notify.sh")
    assert "transport=log-only" in n
    assert "real_transport_not_allowed_in_3C3A" in n, "must refuse a real endpoint"
    for bad in ("curl", "wget", "http-post", "nc ", "requests.post"):
        assert bad not in n, f"notify.sh must not perform a real send ({bad!r})"


def test_health_fail_closed_defaults_to_alarm():
    h = _r(SCHED / "health.sh")
    assert "backup_health=alarm" in h and "wal_health=alarm" in h
    assert "disk_health=alarm" in h and "sched_health=alarm" in h
    assert "overall=alarm" in h
    # dead-man / overdue / disk each require a numeric threshold, else alarm.
    assert 'PITR_DEADMAN_WINDOW_SEC' in h and 'PITR_BACKUP_MAX_AGE_SEC' in h and 'PITR_DISK_MIN_FREE_PCT' in h


def test_env_example_placeholders_only_and_dormant():
    e = _r(SCHED / "scheduler.env.example")
    assert "PITR_SCHEDULER_ENABLED=0" in e, "env example must ship the gate OFF"
    assert "ALERT_WEBHOOK=REPLACE.invalid" in e, "alert transport must stay a placeholder"
    for ph in ("PITR_BACKUP_MAX_AGE_SEC=REPLACE", "PITR_DISK_MIN_FREE_PCT=REPLACE",
               "PITR_DEADMAN_WINDOW_SEC=REPLACE"):
        assert ph in e, f"threshold {ph} must remain a placeholder"


def test_no_secrets_or_real_endpoints_anywhere():
    for p in list(SCHED.iterdir()):
        if not p.is_file():
            continue
        body = _r(p)
        assert not re.search(r"AKIA[0-9A-Z]{16}", body), f"AWS key in {p.name}"
        assert "-----BEGIN" not in body, f"private key in {p.name}"
        # a real Selectel S3 endpoint must never be hard-wired here (only .invalid placeholders allowed)
        for m in re.findall(r"s3\.[a-z0-9-]+\.storage\.selcloud\.ru", body):
            raise AssertionError(f"real Selectel endpoint {m} in {p.name}")


def test_doc_has_activation_rollback_dormant_unknown():
    d = _r(DOC)
    for need in ("DORMANT", "Activation runbook", "Rollback", "UNKNOWN", "NOT READY",
                 "sends NO real alert"):
        assert need in d, f"runbook must document {need!r}"


def test_behavioral_gate_and_health_fail_closed():
    """Execution proof (skipped only if no POSIX sh): default => dormant exit 3; enabled without any
    threshold => overall alarm (never a silent ok). Catches a logic mutation that a text scan misses."""
    sh = shutil.which("sh")
    if not sh:
        import pytest
        pytest.skip("no POSIX sh available")
    # dormant by default -> exit 3, nothing runs
    for script in ("run-backup.sh", "health.sh", "deadman.sh", "retention-enforce.sh", "notify.sh"):
        r = subprocess.run([sh, str(SCHED / script)], capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin"})
        assert r.returncode == 3, f"{script} must refuse (exit 3) when dormant, got {r.returncode}"
        assert "dormant" in r.stdout
    # enabled but NO thresholds -> every health component alarms, overall alarm, exit 1
    env = {"PATH": "/usr/bin:/bin", "PITR_SCHEDULER_ENABLED": "1", "PGBACKREST_STANZA": "pult"}
    r = subprocess.run([sh, str(SCHED / "health.sh")], capture_output=True, text=True, env=env)
    assert r.returncode == 1, "health must fail-closed to alarm without thresholds"
    assert "pitr_overall_health=alarm" in r.stdout
    for comp in ("pitr_backup_health=alarm", "pitr_wal_health=alarm", "pitr_disk_health=alarm",
                 "pitr_scheduler_health=alarm"):
        assert comp in r.stdout, comp
    # notify with a REAL webhook is refused even when enabled
    r = subprocess.run([sh, str(SCHED / "notify.sh"), "warning", "x"], capture_output=True, text=True,
                       env={**env, "ALERT_WEBHOOK": "https://real.example.com/hook"})
    assert r.returncode == 5 and "refuse" in r.stdout, "notify must refuse a real transport"
