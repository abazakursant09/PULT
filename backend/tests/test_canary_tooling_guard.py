"""SECURITY-2D-3E1B-3C2A — offline guard for the DORMANT Selectel canary tooling.

Analyses executable code (ops/canary/canary.py), the candidate policy JSON structure, and the
canary_offline workflow — NOT comments. Every listed mutation must flip a guard assertion RED
(see MUTATION_MATRIX). No network, no credentials, no Docker; pure structural checks.
"""

from __future__ import annotations

import ast
import hashlib
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

# SHA-256 of the EXACT bytes of ops/canary/canary.py. This freezes the whole canary runtime: ANY change
# (safe or unsafe) flips the freeze test RED. Independent reviews confirmed the pinned runtime is safe
# (live -> exit 3 before network/credentials; single-shot writes; loopback-only endpoint; exact cleanup;
# no retries; AST detector reports 0 violations). Because a semantic analyzer can never prove the safety
# of arbitrary future Python, the byte-freeze is the strong invariant; the AST detector below stays as
# defense-in-depth for known retry patterns.
#
# Updating this digest is allowed ONLY when ALL of the following hold together:
#   - a dedicated task explicitly changes the canary runtime;
#   - the full canary.py diff has been reviewed;
#   - live fail-closed re-proven; network confinement re-proven; IAM/cleanup/retry guards re-proven;
#   - the MinIO compatibility matrix is green;
#   - an independent safety review passed;
#   - Inal separately approved the runtime change.
# The digest is a hard-coded literal — never computed at run time, never env-overridable, never auto-updated.
_CANARY_RUNTIME_SHA256 = "a10a463eeb59b1177a5736b97c8f5c553229f57c7be7efd3c3dee8cc571e1976"

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


# ---------------- AST guard: no automatic REPEAT of a mutating S3 operation ----------------
# A "repeated context" runs its body more than once: while / for / async-for / comprehensions /
# generator expressions / higher-order repeats (map, filter, itertools.starmap|repeat, any/all over a
# mutating generator) / retry decorators / direct-or-indirect recursion / a mutating helper called from
# any of these. Repeating a MUTATING S3 op (cp local->remote, mb, rm, rb, multipart, admin user/policy,
# retention/versioning/legal-hold) is unsafe (double-write / duplicate resource / masked partial failure).
#
# DEFAULT FAIL-CLOSED: a mutating `_mc` (or a call to a mutating helper) inside ANY repeated context is a
# violation. The ONLY exception is an exact per-resource `for`/`async-for` loop, and only for a DIRECT
# `_mc`, where a loop-target variable flows (through in-loop assignments) into the RESOURCE-IDENTITY
# argument of the command (the object key / bucket / user / policy name — NOT the local source, a
# timeout, or a print). Unknown / dynamic command lists and unrecognised command shapes are mutating.

_MC_READONLY_FIRST = {"ls", "stat", "du", "tree", "find", "info", "version", "alias"}
_MC_ADMIN_READONLY = {"info"}
_HIGHER_ORDER_REPEAT = {"map", "filter", "starmap", "repeat", "any", "all"}


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


def _funcs(tree: ast.AST) -> dict:
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _mutating_func_names(tree: ast.AST) -> set[str]:
    """Fixpoint set of functions that (transitively) perform a mutating `_mc` op."""
    funcs = _funcs(tree)
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


def _target_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _loaded_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _parents(tree: ast.AST) -> dict:
    p = {}
    for node in ast.walk(tree):
        for c in ast.iter_child_nodes(node):
            p[c] = node
    return p


def _ancestors(node, parents, stop):
    out = []
    cur = parents.get(node)
    while cur is not None and cur is not stop:
        out.append(cur)
        cur = parents.get(cur)
    return out  # nearest-first


def _is_higher_order_repeat(call: ast.Call) -> bool:
    name = _dotted(call.func)
    return name.split(".")[-1] in _HIGHER_ORDER_REPEAT


def _loop_taint(fors: list) -> set[str]:
    """Names tainted by the enclosing for-loops: loop targets + in-loop assignments derived from them."""
    taint = set()
    for f in fors:
        taint |= _target_names(f.target)
    if not fors:
        return taint
    outer = fors[-1]  # outermost enclosing loop (ancestors are nearest-first)
    changed = True
    while changed:
        changed = False
        for n in ast.walk(outer):
            val, tgts = None, []
            if isinstance(n, ast.Assign):
                val, tgts = n.value, n.targets
            elif isinstance(n, ast.AnnAssign) and n.value is not None:
                val, tgts = n.value, [n.target]
            elif isinstance(n, ast.AugAssign):
                val, tgts = n.value, [n.target]
            if val is not None and (_loaded_names(val) & taint):
                for t in tgts:
                    for nm in _target_names(t):
                        if nm not in taint:
                            taint.add(nm)
                            changed = True
    return taint


def _cp_is_download(call: ast.Call) -> bool:
    """A `cp` whose destination is provably LOCAL (a Path/tmp expression) is a download = a read, not a
    mutation. Anything else (remote f-string, bare literal, unknown) is treated as an upload (fail-closed)."""
    if not call.args or not isinstance(call.args[0], ast.List):
        return False
    elts = call.args[0].elts
    lead = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if not lead or lead[0] != "cp" or len(elts) < 3:
        return False
    d = ast.dump(elts[-1])
    return "Path" in d or "tmp" in d


def _resource_arg_nodes(call: ast.Call):
    """Resource-identity argument node(s) of a mutating `_mc([...])`; None if the shape is unrecognised."""
    if not call.args or not isinstance(call.args[0], ast.List):
        return None
    elts = call.args[0].elts
    lead = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if not lead:
        return None
    cmd = lead[0]
    if cmd == "cp":
        return [elts[-1]] if len(elts) >= 2 else None          # destination (remote) is the resource
    if cmd in ("rm", "rb", "mb"):
        return [elts[1]] if len(elts) >= 2 else None            # the path
    if cmd == "admin" and len(lead) >= 2 and lead[1] in ("user", "policy"):
        res = [elts[4]] if len(elts) >= 5 else []               # exact user/policy name
        if "attach" in lead and len(elts) >= 7:
            res.append(elts[6])                                 # + the --user target
        return res or None
    return None  # multipart / retention / unknown mutating command -> cannot localise -> fail-closed


def _mutating_recursion(tree: ast.AST, mutating: set[str]) -> bool:
    funcs = _funcs(tree)
    edges = {n: set() for n in funcs}
    for name, fn in funcs.items():
        for c in ast.walk(fn):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in funcs:
                edges[name].add(c.func.id)

    def reaches_self(start):
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            for nxt in edges.get(cur, ()):
                if nxt == start:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    return any(name in mutating and reaches_self(name) for name in funcs)


def _write_retry_violations(src: str, func_name: str = "minio_compat") -> list[str]:
    tree = ast.parse(src)
    mutating = _mutating_func_names(tree)
    funcs = _funcs(tree)
    target = funcs.get(func_name)
    assert target, f"function {func_name} not found"
    parents = _parents(tree)
    v = []

    # global: recursion among mutating functions, and retry decorators on mutating functions
    if _mutating_recursion(tree, mutating):
        v.append("recursive mutating function (retry via recursion)")
    for name, fn in funcs.items():
        if name in mutating:
            for d in fn.decorator_list:
                dn = _dotted(d if not isinstance(d, ast.Call) else d.func)
                if any(k in dn.lower() for k in ("retry", "backoff", "tenacity")):
                    v.append(f"retry decorator on mutating {name}")

    # higher-order repeats that carry a mutating helper / lambda / comprehension
    for call in ast.walk(target):
        if isinstance(call, ast.Call) and _is_higher_order_repeat(call):
            for a in call.args:
                if isinstance(a, ast.Name) and a.id in mutating:
                    v.append("mutating helper passed to a higher-order repeat")
                elif isinstance(a, ast.Lambda) and (_is_mc_mutating_anywhere(a) or
                        any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in mutating
                            for c in ast.walk(a))):
                    v.append("mutating lambda in a higher-order repeat")
                elif isinstance(a, (ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)) and (
                        _is_mc_mutating_anywhere(a) or
                        any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in mutating
                            for c in ast.walk(a))):
                    v.append("mutating comprehension in a higher-order repeat")

    # every mutating call site inside the target function
    for call in ast.walk(target):
        if not isinstance(call, ast.Call):
            continue
        direct = isinstance(call.func, ast.Name) and call.func.id == "_mc"
        helper = isinstance(call.func, ast.Name) and call.func.id in mutating and not direct
        if direct:
            if not _mc_call_is_mutating(call):
                continue
            if _cp_is_download(call):
                continue  # remote->local download is a read, not a mutation
        elif not helper:
            continue

        anc = _ancestors(call, parents, target)
        bad = None
        for a in anc:
            if isinstance(a, ast.While):
                bad = "while loop"
                break
            if isinstance(a, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                bad = "comprehension/generator"
                break
            if isinstance(a, ast.Call) and _is_higher_order_repeat(a):
                bad = "higher-order repeat"
                break
        if bad:
            v.append(f"mutating S3 op inside {bad}")
            continue

        fors = [a for a in anc if isinstance(a, (ast.For, ast.AsyncFor))]
        if not fors:
            continue  # single-shot -> fine
        if helper:
            v.append("mutating helper called inside a loop (no per-resource proof)")
            continue
        res = _resource_arg_nodes(call)
        if res is None:
            v.append("mutating _mc in a loop with unrecognised/dynamic resource shape")
            continue
        taint = _loop_taint(fors)
        if not any(_loaded_names(rn) & taint for rn in res):
            v.append("mutating _mc in a loop whose resource identity is NOT derived from the loop target")
    return v


def test_no_mutating_mc_repeat_in_runtime():
    """Real canary.py: no mutating S3 op may run inside any repeated context (single-shot invariant)."""
    violations = _write_retry_violations(_code(), "minio_compat")
    assert violations == [], "mutating S3 op in a repeated context: " + "; ".join(violations)


def _scan(body: str) -> list[str]:
    return _write_retry_violations("def _mc(a):\n    return (0, '', '')\n" + body, "minio_compat")


def test_write_retry_detector_flags_every_dangerous_form():
    W = '_mc(["cp", "x", "same-remote"])'          # mutating write to a CONSTANT resource
    danger = {
        "for-range": f"def minio_compat():\n    for _ in range(5):\n        {W}\n",
        "for-list-literal": f"def minio_compat():\n    for _ in [1, 2, 3]:\n        {W}\n",
        "for-tuple": f"def minio_compat():\n    for _ in (1, 2, 3):\n        {W}\n",
        "for-iter": f"def minio_compat():\n    for _ in iter([1, 2, 3]):\n        {W}\n",
        "for-enumerate": f"def minio_compat():\n    for _ in enumerate(range(5)):\n        {W}\n",
        "for-itertools-repeat": f"import itertools\ndef minio_compat():\n    for _ in itertools.repeat(None, 5):\n        {W}\n",
        "while": f"def minio_compat():\n    n = 0\n    while n < 5:\n        {W}\n        n += 1\n",
        "listcomp-range": f"def minio_compat():\n    [{W} for _ in range(5)]\n",
        "listcomp-literal": f"def minio_compat():\n    [{W} for _ in [1, 2, 3]]\n",
        "genexp-any": f"def minio_compat():\n    any({W} for _ in range(5))\n",
        "genexp-all-literal": f"def minio_compat():\n    all({W} for _ in [1, 2, 3])\n",
        "map-helper": f"def _w(x):\n    {W}\ndef minio_compat():\n    list(map(_w, [1, 2, 3]))\n",
        "starmap-helper": f"import itertools\ndef _w(x):\n    {W}\ndef minio_compat():\n    list(itertools.starmap(_w, [(1,)]))\n",
        "helper-1level": f"def _w():\n    {W}\ndef minio_compat():\n    for _ in range(3):\n        _w()\n",
        "helper-2level": f"def _w():\n    {W}\ndef _mid():\n    _w()\ndef minio_compat():\n    for _ in range(3):\n        _mid()\n",
        "helper-3level": f"def _w():\n    {W}\ndef _b():\n    _w()\ndef _a():\n    _b()\ndef minio_compat():\n    for _ in range(3):\n        _a()\n",
        "helper-ignores-var": f"def _w(x):\n    {W}\ndef minio_compat():\n    for i in [1, 2, 3]:\n        _w(i)\n",
        "target-in-print-only": f'def minio_compat():\n    for i in [1, 2, 3]:\n        print(i)\n        {W}\n',
        "target-as-timeout-only": 'def minio_compat():\n    for i in [1, 2, 3]:\n        _mc(["cp", "x", "const"])\n',
        "dynamic-cmd-loop": 'def minio_compat():\n    cmd = ["cp", "x", "y"]\n    for _ in range(5):\n        _mc(cmd)\n',
        "direct-recursion": f"def minio_compat():\n    _rec(3)\ndef _rec(n):\n    if n > 0:\n        {W}\n        _rec(n - 1)\n",
        "indirect-recursion": f"def minio_compat():\n    _a(3)\ndef _a(n):\n    _b(n)\ndef _b(n):\n    if n > 0:\n        {W}\n        _a(n - 1)\n",
        "cp-source-tainted-dst-const": 'def minio_compat():\n    for k in ["a", "b"]:\n        _mc(["cp", str(k), "alias/const-remote"])\n',
        "admin-add-name-const": 'def minio_compat():\n    for u in ["a", "b"]:\n        _mc(["admin", "user", "add", "adm", "fixed", u])\n',
        "rm-const-key": 'def minio_compat():\n    for i in created_keys:\n        print(i)\n        _mc(["rm", "alias/same-key"])\n',
    }
    missed = [name for name, src in danger.items() if not _scan(src)]
    assert not missed, "write-retry MISSED (stayed GREEN): " + ", ".join(missed)


def test_write_retry_detector_allows_safe_forms():
    safe = {
        "range-ls": 'def minio_compat():\n    for _ in range(30):\n        _mc(["ls", "u/b/"])\n',
        "range-stat-info": 'def minio_compat():\n    for _ in range(9):\n        _mc(["stat", "u/b/x"])\n        _mc(["admin", "info", "adm"])\n',
        "compute-only": "def minio_compat():\n    t = 0\n    for i in range(5):\n        t += i\n",
        "cleanup-key": 'def minio_compat():\n    for key in created_objs:\n        _mc(["rm", f"a/{bucket}/{key}"])\n',
        "cleanup-user-policy": 'def minio_compat():\n    for user, pol in created_users:\n        _mc(["admin", "user", "remove", "adm", user])\n        _mc(["admin", "policy", "remove", "adm", pol])\n',
        "seed-tuple-derived-key": 'def minio_compat():\n    for pref in ("pitr/", "logical/"):\n        key = f"{pref}seed"\n        _mc(["cp", str(seed), f"a/{bucket}/{key}"])\n',
        "matrix-role-op": 'def minio_compat():\n    for role, ops in M.items():\n        u = f"cu_{role}"\n        _mc(["admin", "user", "add", "adm", u, sec])\n        for op, prefix in ops:\n            obj = f"{prefix}o"\n            _mc(["cp", str(seed), f"u/{bucket}/{obj}"])\n',
        "single-shot": 'def minio_compat():\n    _mc(["cp", "x", "y"])\n',
    }
    fp = {name: _scan(src) for name, src in safe.items() if _scan(src)}
    assert not fp, "safe form FALSE-POSITIVE (unexpected RED): " + "; ".join(f"{k}:{val}" for k, val in fp.items())


def test_no_write_retry_decorator_or_helper_in_runtime():
    """No generic retry decorator/wrapper that could silently re-issue writes."""
    code = _code()
    assert "@retry" not in code and "tenacity" not in code and "backoff" not in code


# ---------------- SHA-256 runtime freeze (strong invariant) ----------------
def test_canary_runtime_frozen_by_sha256():
    """Freeze the whole canary runtime by byte-level SHA-256. Any change to ops/canary/canary.py — safe or
    not — flips this RED and requires a dedicated runtime-change PR + independent review before re-pinning."""
    digest = hashlib.sha256(CANARY_PY.read_bytes()).hexdigest()
    assert digest == _CANARY_RUNTIME_SHA256, (
        "canary runtime changed; perform dedicated runtime security review and update the pinned digest "
        f"only after approval (got {digest})"
    )


def test_sha256_pin_is_strict_and_unbypassable():
    """The freeze test itself must be a hard, strict, byte-level check with no bypass/auto-update."""
    # pinned digest is a lowercase 64-hex literal
    assert re.fullmatch(r"[0-9a-f]{64}", _CANARY_RUNTIME_SHA256)
    src = Path(__file__).read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "test_canary_runtime_frozen_by_sha256")
    body = ast.get_source_segment(src, fn)
    # hashes the real canary.py bytes with sha256, no text normalisation
    assert "hashlib.sha256" in body
    assert "CANARY_PY.read_bytes()" in body
    for bad in (".read_text", ".replace(", ".strip(", ".splitlines(", "normalize", "os.environ",
                "getenv", "UPDATE_GOLDEN", "hexdigest()  #"):
        assert bad not in body, f"freeze test must not use {bad!r}"
    # mismatch is an assertion (hard failure), not a warning; digest compared, not recomputed-as-expected
    assert "assert digest == _CANARY_RUNTIME_SHA256" in body
    assert "warnings" not in body and "warn(" not in body
    # the expected value is a module-level literal, never assigned from a computation
    pin = next(n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_CANARY_RUNTIME_SHA256"
                                                     for t in n.targets))
    assert isinstance(pin.value, ast.Constant) and isinstance(pin.value.value, str)
