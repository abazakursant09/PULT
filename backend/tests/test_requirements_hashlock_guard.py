"""SECURITY-2D-3B — guard the reproducible, hash-pinned dependency install.

This is *defence-in-depth* text verification, not a reimplementation of pip: pip
itself (via `--require-hashes` in Docker and CI) is the authority that the locks are
correct and installable. These tests only assert that the repository never drifts back
to a floating install — that the four requirements files exist, that requirements.txt
is gone, that every installable pin in each lock is exact and carries a SHA-256 hash,
that no index/VCS/URL escape hatch sneaks in, that the headers record the generator and
its version, and that Docker + every backend CI job install a hash-pinned lock with
`--require-hashes` and nothing ad hoc.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
WORKFLOWS = REPO / ".github" / "workflows"

RUNTIME_IN = BACKEND / "requirements.in"
RUNTIME_LOCK = BACKEND / "requirements.lock"
CI_IN = BACKEND / "requirements-ci.in"
CI_LOCK = BACKEND / "requirements-ci.lock"
LEGACY_TXT = BACKEND / "requirements.txt"
DOCKERFILE = BACKEND / "Dockerfile"
CONSTITUTIONAL_YML = WORKFLOWS / "constitutional_verification.yml"
DOCKER_BUILD_YML = WORKFLOWS / "docker_build.yml"

# A package declaration line: `name==version` at column 0 (optionally with a trailing
# ` \` continuation and/or inline `--hash=` tokens).
_PKG_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")
_HASH_RE = re.compile(r"--hash=(sha256:[0-9a-fA-F]{64})")


def _parse_lock(path: Path) -> dict[str, dict]:
    """Return {name: {'version': str, 'hashes': [str, ...]}} from a lock file.

    Deliberately simple: split into per-package blocks and collect the `--hash=` lines
    that belong to each. No resolution, no network — pip does the real validation.
    """
    records: dict[str, dict] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _PKG_RE.match(stripped)
        if m:
            current = m.group(1).lower()
            records[current] = {"version": m.group(2), "hashes": []}
            records[current]["hashes"].extend(_HASH_RE.findall(stripped))
            continue
        found = _HASH_RE.findall(stripped)
        if found and current is not None:
            records[current]["hashes"].extend(found)
    return records


def _noncomment_lines(path: Path) -> list[str]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def _yaml_noncomment_text(path: Path) -> str:
    """Workflow text with whole-line `#` comments removed, so assertions inspect the
    real YAML directives and never trip over prose in an explanatory comment (e.g. a
    comment that *documents* removing `pip install --upgrade pip`)."""
    return "\n".join(_noncomment_lines(path))


# --------------------------------------------------------------------------- files

def test_four_requirements_files_exist():
    for p in (RUNTIME_IN, RUNTIME_LOCK, CI_IN, CI_LOCK):
        assert p.is_file(), f"missing required file: {p.relative_to(REPO)}"


def test_legacy_requirements_txt_removed():
    assert not LEGACY_TXT.exists(), (
        "backend/requirements.txt must be deleted — a second authoritative input, a copy "
        "of the lock, or a symlink is exactly the drift this task removes."
    )


def test_runtime_in_keeps_alembic_exact_pin():
    # SECURITY-2D-3A pin must survive into the new input verbatim.
    assert "alembic==1.19.1" in RUNTIME_IN.read_text(encoding="utf-8")


def test_ci_in_pulls_in_runtime_intent():
    # The CI input must derive runtime intent from the runtime input, not restate it.
    assert re.search(r"^-r\s+requirements\.in\s*$", CI_IN.read_text(encoding="utf-8"), re.M)


# ------------------------------------------------------------------- lock integrity

def test_every_installable_pin_is_exact_and_hashed():
    for lock in (RUNTIME_LOCK, CI_LOCK):
        records = _parse_lock(lock)
        assert records, f"{lock.name} parsed to zero packages"
        for name, rec in records.items():
            assert rec["version"], f"{lock.name}: {name} has no == version"
            assert rec["hashes"], f"{lock.name}: {name}=={rec['version']} has no --hash=sha256:"
            for h in rec["hashes"]:
                assert h.startswith("sha256:"), f"{lock.name}: {name} non-sha256 hash {h}"


def test_locks_have_no_index_vcs_or_url_escape_hatch():
    forbidden = ("--index-url", "--extra-index-url", "-i ", "--trusted-host",
                 "git+", "hg+", "svn+", "bzr+", "://")
    for lock in (RUNTIME_LOCK, CI_LOCK):
        for line in _noncomment_lines(lock):
            for token in forbidden:
                assert token not in line, f"{lock.name}: forbidden {token!r} in: {line}"


def test_lock_headers_record_generator_and_version():
    for lock in (RUNTIME_LOCK, CI_LOCK):
        head = "\n".join(lock.read_text(encoding="utf-8").splitlines()[:30])
        assert re.search(r"\buv\s+\d+\.\d+\.\d+\b", head), f"{lock.name} header lacks generator+version"
        assert re.search(r"do not edit", head, re.I), f"{lock.name} header lacks 'do not edit'"
        assert "linux" in head.lower(), f"{lock.name} header lacks target platform"
    # Runtime lock targets 3.11 (Docker), CI lock targets 3.13 (CI interpreter).
    assert "3.11" in "\n".join(RUNTIME_LOCK.read_text(encoding="utf-8").splitlines()[:30])
    assert "3.13" in "\n".join(CI_LOCK.read_text(encoding="utf-8").splitlines()[:30])


def test_ci_lock_is_a_consistent_superset_of_runtime_lock():
    """Single-resolver consistency: every runtime package is in the CI lock at the SAME
    version (the CI lock is generated from an input that includes requirements.in)."""
    runtime = _parse_lock(RUNTIME_LOCK)
    ci = _parse_lock(CI_LOCK)
    for name, rec in runtime.items():
        assert name in ci, f"runtime package {name} missing from CI lock"
        assert ci[name]["version"] == rec["version"], (
            f"version drift for {name}: runtime {rec['version']} != ci {ci[name]['version']}"
        )


# ------------------------------------------------------------------------ Dockerfile

def test_dockerfile_installs_runtime_lock_with_require_hashes():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"pip install --require-hashes[^\n]*requirements\.lock", text), (
        "Dockerfile must install requirements.lock with --require-hashes"
    )


def test_dockerfile_does_not_ship_or_install_the_ci_lock_or_tools():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "requirements-ci.lock" not in text, "production image must not reference the CI lock"
    assert "requirements-ci.in" not in text
    assert "requirements.txt" not in text, "stale requirements.txt reference in Dockerfile"


# -------------------------------------------------------------------- CI workflows

def test_constitutional_backend_jobs_install_only_the_full_ci_lock():
    text = _yaml_noncomment_text(CONSTITUTIONAL_YML)
    assert "requirements.txt" not in text, "stale requirements.txt reference in CI workflow"
    assert "--upgrade pip" not in text, "floating `pip install --upgrade pip` must be removed"
    # Every `pip install` in this workflow must be the one hash-pinned CI-lock install —
    # no ad-hoc `pip install pytest/asyncpg/psycopg2-binary/ruff ...`.
    pip_installs = re.findall(r"pip install[^\n]*", text)
    assert pip_installs, "expected the CI-lock install command"
    for cmd in pip_installs:
        assert cmd.strip() == "pip install --require-hashes -r requirements-ci.lock", (
            f"unexpected pip install in CI workflow: {cmd!r}"
        )
    # The three backend jobs (constitution, postgres-explain, postgres-migration).
    assert len(pip_installs) == 3, f"expected 3 backend hash-pinned installs, got {len(pip_installs)}"


def test_docker_build_path_filter_covers_all_four_requirements_files():
    text = DOCKER_BUILD_YML.read_text(encoding="utf-8")
    for f in ("backend/requirements.in", "backend/requirements.lock",
              "backend/requirements-ci.in", "backend/requirements-ci.lock"):
        assert f in text, f"docker_build path-filter missing {f}"
    assert "backend/requirements.txt" not in text, "stale requirements.txt in docker_build path-filter"
