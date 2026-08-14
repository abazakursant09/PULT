"""SECURITY-2D-3E1B-3C2A — offline guard for the DORMANT Selectel canary tooling.

Analyses executable code (ops/canary/canary.py), the candidate policy JSON structure, and the
canary_offline workflow — NOT comments. Every listed mutation must flip a guard assertion RED
(see MUTATION_MATRIX). No network, no credentials, no Docker; pure structural checks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

REPO = Path(__file__).resolve().parents[2]
CANARY = REPO / "ops" / "canary"
CANARY_PY = CANARY / "canary.py"
POLICIES = CANARY / "policies"
WORKFLOW = REPO / ".github" / "workflows" / "canary_offline.yml"
POLICY_DOC = REPO / "docs" / "pitr-operations-policy.md"
NEG_MATRIX = CANARY / "negative-matrix.md"

# Each mutation below, applied to a disposable copy, must make at least one test in this file fail.
MUTATION_MATRIX = [
    "enable a live-network path", "read credentials before the live gate",
    "add a Selectel endpoint to the workflow", "add a default credential",
    "add a credential CLI argument", "print os.environ", "allow s3:* in an Allow",
    "use Resource '*'", "logical-writer allow GetObject", "logical-writer allow DeleteObject",
    "pitr-writer allow DeleteObject", "pitr-writer allow admin action", "reader allow PutObject",
    "reader allow DeleteObject", "app allow anything", "recursive delete", "bucket delete w/o allowlist",
    "unbounded retry", "retry non-idempotent Put", "use --insecure", "use an http:// public endpoint",
    "remove host/prefix allowlist", "remove exact cleanup", "ignore unexpected allow",
    "auto-expand permission", "hardcode a production bucket", "use production DB", "upload-artifact",
    "remove cleanup always", "remove MinIO != Selectel disclaimer", "flip launch gate to READY",
]


def _code() -> str:
    return CANARY_PY.read_text(encoding="utf-8")


def _policies() -> dict[str, dict]:
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in POLICIES.glob("*.json")}


def _allow_actions(pol: dict) -> set[str]:
    st = pol["policy"]["Statement"]
    st = [st] if isinstance(st, dict) else st
    out = set()
    for s in st:
        if s.get("Effect") == "Allow":
            acts = s.get("Action", [])
            out.update([acts] if isinstance(acts, str) else acts)
    return out


# ---------------- canary.py: modes + live fail-closed ----------------
def test_modes_are_exactly_the_allowed_set():
    m = re.search(r'choices=\[([^\]]+)\]', _code())
    assert m, "argparse choices not found"
    choices = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert choices == {"validate-policies", "plan", "minio-compat", "live"}, choices


def test_live_fails_closed_before_any_network_or_credential():
    code = _code()
    # the live branch must appear and return nonzero + the sentinel before dispatching to minio-compat
    live_idx = code.index('args.mode == "live"')
    minio_idx = code.index('args.mode == "minio-compat"')
    assert live_idx < minio_idx, "live gate must be evaluated before minio dispatch"
    live_branch = code[live_idx:minio_idx]
    assert 'LIVE_NOT_IMPLEMENTED = "LIVE_SELECTEL_NOT_IMPLEMENTED"' in code
    assert "LIVE_NOT_IMPLEMENTED" in live_branch
    assert "return 3" in live_branch
    # no network/credential/env read inside the live branch
    for bad in ("subprocess", "environ", "requests", "socket", "mc "):
        assert bad not in live_branch, f"live branch must not touch {bad!r}"


def test_no_selectel_endpoint_in_executable_code():
    assert not re.search(r"selcloud\.ru|storage\.selcloud", _code(), re.I)


def test_no_default_credentials_and_missing_creds_fail_closed():
    code = _code()
    # env reads for creds must default to empty string, and empty must _fail
    assert 'os.environ.get("CANARY_MINIO_ADMIN_SECRET", "")' in code
    assert "not admin_sec" in code and "_fail(" in code


def test_no_credentials_on_argv():
    code = _code()
    for bad in ("--secret", "--access-key", "--key", "--password", "add_argument"):
        if bad == "add_argument":
            # only the positional mode arg is allowed
            assert code.count("add_argument(") == 1, "only the positional mode argument is allowed"
        else:
            assert bad not in code, f"credential CLI arg {bad!r} forbidden"


def test_no_environment_dump():
    code = _code()
    assert "print(os.environ" not in code and "print(dict(os.environ" not in code
    assert "os.environ)" not in code.replace('os.environ.get', '')


# ---------------- policy closure ----------------
def test_no_wildcard_allow_and_no_bare_resource_star():
    for name, doc in _policies().items():
        st = doc["policy"]["Statement"]
        st = [st] if isinstance(st, dict) else st
        for s in st:
            acts = s.get("Action", [])
            acts = [acts] if isinstance(acts, str) else acts
            res = s.get("Resource", [])
            res = [res] if isinstance(res, str) else res
            if s.get("Effect") == "Allow":
                assert "s3:*" not in acts, f"{name}: s3:* in Allow"
            assert "*" not in res, f"{name}: bare Resource '*'"


def test_logical_writer_no_get_or_delete():
    acts = _allow_actions(_policies()["logical-writer.json"])
    assert "s3:GetObject" not in acts and "s3:DeleteObject" not in acts


def test_pitr_writer_no_delete_or_admin():
    acts = _allow_actions(_policies()["pitr-writer.json"])
    for bad in ("s3:DeleteObject", "s3:PutBucketPolicy", "s3:PutLifecycleConfiguration", "s3:PutObjectRetention"):
        assert bad not in acts, f"pitr-writer must not allow {bad}"
    assert "s3:GetObject" in acts and "s3:PutObject" in acts  # Get yes, Delete no


def test_reader_no_put_or_delete():
    acts = _allow_actions(_policies()["restore-reader.json"])
    for bad in ("s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"):
        assert bad not in acts


def test_app_policy_is_deny_only():
    st = _policies()["app-deny.json"]["policy"]["Statement"]
    st = [st] if isinstance(st, dict) else st
    assert st and all(s.get("Effect") == "Deny" for s in st), "app policy must be Deny-only"


def test_retention_admin_marked_provisional_and_not_active_delete():
    doc = _policies()["retention-admin.json"]
    assert doc["_canary"].get("marker") == "NOT_FOR_ROUTINE_BACKUP"
    acts = _allow_actions(doc)
    for prov in ("s3:DeleteObjectVersion", "s3:BypassGovernanceRetention", "s3:PutLifecycleConfiguration"):
        assert prov not in acts, f"provisional action {prov} must not be in active Allow"


def test_reference_policy_is_not_active_and_not_selectable():
    doc = _policies()["pitr-writer-official-reference.json"]
    assert doc["_canary"].get("active") is False
    assert doc["_canary"].get("marker") == "REFERENCE_NOT_ACTIVE_CANDIDATE"
    # the runtime matrix (what minio-compat/plan iterate) must NOT include the reference role
    code = _code()
    matrix = code[code.index("_MATRIX = {"):code.index("# ------------------------- minio-compat")]
    assert "reference" not in matrix, "reference role must never be an executed candidate"


# ---------------- allowlists / run-id / timeout / cleanup ----------------
def test_prefix_and_host_allowlists_present():
    code = _code()
    assert 'ALLOWED_PREFIX_ROOTS = ("pitr/", "logical/", "status/", "canary/")' in code
    assert "MINIO_HOST_ALLOWLIST" in code and "127.0.0.1" in code


def test_random_run_id_generated_and_validated():
    code = _code()
    assert "secrets.token_hex" in code
    assert "re.fullmatch" in code and "run_id" in code


def test_bounded_subprocess_timeout():
    assert re.search(r"subprocess\.run\([^)]*timeout=\d+", _code(), re.S)


def test_no_unbounded_retry_loop():
    assert "while True" not in _code()


def test_no_recursive_or_prune_delete():
    code = _code()
    assert "--recursive" not in code, "no recursive delete"
    assert "prune" not in code, "no system/volume prune"
    # a bucket delete may exist only against the exact synthetic canary bucket variable
    for m in re.finditer(r'"rb",\s*f"adm/\{([^}]+)\}"', code):
        assert m.group(1) == "bucket", "rb only targets the exact synthetic bucket var"


def test_exact_cleanup_in_finally():
    code = _code()
    assert "finally:" in code
    assert "created_objs" in code and "created_users" in code


def test_unexpected_allow_or_deny_is_fatal():
    code = _code()
    assert "failures.append" in code and 'if failures:' in code
    assert "_fail(" in code


def test_no_insecure_or_public_http():
    code = _code()
    assert "--insecure" not in code and "--no-check-certificate" not in code
    # any http:// literal in code must be the private allowlist regex, not a public host
    for lit in re.findall(r'http://[^\s"\')]+', code):
        assert re.search(r"(minio|localhost|127\.0\.0\.1)", lit), f"public http endpoint {lit!r}"


def test_no_production_bucket_or_db():
    code = _code()
    for bad in ("pult-pitr", "pult-backup", "business_pult", "postgres://", "psycopg", "sqlalchemy"):
        assert bad not in code, f"canary tooling must not reference {bad!r}"


def test_minio_not_selectel_disclaimer_everywhere():
    code = _code()
    # pin it in the module docstring (a stable, singular location), not merely somewhere in the file
    docstring = code.split('"""')[1]
    assert "MinIO != Selectel" in docstring, "module docstring must carry the MinIO != Selectel disclaimer"
    assert "MinIO ≠ Selectel" in NEG_MATRIX.read_text(encoding="utf-8")
    assert "MinIO ≠ Selectel" in POLICY_DOC.read_text(encoding="utf-8")


def test_deny_driven_no_auto_expansion_documented():
    nm = NEG_MATRIX.read_text(encoding="utf-8")
    assert "NOT auto-widened" in nm or "not auto-widened" in nm
    doc = POLICY_DOC.read_text(encoding="utf-8")
    assert "auto-expansion" in doc or "auto-widen" in doc or "not auto" in doc.lower()


def test_launch_gate_still_not_ready():
    doc = POLICY_DOC.read_text(encoding="utf-8")
    # pin the current-status line exactly; a flip to READY must be caught
    assert "Текущий статус: **NOT READY.**" in doc
    assert "Текущий статус: **READY" not in doc


# ---------------- workflow ----------------
@pytest.mark.skipif(yaml is None, reason="pyyaml unavailable")
def test_workflow_hardening():
    raw = WORKFLOW.read_text(encoding="utf-8")
    wf = yaml.safe_load(raw)
    assert wf["permissions"] == {"contents": "read"}
    assert "concurrency" in wf
    # triggers only touch 3C2A paths
    pr_paths = wf[True]["pull_request"]["paths"]
    assert any("ops/canary/**" == p for p in pr_paths)
    for job in wf["jobs"].values():
        assert "timeout-minutes" in job
    # no secrets, no artifacts, no --insecure, no Selectel host
    assert "secrets." not in raw and "${{ secrets" not in raw
    assert "upload-artifact" not in raw
    assert "--insecure" not in raw
    assert not re.search(r"selcloud\.ru|storage\.selcloud", raw, re.I)
    # actions pinned by 40-hex SHA (no floating @vX/@main)
    for ref in re.findall(r"uses:\s*([^\s]+)", raw):
        assert re.search(r"@[0-9a-f]{40}$", ref), f"action not SHA-pinned: {ref}"
    # cleanup always
    assert "if: always()" in raw
    # persist-credentials false on every checkout
    assert raw.count("persist-credentials: false") >= 2


@pytest.mark.skipif(yaml is None, reason="pyyaml unavailable")
def test_workflow_minio_endpoint_is_private():
    raw = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r'CANARY_MINIO_ENDPOINT:\s*"([^"]+)"', raw)
    assert m and re.search(r"(127\.0\.0\.1|localhost|minio)", m.group(1))
    assert m.group(1).startswith("http://127.0.0.1") or m.group(1).startswith("http://localhost")


def test_mutation_matrix_is_declared():
    assert len(MUTATION_MATRIX) >= 30
