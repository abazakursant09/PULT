"""No Docker locally: prove adapter script preservation and failure handling."""
import copy
import importlib.util
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("same_image", ROOT / "ops/pitr/run_same_image_suite.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
IMAGE = "sha256:" + "a" * 64


def workflow(suite):
    return yaml.safe_load((ROOT / f".github/workflows/pitr_{suite}.yml").read_text())


@pytest.mark.parametrize("suite", ["synthetic", "extended"])
def test_exact_matrix_preserved_except_immutable_image_reference(suite):
    doc = workflow(suite)
    scripts, env = m.plan(doc, suite, IMAGE)
    original = doc["jobs"]["pitr-" + suite]
    selected = [s for s in original["steps"] if s["name"] in m.SELECTED[suite]]
    assert [name for name, _ in scripts] == m.SELECTED[suite]
    assert len(scripts) == len(selected)
    for (_, body), step in zip(scripts, selected):
        assert body == step["run"].replace("pult-pitr:ci", IMAGE)
        assert "pult-pitr:ci" not in body
        assert "docker build" not in body and "docker pull" not in body
    assert env == original["env"]


@pytest.mark.parametrize("suite", ["synthetic", "extended"])
@pytest.mark.parametrize("change", ["delete", "extra", "if", "expression", "env", "order"])
def test_drift_requires_review(suite, change):
    doc = workflow(suite)
    job = doc["jobs"]["pitr-" + suite]
    if change == "delete":
        job["steps"].pop(-2)
    elif change == "extra":
        job["steps"].append(copy.deepcopy(job["steps"][-1]))
    elif change == "if":
        job["steps"][-2]["if"] = "false"
    elif change == "expression":
        job["steps"][-2]["run"] += "${{ secrets.KEY }}"
    elif change == "env":
        job["env"]["PRODUCTION_KEY"] = "forbidden"
    else:
        job["steps"].reverse()
    with pytest.raises(ValueError):
        m.plan(doc, suite, IMAGE)


@pytest.mark.parametrize("bad", ["pult-pitr:ci", "latest", "sha256:a", "sha256:" + "A" * 64, ""])
def test_no_mutable_identity(bad):
    with pytest.raises(ValueError):
        m.plan(workflow("synthetic"), "synthetic", bad)


def test_failure_preserved_and_cleanup_runs():
    calls = []

    def run(body, env):
        calls.append(body)
        if body == "FAIL":
            raise subprocess.CalledProcessError(1, "matrix")

    with pytest.raises(subprocess.CalledProcessError):
        m.execute([("setup", "SETUP"), ("matrix", "FAIL"), ("unused", "SKIP"),
                   ("Teardown", "CLEANUP")], {}, run)
    assert calls == ["SETUP", "FAIL", "CLEANUP"]


def test_success_runs_all_steps_and_cleanup():
    calls = []
    m.execute([("setup", "A"), ("matrix", "B"), ("Teardown", "C")], {},
              lambda body, env: calls.append(body))
    assert calls == ["A", "B", "C"]


@pytest.mark.parametrize("output", [IMAGE + " linux/arm64", "sha256:" + "b" * 64 + " linux/amd64"])
def test_image_inspection_mismatch_fails(monkeypatch, output):
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, output))
    with pytest.raises(ValueError):
        m.verify_image(IMAGE)


def test_refuses_local_execution(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(ValueError, match="hosted"):
        m.main(["synthetic", "--image-id", IMAGE])


def test_workflow_build_once_readonly_no_exports():
    doc = yaml.safe_load((ROOT / ".github/workflows/pitr_same_image.yml").read_text())
    assert doc["permissions"] == {"contents": "read"}
    assert set(doc[True]) == {"workflow_dispatch", "pull_request"}
    assert set(doc["jobs"]) == {"same-image"}
    job = doc["jobs"]["same-image"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 90
    assert "permissions" not in job
    steps = job["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert runs.count("-t pult-pitr:ci ops/pitr") == 1
    calls = [s for s in steps if "run_same_image_suite.py" in s.get("run", "")]
    assert len(calls) == 2
    assert " synthetic " in calls[0]["run"] and " extended " in calls[1]["run"]
    for step in calls:
        assert step["env"] == {"IMAGE_ID": "${{ steps.candidate.outputs.image_id }}"}
        assert '--image-id "$IMAGE_ID"' in step["run"]
        assert "if" not in step and "continue-on-error" not in step
    for step in steps:
        assert "continue-on-error" not in step
        assert not any(x in step.get("uses", "") for x in ("upload-artifact", "download-artifact", "login-action"))
    assert not any(x in runs for x in ("docker push", "docker login", "docker save", "ghcr.io"))
