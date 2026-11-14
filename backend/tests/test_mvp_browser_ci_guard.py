"""Offline contracts for the synthetic, no-credentials browser workflow."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from textwrap import dedent

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github/workflows/mvp_browser_e2e.yml"
RUNNER = REPO / "frontend/e2e/run-ci.sh"
SCENARIO_COMMAND = (
    "npm run test:e2e -- e2e/mvp-path.spec.ts --project=chromium --retries=0 "
    "--forbid-only --global-timeout=600000 --trace=off"
)
MIRROR_STEP = "Use official Ubuntu package mirrors"
MIRROR_COMMAND = dedent(r"""
    set -euo pipefail
    # runner-images uses one priority-tagged mirror list for all Ubuntu pockets.
    # Select a reachable complete HTTPS archive; a single mirror is not a gate dependency.
    # Ubuntu signing keys, suites, TLS verification and all tests stay unchanged.
    die() { printf '%s\n' "Ubuntu mirror preflight: $1" >&2; exit 1; }
    test -f /etc/apt/apt-mirrors.txt && test ! -L /etc/apt/apt-mirrors.txt && test -s /etc/apt/apt-mirrors.txt || die 'mirror list missing or unsafe'
    test -f /etc/apt/sources.list.d/ubuntu.sources && test ! -L /etc/apt/sources.list.d/ubuntu.sources || die 'unsupported sources layout'
    grep -Fq 'mirror+file:/etc/apt/apt-mirrors.txt' /etc/apt/sources.list.d/ubuntu.sources || die 'sources do not use the mirror list'
    grep -Eq '^https?://(azure\.archive|archive|security)\.ubuntu\.com/ubuntu/([[:space:]]+priority:[0-9]+)?[[:space:]]*$|^https://mirrors\.edge\.kernel\.org/ubuntu/[[:space:]]*$' /etc/apt/apt-mirrors.txt || die 'unrecognized mirror list'
    mirror_base=''
    for mirror_candidate in https://archive.ubuntu.com/ubuntu/ https://mirrors.edge.kernel.org/ubuntu/; do
      mirror_host="${mirror_candidate#https://}"
      mirror_host="${mirror_host%%/*}"
      if ! getent ahosts "$mirror_host" >/dev/null; then continue; fi
      mirror_ready=1
      for mirror_suite in noble noble-updates noble-backports noble-security; do
        if ! curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
          --connect-timeout 10 --max-time 30 --max-filesize 2097152 --output /dev/null \
          "${mirror_candidate}dists/$mirror_suite/InRelease"; then
          mirror_ready=0
          break
        fi
      done
      if [ "$mirror_ready" = 1 ]; then mirror_base="$mirror_candidate"; break; fi
    done
    [ -n "$mirror_base" ] || die 'no trusted HTTPS mirror available'
    printf '%s\n' "$mirror_base" | sudo tee /etc/apt/apt-mirrors.txt >/dev/null
""").lstrip("\n")


def _workflow():
    assert WORKFLOW.is_file(), "browser workflow missing"
    # BaseLoader preserves GitHub's 'on' key rather than YAML 1.1's boolean alias.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _job():
    workflow = _workflow()
    assert set(workflow["jobs"]) == {"mvp-browser-e2e"}
    return workflow["jobs"]["mvp-browser-e2e"]


def _steps():
    steps = _job()["steps"]
    names = [step["name"] for step in steps]
    assert len(names) == len(set(names)), "duplicate step names"
    return dict(zip(names, steps, strict=True))


def _script():
    assert RUNNER.is_file(), "namespace runner missing"
    return RUNNER.read_text(encoding="utf-8")


def _assert_no_external_product_inputs(workflow):
    # Only the complete, reviewed package-setup step may contain repository URLs.
    # Keep credential and privileged-execution bans across the entire workflow.
    assert not re.search(r"secrets\.|upload-artifact|pull_request_target|self-hosted", repr(workflow))
    steps = workflow["jobs"]["mvp-browser-e2e"]["steps"]
    mirrors = [step for step in steps if step.get("name") == MIRROR_STEP]
    assert len(mirrors) == 1
    assert mirrors[0] == {"name": MIRROR_STEP, "run": MIRROR_COMMAND}
    without_mirror = deepcopy(workflow)
    without_mirror["jobs"]["mvp-browser-e2e"]["steps"] = [
        step for step in without_mirror["jobs"]["mvp-browser-e2e"]["steps"]
        if step.get("name") != MIRROR_STEP
    ]
    assert not re.search(r"https?://", repr(without_mirror))


def test_browser_workflow_triggers_and_permissions_are_narrow():
    workflow = _workflow()
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    for event in ("pull_request", "push"):
        assert workflow["on"][event]["branches"] == ["master"]
        assert set(workflow["on"][event]["paths"]) == {
            "backend/**", "frontend/**", ".github/workflows/mvp_browser_e2e.yml",
        }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "mvp-browser-e2e-${{ github.ref }}", "cancel-in-progress": "true",
    }
    job = _job()
    # Both independently bounded SQLite and PostgreSQL suites share this job.
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == "30"
    assert job["defaults"] == {"run": {"shell": "bash"}}
    assert not ({"if", "continue-on-error", "permissions", "environment", "services"} & job.keys())


def test_browser_workflow_uses_only_existing_pinned_setup_actions_and_locks():
    steps = _steps()
    expected_actions = {
        "Checkout": "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "Set up Python": "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "Set up Node": "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
    }
    assert {name: step["uses"] for name, step in steps.items() if "uses" in step} == expected_actions
    assert steps["Checkout"]["with"] == {"persist-credentials": "false"}
    assert steps["Set up Python"]["with"] == {"python-version": "3.13"}
    assert steps["Set up Node"]["with"] == {"node-version": "22"}
    for name, directory, command in (
        ("Install locked backend dependencies", "backend", "pip install --require-hashes -r requirements-ci.lock"),
        ("Install locked frontend dependencies", "frontend", "npm ci"),
        ("Install Chromium", "frontend", "npx --no-install playwright install --with-deps chromium"),
    ):
        assert steps[name] == {"name": name, "working-directory": directory, "run": command}


def test_browser_workflow_runs_the_namespace_runner_without_failure_masking():
    steps = _steps()
    assert set(steps) == {
        "Checkout", "Set up Python", "Set up Node", "Install locked backend dependencies",
        "Install locked frontend dependencies", "Install Chromium", "Prepare disposable database", "Run MVP browser",
        MIRROR_STEP,
        "Prepare native PostgreSQL tools and disposable root",
        "Run PostgreSQL browser, native restore and session acceptance",
    }
    assert steps["Run MVP browser"] == {
        "name": "Run MVP browser", "working-directory": "frontend", "run": "bash e2e/run-ci.sh",
    }
    assert all("continue-on-error" not in step and "if" not in step for step in steps.values())
    _assert_no_external_product_inputs(_workflow())


def test_browser_mirror_setup_is_exact_and_precedes_chromium_install():
    _assert_no_external_product_inputs(_workflow())
    names = list(_steps())
    assert names.index("Install locked frontend dependencies") < names.index(MIRROR_STEP)
    assert names.index(MIRROR_STEP) < names.index("Install Chromium")


@pytest.mark.parametrize("mutation", [
    "product_url", "secret_elsewhere", "secret_in_mirror", "extra_mirror_command",
    "extra_mirror_setting", "duplicate_mirror",
])
def test_browser_mirror_exception_rejects_unreviewed_inputs(mutation):
    workflow = _workflow()
    steps = workflow["jobs"]["mvp-browser-e2e"]["steps"]
    mirror = next(step for step in steps if step["name"] == MIRROR_STEP)
    if mutation == "product_url":
        workflow["jobs"]["mvp-browser-e2e"]["env"]["PRODUCT_URL"] = "https://product.invalid"
    elif mutation == "secret_elsewhere":
        workflow["jobs"]["mvp-browser-e2e"]["env"]["TOKEN"] = "${{ secrets.PRODUCT_TOKEN }}"
    elif mutation == "secret_in_mirror":
        mirror["run"] += "echo '${{ secrets.PRODUCT_TOKEN }}'\n"
    elif mutation == "extra_mirror_command":
        mirror["run"] += "curl https://unreviewed.invalid\n"
    elif mutation == "extra_mirror_setting":
        mirror["env"] = {"EXTRA": "unreviewed"}
    else:
        steps.append(deepcopy(mirror))
    with pytest.raises(AssertionError):
        _assert_no_external_product_inputs(workflow)


def test_browser_workflow_prepares_fresh_run_local_database():
    assert _job()["env"] == {
        "CI": "true", "NEXT_TELEMETRY_DISABLED": "1", "DO_NOT_TRACK": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert _steps()["Prepare disposable database"] == {
        "name": "Prepare disposable database",
        "run": 'E2E_ROOT="$(mktemp -d "${RUNNER_TEMP%/}/pult-mvp-e2e.XXXXXXXX")"\n'
               'mkdir "$E2E_ROOT/data"\n'
               'echo "E2E_DB_PATH=$E2E_ROOT/data/e2e.sqlite" >> "$GITHUB_ENV"\n',
    }
    script = _script()
    for required in (
        'root.parent == Path(os.environ["RUNNER_TEMP"]).resolve()',
        'root.name.startswith("pult-mvp-e2e.")',
        'db.parent.name == "data" and db.name == "e2e.sqlite" and not db.exists()',
    ):
        assert required in script


def test_browser_runner_scrubs_environment_and_disables_external_features():
    script = _script().replace("\\\n", " ")
    env_block = script.split("exec sudo -n /usr/bin/env -i ", 1)[1].split("/usr/bin/unshare", 1)[0]
    assert set(shlex.split(env_block)) == {
        "PATH=$PATH", "HOME=$HOME", "LANG=C.UTF-8", "LD_LIBRARY_PATH=$PYTHON_LIBDIR",
        "E2E_DB_PATH=$E2E_DB_PATH", "CI=true",
        "RUNNER_TEMP=$RUNNER_TEMP", "PULT_E2E_SUITE=$PULT_E2E_SUITE",
        "PULT_E2E_PG_ROOT=${PULT_E2E_PG_ROOT:-}", "PULT_E2E_PG_BIN=${PULT_E2E_PG_BIN:-}",
        "APP_ENV=development", "SMTP_HOST=", "SMTP_USER=", "SMTP_PASSWORD=", "SENTRY_DSN=", "CRED_ENC_KEY=",
        "BILLING_ENABLED=false", "NEXT_PUBLIC_BILLING_ENABLED=false", "API_DATA_SYNC_ENABLED=false",
        "AUTOMATION_ENABLED=false", "NEXT_TELEMETRY_DISABLED=1", "DO_NOT_TRACK=1",
        "PYTHONDONTWRITEBYTECODE=1", "NPM_CONFIG_OFFLINE=true",
    }
    assert 'sysconfig.get_config_var("LIBDIR")' in _script()
    assert 'test -d "$PYTHON_LIBDIR"' in _script()


def test_browser_runner_confines_all_children_and_drops_privileges():
    script = _script()
    for required in (
        "/usr/bin/unshare --net --", "/usr/sbin/ip link set lo up",
        'test "$(/usr/sbin/ip -o link show | /usr/bin/wc -l)" -eq 1',
        'exec /usr/bin/setpriv --reuid="$1" --regid="$2" --clear-groups',
        "--bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs",
        'assert interfaces == ["lo"]', 'assert os.getuid() != 0',
        'probe.connect_ex(("192.0.2.1", 9)) == errno.ENETUNREACH',
    ):
        assert required in script
    assert not re.search(r"\b(curl|wget|nsenter|docker)\b", script)


def test_browser_runner_preserves_failure_and_checks_shutdown_inside_namespace():
    script = _script()
    assert script.startswith("#!/usr/bin/env bash\n") and "set -euo pipefail\n" in script
    child = script.split("<<'E2E_SCRIPT'\n", 1)[1]
    assert child.endswith('exit "$result"\nE2E_SCRIPT\n')
    assert child.count(SCENARIO_COMMAND) == 1
    assert SCENARIO_COMMAND + " </dev/null || result=$?\n" in child
    assert "python - <<'PY' || result=1\n" in child
    assert "for port in (8099, 3099, 8097, 3097, 55432):" in child
    assert 'probe.connect_ex(("127.0.0.1", port)) != 0' in child
    assert "|| true" not in script and "exit 0" not in script


def test_browser_workflow_prepares_only_native_tools_and_fresh_owned_pg_root():
    steps = _steps()
    name = "Prepare native PostgreSQL tools and disposable root"
    assert steps[name] == {
        "name": name,
        "run": 'if [ ! -x /usr/lib/postgresql/16/bin/initdb ]; then\n'
               '  sudo apt-get update\n'
               '  sudo apt-get install -y postgresql-16\n'
               'fi\n'
               'echo "PULT_E2E_PG_BIN=/usr/lib/postgresql/16/bin" >> "$GITHUB_ENV"\n'
               'PG_E2E_ROOT="$(mktemp -d "${RUNNER_TEMP%/}/pult-pg-e2e.XXXXXXXX")"\n'
               'echo "PULT_E2E_PG_ROOT=$PG_E2E_ROOT" >> "$GITHUB_ENV"\n',
    }
    name = "Run PostgreSQL browser, native restore and session acceptance"
    assert steps[name] == {
        "name": name, "working-directory": "frontend",
        "env": {"PULT_E2E_SUITE": "postgres"}, "run": "bash e2e/run-ci.sh",
    }
    assert list(steps).index("Run MVP browser") < list(steps).index(name)


def test_browser_postgres_branch_keeps_failure_capture_and_owned_cluster_cleanup():
    script = _script()
    assert 'PULT_E2E_SUITE="${PULT_E2E_SUITE:-sqlite}"' in script
    assert 'case "$PULT_E2E_SUITE" in sqlite|postgres) ;; *) exit 2 ;; esac' in script
    child = script.split("<<'E2E_SCRIPT'\n", 1)[1]
    branch = child.split('if [ "$PULT_E2E_SUITE" = postgres ]; then\n', 1)[1].split('\nelse\n', 1)[0]
    assert 'python e2e/pg-database.py init\n' in branch
    assert 'python e2e/pg-database.py create\n' in branch
    flat = re.sub(r"\s+", " ", branch.replace("\\\n", " "))
    command = (
        "npm run test:e2e -- --config=e2e/pg-playwright.config.ts --project=chromium --retries=0 "
        "--forbid-only --global-timeout=900000 --trace=off </dev/null || result=$?"
    )
    assert flat.count(command) == 1
    assert '--auth-host=scram-sha-256 --auth-local=trust --pwfile="$PULT_E2E_PG_ROOT/password.txt"' in flat
    assert '-o "-h 127.0.0.1 -p 55432 -c unix_socket_directories=\'\'" -w start' in flat
    assert '"$PULT_E2E_PG_BIN/pg_ctl" -D "$PULT_E2E_PG_ROOT/data" -m fast -w stop' in branch
    assert branch.index('trap stop_pg EXIT\n') < branch.index(command.split(' --project=', 1)[0])
    assert branch.endswith('  stop_pg\n  trap - EXIT')


def _run_mirror_preflight(tmp_path, scenario, *, unsafe=None):
    """Execute the real shell block with DNS/curl/sudo stubbed; never touch /etc/network."""
    tools = tmp_path / "bin"
    tools.mkdir()
    mirrors, sources, trace = tmp_path / "mirrors", tmp_path / "sources", tmp_path / "trace"
    mirrors.write_text("http://azure.archive.ubuntu.com/ubuntu/ priority:1\n", encoding="utf-8", newline="\n")
    sources.write_text("Types: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt\n"
                       "Suites: noble noble-updates noble-backports noble-security\n"
                       "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n", encoding="utf-8", newline="\n")
    if unsafe == "empty":
        mirrors.write_text("", encoding="utf-8", newline="\n")
    elif unsafe == "unknown":
        mirrors.write_text("https://unreviewed.invalid/ubuntu/\n", encoding="utf-8", newline="\n")
    elif unsafe == "layout":
        sources.write_text("URIs: https://unreviewed.invalid/\n", encoding="utf-8", newline="\n")
    elif unsafe == "already_selected":
        mirrors.write_text("https://archive.ubuntu.com/ubuntu/\n", encoding="utf-8", newline="\n")
    before_mirror, before_source = mirrors.read_bytes(), sources.read_bytes()
    scripts = {
        "getent": r'''printf 'dns:%s\n' "$*" >> "$MIRROR_TRACE"
[ "$#" = 2 ] && [ "$1" = ahosts ] || exit 91
case "$2" in archive.ubuntu.com|mirrors.edge.kernel.org) ;; *) exit 91;; esac
if [ "$MIRROR_SCENARIO" = all_dns_down ]; then exit 2; fi
if [ "$MIRROR_SCENARIO" = primary_dns_down ] && [ "$2" = archive.ubuntu.com ]; then exit 2; fi
exit 0''',
        "curl": r'''printf 'curl:%s\n' "$*" >> "$MIRROR_TRACE"
url="${!#}"
expected=(--fail --silent --show-error --location --proto '=https' --proto-redir '=https'
          --connect-timeout 10 --max-time 30 --max-filesize 2097152 --output /dev/null "$url")
[ "$*" = "${expected[*]}" ] || exit 91
case "$url" in
  https://archive.ubuntu.com/ubuntu/dists/*/InRelease) mirror_role=primary;;
  https://mirrors.edge.kernel.org/ubuntu/dists/*/InRelease) mirror_role=secondary;;
  *) exit 91;;
esac
case "$MIRROR_SCENARIO:$mirror_role" in all_down:*|kernel_down:secondary|primary_down:primary) exit 28;; esac
if [ "$MIRROR_SCENARIO" = primary_partial ] && [[ "$url" = *noble-security* ]] && [ "$mirror_role" = primary ]; then exit 22; fi
exit 0''',
        "grep": r'''args=("$@")
last=$((${#args[@]}-1))
case "${args[$last]}" in
  /etc/apt/apt-mirrors.txt) args[$last]="$MIRROR_FILE";;
  /etc/apt/sources.list.d/ubuntu.sources) args[$last]="$MIRROR_SOURCES";;
  *) exit 91;;
esac
exec /usr/bin/grep "${args[@]}"''',
        "sudo": r'''[ "$#" = 2 ] && [ "$1" = tee ] && [ "$2" = /etc/apt/apt-mirrors.txt ] || exit 91
printf 'write\n' >> "$MIRROR_TRACE"
[ "$MIRROR_SCENARIO" != write_failure ] || exit 1
exec /usr/bin/tee "$MIRROR_FILE"''',
    }
    for name, script in scripts.items():
        path = tools / name
        path.write_bytes(("#!/bin/bash\nset -euo pipefail\n" + script + "\n").encode("utf-8"))
        path.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key in {"SystemRoot", "WINDIR", "TEMP", "TMP"}}
    env.update(MIRROR_FIXTURE_BIN=tools.as_posix(), MIRROR_FILE=mirrors.as_posix(),
               MIRROR_SOURCES=sources.as_posix(), MIRROR_TRACE=trace.as_posix(), MIRROR_SCENARIO=scenario,
               MIRROR_UNSAFE=unsafe or "", PATH="/usr/bin:/bin")
    convert = (r'''export MIRROR_FIXTURE_BIN="$(cygpath -u "$MIRROR_FIXTURE_BIN")"
export MIRROR_FILE="$(cygpath -u "$MIRROR_FILE")"
export MIRROR_SOURCES="$(cygpath -u "$MIRROR_SOURCES")"
export MIRROR_TRACE="$(cygpath -u "$MIRROR_TRACE")"
''' if os.name == "nt" else "")
    prefix = r'''test() {
  local args=("$@")
  local last=$((${#args[@]}-1))
  case "${args[$last]}" in
    /etc/apt/apt-mirrors.txt) args[$last]="$MIRROR_FILE";;
    /etc/apt/sources.list.d/ubuntu.sources) args[$last]="$MIRROR_SOURCES";;
  esac
  builtin test "${args[@]}"
}
export PATH="$MIRROR_FIXTURE_BIN"
'''
    if unsafe == "link":
        prefix = prefix.replace('builtin test "${args[@]}"',
                                'if [[ "$*" = "! -L /etc/apt/apt-mirrors.txt" ]]; then return 1; fi\n  builtin test "${args[@]}"')
    shell = "C:/Program Files/Git/usr/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    assert shell, "bash is required for this executable contract"
    result = subprocess.run([shell, "-c", convert + prefix + _steps()[MIRROR_STEP]["run"]],
                            env=env, capture_output=True, text=True, timeout=20)
    assert sources.read_bytes() == before_source  # Suites/keyring/source trust config untouched.
    return result, mirrors.read_bytes().decode("utf-8"), trace.read_text(encoding="utf-8") if trace.exists() else "", before_mirror.decode("utf-8")


def test_mirror_preflight_works_when_old_single_mirror_times_out(tmp_path):
    result, mirrors, trace, _ = _run_mirror_preflight(tmp_path, "kernel_down")
    assert result.returncode == 0, result.stderr
    assert mirrors == "https://archive.ubuntu.com/ubuntu/\n"
    assert "https://mirrors.edge.kernel.org/ubuntu/" not in trace
    for suite in ("noble", "noble-updates", "noble-backports", "noble-security"):
        assert f"https://archive.ubuntu.com/ubuntu/dists/{suite}/InRelease" in trace


@pytest.mark.parametrize("scenario", ["primary_down", "primary_dns_down", "primary_partial"])
def test_mirror_preflight_uses_secondary_only_after_primary_failure(tmp_path, scenario):
    result, mirrors, trace, _ = _run_mirror_preflight(tmp_path, scenario)
    assert result.returncode == 0, result.stderr
    assert mirrors == "https://mirrors.edge.kernel.org/ubuntu/\n"
    assert trace.splitlines().count("write") == 1
    for suite in ("noble", "noble-updates", "noble-backports", "noble-security"):
        assert f"https://mirrors.edge.kernel.org/ubuntu/dists/{suite}/InRelease" in trace
    if scenario == "primary_dns_down":
        assert "https://archive.ubuntu.com/ubuntu/dists/" not in trace


@pytest.mark.parametrize("scenario", ["all_down", "all_dns_down"])
def test_mirror_preflight_fails_closed_without_any_complete_archive(tmp_path, scenario):
    result, mirrors, trace, before = _run_mirror_preflight(tmp_path, scenario)
    assert result.returncode != 0 and "no trusted HTTPS mirror available" in result.stderr
    assert mirrors == before and "write" not in trace.splitlines()
    if scenario == "all_dns_down":
        assert "curl:" not in trace


@pytest.mark.parametrize("unsafe", ["empty", "unknown", "layout", "link"])
def test_mirror_preflight_rejects_unsafe_layout_before_network_or_write(tmp_path, unsafe):
    result, mirrors, trace, before = _run_mirror_preflight(tmp_path, "healthy", unsafe=unsafe)
    assert result.returncode != 0
    assert mirrors == before and trace == ""


def test_mirror_preflight_is_idempotent_and_does_not_hide_write_failure(tmp_path):
    result, mirrors, trace, before = _run_mirror_preflight(tmp_path, "healthy", unsafe="already_selected")
    assert result.returncode == 0 and mirrors == before
    assert "mirrors.edge.kernel.org" not in trace


def test_mirror_preflight_write_error_remains_a_failed_gate(tmp_path):
    result, mirrors, trace, before = _run_mirror_preflight(tmp_path, "write_failure")
    assert result.returncode != 0 and mirrors == before
    assert trace.splitlines().count("write") == 1


def test_mirror_selection_preserves_tls_bounds_and_apt_authentication():
    script = _steps()[MIRROR_STEP]["run"]
    assert "--proto '=https' --proto-redir '=https'" in script
    assert "--connect-timeout 10 --max-time 30 --max-filesize 2097152" in script
    for forbidden in ("--insecure", "--retry", "trusted=yes", "AllowInsecure", "AllowUnauthenticated",
                      "allow-unauthenticated", "apt-key", "Signed-By:", "Check-Valid-Until=false"):
        assert forbidden not in script
    assert script.count("sudo tee") == 1
    assert '"$mirror_base" | sudo tee /etc/apt/apt-mirrors.txt' in script
