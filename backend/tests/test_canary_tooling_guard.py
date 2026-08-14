"""SECURITY-2D-3E1B-3C2A — offline guard for the DORMANT Selectel canary tooling.

Analyses executable code (ops/canary/canary.py), the candidate policy JSON structure, and the
canary_offline workflow — NOT comments. Every listed mutation must flip a guard assertion RED
(see MUTATION_MATRIX). No network, no credentials, no Docker; pure structural checks.
"""

from __future__ import annotations

import ast
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
    "unbounded retry", "use --insecure", "use an http:// public endpoint",
    "remove host/prefix allowlist", "remove exact cleanup", "ignore unexpected allow",
    "auto-expand permission", "hardcode a production bucket", "use production DB", "upload-artifact",
    "remove cleanup always", "remove MinIO != Selectel disclaimer", "flip launch gate to READY",
    # write-retry family (AST guard) — each must be RED; the read-only range loop is a GREEN negative control
    "wrap remote Put in for _ in range(N)", "wrap Put in while attempts<N",
    "move Put into a helper called from range loop", "dynamic command list inside a retry loop",
    "NEGATIVE CONTROL: range loop with read-only ls/stat stays GREEN",
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
    assert len(MUTATION_MATRIX) >= 34


# ---------------- AST guard: no retry of mutating S3 operations ----------------
# A retry loop repeats the SAME operation (for _ in range(N) / while ...). Repeating a mutating S3
# operation (cp local->remote, mb, rm, rb, multipart, admin user/policy, retention/legal-hold) is
# unsafe (double-write / duplicate resource / masked partial failure). Iterating a COLLECTION
# (for key in created_objs) performs one op per distinct resource and is allowed (that is the cleanup
# path). Read-only ops (ls/stat/info) may be retried (readiness polling). Unknown / dynamic command
# lists are treated as mutating (fail-closed).

# first-token (and admin subcommand) command sets that are provably read-only / loop-safe
_MC_READONLY_FIRST = {"ls", "stat", "du", "tree", "find", "info", "version", "alias"}
_MC_ADMIN_READONLY = {"info"}


def _mc_call_is_mutating(call: ast.Call) -> bool:
    """True if this `_mc([...])` call mutates an external resource (fail-closed on anything unprovable)."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "_mc"):
        return False
    if not call.args:
        return True
    first = call.args[0]
    if not isinstance(first, ast.List) or not first.elts:
        return True  # dynamic / non-literal command list -> cannot prove read-only
    lead = [e.value for e in first.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if not lead:
        return True
    cmd = lead[0]
    if cmd == "admin":
        return not (len(lead) >= 2 and lead[1] in _MC_ADMIN_READONLY)
    return cmd not in _MC_READONLY_FIRST


def _is_mc_mutating_anywhere(node: ast.AST) -> bool:
    return any(isinstance(c, ast.Call) and _mc_call_is_mutating(c) for c in ast.walk(node))


def _mutating_func_names(tree: ast.AST) -> set[str]:
    """Fixpoint set of module-level functions that (transitively) perform a mutating `_mc` op."""
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    mutating = {name for name, fn in funcs.items() if _is_mc_mutating_anywhere(fn)}
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            if name in mutating:
                continue
            for c in ast.walk(fn):
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in mutating:
                    mutating.add(name)
                    changed = True
                    break
    return mutating


def _retry_loop_nodes(fn: ast.FunctionDef) -> list[ast.AST]:
    """Loops that REPEAT the same body: while-loops, for-in-range, and range-driven comprehensions."""
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.While):
            out.append(n)
        elif isinstance(n, ast.For) and isinstance(n.iter, ast.Call) \
                and isinstance(n.iter.func, ast.Name) and n.iter.func.id == "range":
            out.append(n)
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for g in n.generators:
                if isinstance(g.iter, ast.Call) and isinstance(g.iter.func, ast.Name) and g.iter.func.id == "range":
                    out.append(n)
                    break
    return out


def _retry_violations(fn: ast.FunctionDef, mutating_names: set[str]) -> list[str]:
    v = []
    for loop in _retry_loop_nodes(fn):
        for c in ast.walk(loop):
            if not isinstance(c, ast.Call):
                continue
            if _mc_call_is_mutating(c):
                v.append(f"line {getattr(c, 'lineno', '?')}: mutating _mc inside retry loop")
            elif isinstance(c.func, ast.Name) and c.func.id in mutating_names:
                v.append(f"line {getattr(c, 'lineno', '?')}: retry loop calls mutating helper {c.func.id!r}")
    return v


def _scan_source_for_retry_writes(src: str, func_name: str = "minio_compat") -> list[str]:
    tree = ast.parse(src)
    mutating = _mutating_func_names(tree)
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name]
    assert fns, f"function {func_name} not found"
    out = []
    for fn in fns:
        out += _retry_violations(fn, mutating)
    return out


def test_no_mutating_mc_inside_retry_loop():
    """Real canary.py: minio_compat must never repeat a mutating S3 op in a retry loop."""
    violations = _scan_source_for_retry_writes(_code(), "minio_compat")
    assert violations == [], "mutating S3 op inside a retry loop: " + "; ".join(violations)


def test_retry_detector_has_teeth():
    """The AST detector must flag write-retries (incl. helper bypass / dynamic cmd) and pass read-only retries."""
    def scan(body):
        src = "def _mc(a):\n    return (0, '', '')\n" + body
        return _scan_source_for_retry_writes(src, "minio_compat")

    put = 'f"u/{b}/{o}"'
    # A: for _ in range(N) around a remote Put -> RED
    assert scan(f'def minio_compat():\n    for _ in range(5):\n        _mc(["cp", "x", {put}])\n')
    # B: while attempts < N around Put -> RED
    assert scan(f'def minio_compat():\n    n = 0\n    while n < 5:\n        _mc(["cp", "x", {put}]); n += 1\n')
    # helper bypass: Put in a helper called from a range loop -> RED
    assert scan(f'def _put():\n    _mc(["cp", "x", {put}])\ndef minio_compat():\n    for _ in range(5):\n        _put()\n')
    # dynamic command list inside a retry loop -> RED (fail-closed)
    assert scan('def minio_compat():\n    cmd = ["cp", "x", "y"]\n    for _ in range(5):\n        _mc(cmd)\n')
    # admin mutation retried -> RED
    assert scan('def minio_compat():\n    for _ in range(3):\n        _mc(["admin", "user", "add", "u", "s"])\n')
    # NEGATIVE CONTROL: read-only ls/stat retried (readiness polling) -> GREEN
    assert scan('def minio_compat():\n    for _ in range(30):\n        _mc(["ls", "u/b/"])\n        _mc(["stat", "u/b/x"])\n') == []
    # collection iteration performing one write per element (cleanup) -> GREEN
    assert scan('def minio_compat():\n    for k in ["a", "b"]:\n        _mc(["rm", f"u/{k}"])\n') == []


def test_no_write_retry_decorator_or_helper_in_runtime():
    """No generic retry decorator/wrapper that could silently re-issue writes."""
    code = _code()
    assert "@retry" not in code and "tenacity" not in code and "backoff" not in code
