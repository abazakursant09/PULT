"""SECURITY-2D-3E1 — guard that every production base image is pinned by digest.

A readable tag (``python:3.11-slim``) is mutable: the registry owner can repoint it
at any time, so the next build could silently pull a different OS / system libraries.
Pinning ``tag@sha256:<digest>`` keeps the tag for humans but freezes the exact bytes.

This guard is OFFLINE and analyses ONLY tracked Git files (``git ls-files``) — never a
recursive walk of the physical worktree, so untracked/user files cannot influence it.
It asserts that the set of tracked Dockerfiles equals a reviewed allowlist and that every
``FROM`` in each (all stages, not just the first) carries a readable tag AND a full
``@sha256:`` digest. Registry ownership of a digest is proven separately by the CI Docker
Build actually building on the pinned digest — not here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

# Reviewed allowlist of production Dockerfiles (repo-relative, POSIX). A new tracked
# Dockerfile that is not classified here must fail the guard until it is reviewed.
ALLOWLISTED_DOCKERFILES = ("backend/Dockerfile", "frontend/Dockerfile", "ops/backup/Dockerfile",
                           "ops/pitr/Dockerfile")

# Stage counts: single-stage app/backup images; the PITR runner is a 2-stage source build
# (builder + final), both FROM the same pinned PostgreSQL base.
EXPECTED_STAGE_COUNT = {"backend/Dockerfile": 1, "frontend/Dockerfile": 1, "ops/backup/Dockerfile": 1,
                        "ops/pitr/Dockerfile": 2}

# Known-good pinned references (image:tag@sha256:digest), verified against the Docker
# Registry v2 API and the Docker Hub API on 2026-08-11 (both agree; the python index
# digest also matches the merged SECURITY-2D-3B docker-build CI log). A digest bump is a
# deliberate, separately reviewed change — update these constants in that same PR.
EXPECTED_REFS = {
    "backend/Dockerfile": (
        "python:3.11-slim@sha256:"
        "90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff"
    ),
    "frontend/Dockerfile": (
        "node:20-alpine@sha256:"
        "fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293"
    ),
    # SECURITY-2D-3E1B-3A backup runner: FROM the pinned production PostgreSQL image (adds
    # only pinned+hash-verified rclone/age). Classified here because it is a tracked,
    # production-relevant Dockerfile that must stay digest-pinned.
    "ops/backup/Dockerfile": (
        "postgres:16-alpine@sha256:"
        "57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
    ),
    # SECURITY-2D-3E1B-3B1 PITR runner — both stages FROM the pinned PostgreSQL base.
    "ops/pitr/Dockerfile": (
        "postgres:16-alpine@sha256:"
        "57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
    ),
}

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOCKERFILE_NAME_RE = re.compile(r"^Dockerfile(\..+)?$")


def _tracked_dockerfiles() -> list[str]:
    """Tracked Dockerfiles only, via git (not a physical walk)."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return sorted(
        p for p in out if _DOCKERFILE_NAME_RE.match(p.rsplit("/", 1)[-1])
    )


def _from_directives(rel_path: str) -> list[str]:
    """All real FROM directives of a Dockerfile (comment lines excluded)."""
    text = (REPO / rel_path).read_text(encoding="utf-8")
    froms = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if re.match(r"(?i)^FROM\s", s):
            froms.append(s)
    return froms


def _image_ref(from_line: str) -> str:
    """Extract the image reference from a FROM line, dropping an optional --platform
    prefix and an optional `AS <stage>` suffix. A digest that appears only inside a
    comment can never reach here because comment lines are filtered out upstream."""
    tokens = from_line.split()
    # tokens[0] == FROM (any case)
    rest = tokens[1:]
    if rest and rest[0].startswith("--platform="):
        rest = rest[1:]
    assert rest, f"FROM has no image reference: {from_line!r}"
    ref = rest[0]
    # drop `AS stage` if present (rest[1:] == ['AS','name'])
    return ref


def _assert_pinned(ref: str, where: str) -> None:
    """A production image reference MUST be `name:tag@sha256:<64 lowercase hex>`."""
    assert "@" in ref, f"{where}: image is tag-only (no @sha256 digest): {ref!r}"
    name_tag, _, digest = ref.partition("@")
    # digest-only (no readable tag) is forbidden
    assert ":" in name_tag, f"{where}: digest-only, no readable tag: {ref!r}"
    image, _, tag = name_tag.partition(":")
    assert image, f"{where}: empty image name: {ref!r}"
    assert tag, f"{where}: empty tag: {ref!r}"
    assert tag != "latest", f"{where}: 'latest' tag is forbidden: {ref!r}"
    assert _SHA256_RE.match(digest), (
        f"{where}: digest must be exactly 'sha256:' + 64 lowercase hex "
        f"(no uppercase, no truncation): {digest!r}"
    )


def test_only_allowlisted_dockerfiles_are_tracked():
    tracked = _tracked_dockerfiles()
    unexpected = [p for p in tracked if p not in ALLOWLISTED_DOCKERFILES]
    assert not unexpected, (
        f"tracked Dockerfile(s) not in the reviewed digest-pin allowlist: {unexpected}. "
        "Classify and pin any new production image before adding it."
    )
    for expected in ALLOWLISTED_DOCKERFILES:
        assert expected in tracked, f"expected production Dockerfile missing: {expected}"


def test_every_from_in_every_stage_is_tag_plus_full_digest():
    for rel in ALLOWLISTED_DOCKERFILES:
        froms = _from_directives(rel)
        assert froms, f"{rel}: no FROM directive found"
        for line in froms:
            _assert_pinned(_image_ref(line), f"{rel} FROM {line!r}")


def test_stage_counts_match_expected():
    for rel, count in EXPECTED_STAGE_COUNT.items():
        froms = _from_directives(rel)
        assert len(froms) == count, (
            f"{rel}: expected {count} stage(s), found {len(froms)} — an unpinned extra "
            "stage must not be added unnoticed"
        )


def test_known_good_references_match():
    for rel, expected in EXPECTED_REFS.items():
        froms = _from_directives(rel)
        assert froms, f"{rel}: no FROM directive found"
        # Every stage's base (1 for single-stage, N for multi-stage builds) must be exactly the
        # reviewed known-good reference.
        for line in froms:
            ref = _image_ref(line)
            assert ref == expected, (
                f"{rel}: pinned reference drifted from the reviewed known-good value.\n"
                f"  expected: {expected}\n  found:    {ref}\n"
                "A digest bump must be a deliberate, separately reviewed change that updates "
                "this constant in the same PR."
            )
