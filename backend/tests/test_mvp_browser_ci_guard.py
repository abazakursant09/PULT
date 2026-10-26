"""Offline contracts for the synthetic, no-credentials browser workflow."""

from __future__ import annotations

from pathlib import Path
import re
import shlex

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github/workflows/mvp_browser_e2e.yml"
RUNNER = REPO / "frontend/e2e/run-ci.sh"
SCENARIO_COMMAND = (
    "npm run test:e2e -- e2e/mvp-path.spec.ts --project=chromium --retries=0 "
    "--forbid-only --global-timeout=600000 --trace=off"
)


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
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == "15"
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
    }
    assert steps["Run MVP browser"] == {
        "name": "Run MVP browser", "working-directory": "frontend", "run": "bash e2e/run-ci.sh",
    }
    assert all("continue-on-error" not in step and "if" not in step for step in steps.values())
    parsed_code = repr(_workflow())
    assert not re.search(r"secrets\.|upload-artifact|pull_request_target|self-hosted|https?://", parsed_code)


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
    assert "for port in (8099, 3099):" in child
    assert 'probe.connect_ex(("127.0.0.1", port)) != 0' in child
    assert "|| true" not in script and "exit 0" not in script
