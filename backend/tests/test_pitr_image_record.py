"""Synthetic records only: successful validation is NOT provenance verification."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/pitr/validate_image_record.py"
SPEC = importlib.util.spec_from_file_location("pitr_record", SCRIPT)
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)
POLICY = dict(source_repository="example/project", source_commit="a" * 40,
              image_repository="ghcr.io/example/test", platform="linux/amd64")


def record():
    digest = "sha256:" + "b" * 64
    evidence = dict(source_commit=POLICY["source_commit"], image_digest=digest,
                    phase="post_publication", run_id=1, run_attempt=1, conclusion="success")
    return dict(schema_version=1, **POLICY, image_digest=digest,
                evidence=dict(synthetic=copy.deepcopy(evidence), extended=copy.deepcopy(evidence)))


def run(data, **policy):
    return v.validate(json.dumps(data).encode(), **(POLICY | policy))


def test_forged_but_consistent_is_only_structure_valid():
    assert run(record()) == "STRUCTURE_AND_BINDING_VALID"
    assert v.validate(json.dumps(record(), indent=4, sort_keys=True).encode(), **POLICY) == v.SUCCESS


@pytest.mark.parametrize("level", ["top", "evidence", "synthetic", "extended"])
@pytest.mark.parametrize("change", ["missing", "extra", "duplicate"])
def test_exact_keys(level, change):
    data = record()
    obj = data if level == "top" else data["evidence"] if level == "evidence" else data["evidence"][level]
    key = next(iter(obj))
    if change == "missing":
        del obj[key]
    elif change == "extra":
        obj["extra"] = "secret"
    raw = json.dumps(data)
    if change == "duplicate":
        needle = json.dumps(key) + ":"
        raw = raw.replace(needle, needle + "null," + needle, 1)
    with pytest.raises(v.InvalidRecord):
        v.validate(raw.encode(), **POLICY)


@pytest.mark.parametrize("raw", [b"\xff", b"{}{}", b"NaN", b"Infinity", b"null",
                                  b" " * (v.LIMIT + 1), b"[" * 2000 + b"]" * 2000],
                         ids=["encoding", "trailing", "nan", "infinity", "null", "oversize", "depth"])
def test_malformed(raw):
    with pytest.raises(v.InvalidRecord):
        v.validate(raw, **POLICY)


@pytest.mark.parametrize("field,value", [("schema_version", True), ("schema_version", "1"),
    ("image_digest", "latest"), ("image_digest", "sha256:" + "0" * 64),
    ("image_digest", "sha256:" + "B" * 64), ("image_digest", "sha256:" + "b" * 63),
    ("image_digest", "sha256:" + "g" * 64), ("platform", "linux/arm64"),
    ("source_commit", "c" * 40), ("source_repository", "other/repo"),
    ("image_repository", "ghcr.io/example/test:latest")])
def test_wrong_fields(field, value):
    data = record()
    data[field] = value
    with pytest.raises(v.InvalidRecord):
        run(data)


@pytest.mark.parametrize("kind", ["synthetic", "extended"])
@pytest.mark.parametrize("field,value", [("source_commit", "c" * 40), ("image_digest", "sha256:" + "c" * 64),
    ("phase", "pre_publication"), ("conclusion", "pending"), ("conclusion", "skipped"),
    ("conclusion", "failure"), ("run_id", True), ("run_id", 0), ("run_id", "1"),
    ("run_attempt", -1), ("run_attempt", True)])
def test_bad_evidence(kind, field, value):
    data = record()
    data["evidence"][kind][field] = value
    with pytest.raises(v.InvalidRecord):
        run(data)


@pytest.mark.parametrize("value", ["https://ghcr.io/a/b", "ghcr.io/user:secret@a/b",
                                  "ghcr.io/a/b?x=1", "ghcr.io/a/b#x", "ghcr.io/a/b ",
                                  "ghcr.io/a/b@sha256:" + "b" * 64])
def test_policy_cannot_authorize_malformed_repository(value):
    data = record()
    data["image_repository"] = value
    with pytest.raises(v.InvalidRecord):
        run(data, image_repository=value)


def test_cli_safe_output(tmp_path, capsys):
    path = tmp_path / "candidate.json"
    path.write_text("secret-invalid-input", encoding="utf-8")
    args = [str(path)]
    for key, value in POLICY.items():
        args += ["--" + key.replace("_", "-"), value]
    assert v.main(args) == 1
    assert capsys.readouterr().err == "INVALID_INPUT\n"
    path.write_text(json.dumps(record()), encoding="utf-8")
    assert v.main(args) == 0
    assert capsys.readouterr().out == v.SUCCESS + "\n"
    assert v.main(["--unknown=secret"]) == 1
    assert capsys.readouterr().err == "INVALID_INPUT\n"


def test_no_network_or_process_calls(monkeypatch):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("external operation")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert run(record()) == v.SUCCESS
    with pytest.raises(v.InvalidRecord):
        v.validate(b"invalid", **POLICY)


def test_missing_file_and_oversize_cli(tmp_path, capsys):
    path = tmp_path / "missing-secret-name"
    args = [str(path)]
    for key, value in POLICY.items():
        args += ["--" + key.replace("_", "-"), value]
    assert v.main(args) == 1
    assert capsys.readouterr().err == "INVALID_INPUT\n"
    path.write_bytes(b" " * (v.LIMIT + 1))
    assert v.main(args) == 1
    assert capsys.readouterr().err == "INVALID_INPUT\n"


@pytest.mark.parametrize("field,value", [("source_repository", "different/project"),
    ("source_commit", "c" * 40), ("image_repository", "ghcr.io/other/image"),
    ("platform", "linux/arm64")])
def test_separate_policy_binding(field, value):
    with pytest.raises(v.InvalidRecord):
        run(record(), **{field: value})
