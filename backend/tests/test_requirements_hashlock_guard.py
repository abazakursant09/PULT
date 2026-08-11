"""SECURITY-2D-3B — guard the reproducible, hash-pinned dependency install.

This is *defence-in-depth* text verification, not a reimplementation of pip: pip
itself (via `--require-hashes` in Docker and CI) is the authority that the locks are
correct and installable. These tests only assert that the repository never drifts back
to a floating install — that the four requirements files exist, that requirements.txt
is gone, that every installable pin in each lock is exact and carries a SHA-256 hash,
that no index/VCS/URL escape hatch sneaks in, that the headers record the generator and
its version, and that Docker + every backend CI job install a hash-pinned lock with
`--require-hashes` and nothing ad hoc.

CORRECTION (guard mutation gaps closed):
  A. Exact-pin validation no longer uses a `==`-only regex. Every non-comment logical
     line of each lock is *reconstructed* (backslash + `--hash` continuations joined) and
     parsed with `packaging.requirements.Requirement`. A line that is not an exact
     `name==version` — a range (`>=`,`<=`,`<`,`>`,`~=`,`!=`, wildcard), an editable/VCS/URL,
     or anything malformed — now FAILS instead of being silently skipped.
  B. The docker_build path-filter is validated *per event*: `push` and `pull_request` are
     each required to track all four requirements files. A whole-workflow substring check
     could not see a lock removed from just one trigger; the per-event check can.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from packaging.requirements import InvalidRequirement, Requirement

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

REQUIREMENT_FILES = (
    "backend/requirements.in",
    "backend/requirements.lock",
    "backend/requirements-ci.in",
    "backend/requirements-ci.lock",
)

_HASH_FULL = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
# Substrings that must never appear inside a lock requirement (direct URL / VCS / index).
_ESCAPE_SUBSTRINGS = ("git+", "hg+", "svn+", "bzr+", "://")


# ----------------------------------------------------------------- lock line parsing

def _logical_lines(path: Path) -> list[str]:
    """Reconstruct each logical requirement line of a generated lock.

    Joins backslash continuations and the indented `--hash=` continuation lines so that a
    package and every one of its hashes become ONE logical string. Whole-line comments and
    blank lines are dropped. Nothing installable is ever skipped: whatever is left is handed
    to the validator, so a stray/unexpected line fails loudly rather than vanishing.
    """
    out: list[str] = []
    buf = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.rstrip("\r")
        # A comment or blank line only counts as a boundary when we are not mid-continuation.
        if not buf and (not s.strip() or s.strip().startswith("#")):
            continue
        if s.rstrip().endswith("\\"):
            buf += s.rstrip()[:-1] + " "
            continue
        buf += s
        if buf.strip():
            out.append(buf.strip())
        buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _installable_records(path: Path) -> list[tuple[str, str, list[str], str]]:
    """Parse every logical requirement of a lock, asserting strict hash-lock invariants.

    Returns [(name, version, hashes, raw), ...]. Raises AssertionError on the FIRST line that
    violates any invariant — an unexpected option line, a VCS/URL escape hatch, a malformed
    requirement, a non-exact pin (range/wildcard), or a pin with no sha256 hash. This is what
    stops a `==`→range edit from being silently accepted.
    """
    records: list[tuple[str, str, list[str], str]] = []
    for raw in _logical_lines(path):
        assert not raw.startswith("-"), f"{path.name}: unexpected option/escape line: {raw!r}"
        parts = raw.split("--hash=")
        req_part = parts[0].strip()
        hashes = [p.strip() for p in parts[1:]]
        for bad in _ESCAPE_SUBSTRINGS:
            assert bad not in req_part, f"{path.name}: VCS/URL/index escape hatch in {raw!r}"
        try:
            req = Requirement(req_part)
        except InvalidRequirement as exc:
            raise AssertionError(f"{path.name}: malformed requirement {raw!r}: {exc}") from exc
        assert not req.url, f"{path.name}: direct URL for {req.name}: {req.url}"
        specifiers = list(req.specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "==", (
            f"{path.name}: {req.name} is not exact-pinned with '==' "
            f"(got {str(req.specifier)!r}) — ranges/wildcards are forbidden in a hash-lock"
        )
        version = specifiers[0].version
        assert "*" not in version, f"{path.name}: {req.name} uses a wildcard pin {version!r}"
        assert hashes, f"{path.name}: {req.name}=={version} has no --hash=sha256:"
        for h in hashes:
            assert _HASH_FULL.match(h), f"{path.name}: {req.name} has a non-sha256 hash {h!r}"
        records.append((req.name.lower(), version, hashes, raw))
    return records


def _pin_map(path: Path) -> dict[str, str]:
    return {name: version for name, version, _hashes, _raw in _installable_records(path)}


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


def _event_path_filters(path: Path) -> dict[str, list[str] | None]:
    """Return {event: paths-list-or-None} for the `push` and `pull_request` triggers.

    Parses the workflow as YAML and inspects each trigger separately, so a requirements
    file removed from ONE trigger's `paths:` is detectable. Note: PyYAML (YAML 1.1) parses a
    bare `on:` key as the boolean True, so the trigger map is looked up under both keys.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = data.get("on")
    if triggers is None:
        triggers = data.get(True)
    assert isinstance(triggers, dict), f"{path.name}: no parseable 'on:' trigger map"
    result: dict[str, list[str] | None] = {}
    for event in ("push", "pull_request"):
        node = triggers.get(event)
        result[event] = node.get("paths") if isinstance(node, dict) else None
    return result


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

def test_every_installable_line_is_exact_pinned_and_hashed():
    """Gap A: reconstruct and parse EVERY logical line of each lock (not just `==` lines).

    A range, wildcard, editable/VCS/URL, or malformed requirement now fails inside
    _installable_records; a `==`→`>=` edit can no longer slip past by simply not matching a
    package regex."""
    for lock in (RUNTIME_LOCK, CI_LOCK):
        records = _installable_records(lock)
        # Sanity floor: the runtime lock has 45 pins and the CI lock 54; a parser that
        # silently produced nothing (or a truncated lock) must not pass.
        assert len(records) >= 15, f"{lock.name}: only {len(records)} pins parsed"


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
    runtime = _pin_map(RUNTIME_LOCK)
    ci = _pin_map(CI_LOCK)
    for name, version in runtime.items():
        assert name in ci, f"runtime package {name} missing from CI lock"
        assert ci[name] == version, (
            f"version drift for {name}: runtime {version} != ci {ci[name]}"
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


def test_docker_build_path_filter_covers_all_four_files_in_each_event():
    """Gap B: `push` and `pull_request` are each required to track all four requirements
    files. Removing any one path from either trigger fails this test — a whole-workflow
    substring check could not tell which trigger the path belonged to."""
    filters = _event_path_filters(DOCKER_BUILD_YML)
    for event in ("push", "pull_request"):
        paths = filters[event]
        assert paths, f"docker_build.yml: '{event}' trigger has no 'paths:' filter"
        for required in REQUIREMENT_FILES:
            assert required in paths, (
                f"docker_build.yml: '{event}.paths' does not track {required}"
            )


def test_docker_build_path_filter_has_no_stale_requirements_txt():
    text = DOCKER_BUILD_YML.read_text(encoding="utf-8")
    assert "backend/requirements.txt" not in text, "stale requirements.txt in docker_build path-filter"
