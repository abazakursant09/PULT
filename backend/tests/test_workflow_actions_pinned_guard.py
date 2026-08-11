"""SECURITY-2D-3D — GitHub Actions pinning + minimal-permission guards.

Line-based (NOT a YAML round-trip — a serializer would turn the `on:` key into the boolean True). Pins the
CI-hardening invariants: every external `uses:` is a 40-hex commit SHA (no moving tag/branch), each checkout
drops the token, the constitutional workflow declares read-only token perms, and the Docker workflow neither
gains write perms nor pushes. Authoritative behaviour proof stays the CI run itself.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(__file__)
_WF_DIR = os.path.join(_HERE, "..", "..", ".github", "workflows")

_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)")
_EXTERNAL_SHA = re.compile(r"^[^./][^@]+@[0-9a-f]{40}$")   # owner/repo@<40-hex>
_MUTABLE = re.compile(r"@(v\d+(\.\d+)*|main|master|latest|[0-9a-f]{7,8})$")


def _workflows():
    return [os.path.join(_WF_DIR, f) for f in os.listdir(_WF_DIR)
            if f.endswith((".yml", ".yaml"))]


def _uses_refs(path):
    refs = []
    for ln in open(path, encoding="utf-8"):
        m = _USES.match(ln)
        if m:
            refs.append(m.group(1))
    return refs


def test_every_external_uses_is_full_sha():
    assert _workflows(), "no workflow files found"
    for wf in _workflows():
        for ref in _uses_refs(wf):
            if ref.startswith("./"):
                continue                                   # local composite action — no SHA required
            assert "@" in ref, f"{os.path.basename(wf)}: external use without ref: {ref}"
            assert _EXTERNAL_SHA.match(ref), f"{os.path.basename(wf)}: not SHA-pinned: {ref}"
            assert not _MUTABLE.search(ref), f"{os.path.basename(wf)}: mutable ref: {ref}"


def test_every_checkout_drops_credentials():
    # each `uses: actions/checkout@<sha>` line is immediately followed by a with:/persist-credentials: false
    for wf in _workflows():
        lines = open(wf, encoding="utf-8").read().split("\n")
        for i, ln in enumerate(lines):
            if re.search(r"uses:\s*actions/checkout@[0-9a-f]{40}", ln):
                window = "\n".join(lines[i:i + 4])
                assert "persist-credentials: false" in window, \
                    f"{os.path.basename(wf)} line {i+1}: checkout without persist-credentials: false"


def test_constitutional_has_top_level_read_permissions():
    p = os.path.join(_WF_DIR, "constitutional_verification.yml")
    lines = open(p, encoding="utf-8").read().split("\n")
    # a column-0 `permissions:` followed by `  contents: read`, before the top-level `jobs:`
    top_perm = None
    for i, ln in enumerate(lines):
        if ln == "jobs:":
            break
        if ln == "permissions:":
            top_perm = i
    assert top_perm is not None, "constitutional: no top-level permissions block"
    assert lines[top_perm + 1].strip() == "contents: read", "constitutional: top-level perms not contents: read"


def test_docker_workflow_no_write_perms_and_no_push():
    src = open(os.path.join(_WF_DIR, "docker_build.yml"), encoding="utf-8").read()
    assert "push: false" in src and "push: true" not in src
    for bad in ("contents: write", "packages: write", "id-token: write", "pull-requests: write"):
        assert bad not in src, f"docker_build grants {bad!r}"


def test_no_pull_request_target():
    for wf in _workflows():
        assert "pull_request_target" not in open(wf, encoding="utf-8").read(), \
            f"{os.path.basename(wf)} uses pull_request_target"
