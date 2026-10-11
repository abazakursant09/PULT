"""Manual source-only orchestration; no publishing or retained-image proof."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"


def check_preflight(doc):
    triggers = doc.get("on", doc.get(True))
    assert triggers == {"workflow_dispatch": None}
    assert doc["permissions"] == {"contents": "read"}
    assert doc["jobs"] == {
        "synthetic": {"uses": "./.github/workflows/pitr_synthetic.yml"},
        "extended": {"needs": "synthetic", "uses": "./.github/workflows/pitr_extended.yml"},
    }
    assert doc["concurrency"] == {
        "group": "pitr-source-preflight-${{ github.ref }}", "cancel-in-progress": False,
    }


def test_preflight_is_manual_source_only():
    check_preflight(yaml.safe_load((WORKFLOWS / "pitr_source_preflight.yml").read_text()))


@pytest.mark.parametrize("name", ["synthetic", "extended"])
def test_called_suites_keep_readonly_source_mode(name):
    doc = yaml.safe_load((WORKFLOWS / f"pitr_{name}.yml").read_text())
    triggers = doc.get("on", doc.get(True))
    assert "workflow_call" in triggers and triggers["workflow_call"] is None
    assert doc["permissions"] == {"contents": "read"}
    job = doc["jobs"][f"pitr-{name}"]
    assert "permissions" not in job
    steps = job["steps"]
    assert any("docker buildx build --load -t pult-pitr:ci ops/pitr" in s.get("run", "") for s in steps)
    assert not any("secrets" in s for s in steps)


@pytest.mark.parametrize("change", ["push", "write", "inherit", "publish", "skip", "ref", "parallel"])
def test_dangerous_or_misleading_changes_rejected(change):
    doc = yaml.safe_load((WORKFLOWS / "pitr_source_preflight.yml").read_text())
    if change == "push":
        doc[True]["push"] = None
    elif change == "write":
        doc["permissions"]["packages"] = "write"
    elif change == "inherit":
        doc["jobs"]["synthetic"]["secrets"] = "inherit"
    elif change == "publish":
        doc["jobs"]["publish"] = {"run": "docker push image"}
    elif change == "skip":
        doc["jobs"]["synthetic"]["if"] = False
    elif change == "ref":
        doc["jobs"]["synthetic"]["uses"] += "@master"
    else:
        del doc["jobs"]["extended"]["needs"]
    with pytest.raises(AssertionError):
        check_preflight(doc)
