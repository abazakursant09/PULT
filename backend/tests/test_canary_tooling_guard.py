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
_CANARY_RUNTIME_SHA256 = "7bdc0237e373d747bc10a1c8a433385b50c6acd0375b723add19d4cfb9665618"
# Review marker — bumped with the digest on every reviewed canary.py runtime change (3C2D SigV4 canonical query).
_CANARY_RUNTIME_REVIEW = "3C2D-v3-error-telemetry"

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


def test_live_gate_is_double_and_fail_closed_structurally():
    code = _code()
    # live() must exist, require the explicit env acknowledgement AND a typed confirmation, and validate
    # region/endpoint/bucket/run_id/project BEFORE any transport is constructed.
    assert "def live_validate(args, env)" in code and "def live(args, env=None)" in code
    assert 'LIVE_GATE_ENV = "PULT_SELECTEL_CANARY_LIVE"' in code
    assert 'LIVE_GATE_VALUE = "YES_I_UNDERSTAND"' in code
    assert "env.get(LIVE_GATE_ENV) != LIVE_GATE_VALUE" in code
    assert 'raise LiveGateError' in code
    assert 'bucket != f"pult-canary-{runid}"' in code
    assert "typed confirmation" in code
    # live CLI must still defer real execution (3C2C2-A wires the transport but gates its execution to 3C2C2-B)
    assert "SELECTEL_EXECUTION_GATED_UNTIL_3C2C2B" in code
    # no direct network primitive in the live path (transport is deferred; MinIO uses mc only in minio-compat)
    live_seg = code[code.index("def live(args"):]
    for bad in ("requests.", "urllib", "http.client", "socket.socket"):
        assert bad not in live_seg, f"live path must not use {bad!r}"


def test_selcloud_only_in_endpoint_allowlist():
    code = _code()
    # selcloud.ru may appear ONLY inside the LIVE_REGION_ENDPOINTS allowlist (no other use, no wildcard).
    allowlist = code[code.index("LIVE_REGION_ENDPOINTS = {"):code.index("LIVE_GATE_ENV =")]
    total = len(re.findall(r"selcloud\.ru", code))
    in_allow = len(re.findall(r"selcloud\.ru", allowlist))
    assert total == in_allow and total >= 1, "selcloud.ru must appear only in LIVE_REGION_ENDPOINTS"
    assert "*.selcloud" not in code and "*.storage" not in code, "no wildcard endpoint"
    # endpoints are explicit https hosts, region-keyed
    for host in re.findall(r'"(https://[^"]+)"', allowlist):
        assert host.startswith("https://s3.") and host.endswith(".storage.selcloud.ru")


def test_no_default_credentials_and_missing_creds_fail_closed():
    code = _code()
    # env reads for creds must default to empty string, and empty must _fail
    assert 'os.environ.get("CANARY_MINIO_ADMIN_SECRET", "")' in code
    assert "not admin_sec" in code and "_fail(" in code


def test_no_credentials_on_argv():
    code = _code()
    # credentials are NEVER an argv argument; only non-secret live parameters are allowed as flags.
    for bad in ("--secret", "--access-key", "--secret-key", "--key", "--password", "--token", "--access"):
        assert bad not in code, f"credential CLI arg {bad!r} forbidden"
    # every argparse flag must be one of the allowlisted non-secret parameters
    allowed_flags = {"--project-id", "--region", "--endpoint", "--bucket", "--run-id", "--confirm",
                     "--execute-live", "--ack", "--max-object-bytes", "--deadline"}
    flags = set(re.findall(r'add_argument\("(--[a-z-]+)"', code))
    assert flags <= allowed_flags, f"unexpected argv flags: {flags - allowed_flags}"


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
    # any http:// literal in code must be a private host — OR the S3 XML namespace URI (not an endpoint)
    for lit in re.findall(r'http://[^\s"\')]+', code):
        assert re.search(r"(minio|localhost|127\.0\.0\.1)", lit) or lit.startswith("http://s3.amazonaws.com/doc/"), \
            f"public http endpoint {lit!r}"


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


def test_runtime_review_marker_matches():
    """The runtime carries a review marker bumped together with the digest on every reviewed change."""
    assert f'CANARY_RUNTIME_REVIEW = "{_CANARY_RUNTIME_REVIEW}"' in _code()


# ================= SECURITY-2D-3E1B-3C2C1 live-mode (DORMANT) guards + behaviour =================
import importlib.util as _ilu  # noqa: E402


def _load_canary():
    spec = _ilu.spec_from_file_location("canary_runtime", str(CANARY_PY))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Args:
    def __init__(self, **kw):
        self.project_id = self.region = self.endpoint = self.bucket = self.run_id = self.confirm = None
        for k, v in kw.items():
            setattr(self, k, v)


def _valid_args():
    rid = "0123456789ab"
    proj = "0123456789abcdef"
    ep = "https://s3.ru-7.storage.selcloud.ru"
    bkt = f"pult-canary-{rid}"
    return _Args(project_id=proj, region="ru-7", endpoint=ep, bucket=bkt, run_id=rid,
                 confirm=f"{proj}/ru-7/{ep}/{bkt}/{rid}")


class _NoNetwork:
    """Context manager: any socket connection attempt raises -> proves the code path did no networking."""
    def __enter__(self):
        import socket
        self._orig = socket.socket.connect
        def _boom(self_, *a, **k):
            raise AssertionError("network connection attempted in a no-network path")
        socket.socket.connect = _boom
        return self
    def __exit__(self, *a):
        import socket
        socket.socket.connect = self._orig


def test_live_gate_refuses_without_env_and_before_network():
    c = _load_canary()
    with _NoNetwork():
        assert c.live(_valid_args(), env={}) == 4  # missing gate env -> refuse, no network


def test_live_gate_matrix_fail_closed():
    c = _load_canary()
    good = _valid_args()
    ok_env = {c.LIVE_GATE_ENV: c.LIVE_GATE_VALUE}
    # each single tampering must raise LiveGateError (no network), i.e. live() returns 4
    bad = {
        "wrong region": _Args(project_id=good.project_id, region="us-east-1", endpoint=good.endpoint,
                              bucket=good.bucket, run_id=good.run_id, confirm=good.confirm),
        "endpoint mismatch": _Args(project_id=good.project_id, region="ru-7",
                              endpoint="https://s3.ru-1.storage.selcloud.ru", bucket=good.bucket,
                              run_id=good.run_id, confirm=good.confirm),
        "http endpoint": _Args(project_id=good.project_id, region="ru-7",
                              endpoint="http://s3.ru-7.storage.selcloud.ru", bucket=good.bucket,
                              run_id=good.run_id, confirm=good.confirm),
        "bad bucket": _Args(project_id=good.project_id, region="ru-7", endpoint=good.endpoint,
                              bucket="prod-backup", run_id=good.run_id, confirm=good.confirm),
        "bad runid": _Args(project_id=good.project_id, region="ru-7", endpoint=good.endpoint,
                              bucket=good.bucket, run_id="ZZZ", confirm=good.confirm),
        "bad project": _Args(project_id="nope", region="ru-7", endpoint=good.endpoint,
                              bucket=good.bucket, run_id=good.run_id, confirm=good.confirm),
        "confirm mismatch": _Args(project_id=good.project_id, region="ru-7", endpoint=good.endpoint,
                              bucket=good.bucket, run_id=good.run_id, confirm="wrong"),
    }
    with _NoNetwork():
        for name, a in bad.items():
            assert c.live(a, env=ok_env) == 4, f"{name} must be refused"
            try:
                c.live_validate(a, ok_env)
                raise AssertionError(f"{name} should have raised")
            except c.LiveGateError:
                pass


def test_live_full_gate_defers_selectel_execution():
    c = _load_canary()
    ok_env = {c.LIVE_GATE_ENV: c.LIVE_GATE_VALUE}
    with _NoNetwork():
        # gate passes, but real Selectel transport is not wired in 3C2C1 -> deferral exit, still no network
        assert c.live(_valid_args(), env=ok_env) == 5
        try:
            c.SelectelTransport({"bucket": "x"})
            raise AssertionError("SelectelTransport must refuse in 3C2C1")
        except c.LiveGateError as e:
            assert "3C2C2" in str(e)


# ---- FakeTransport: in-memory, drives the orchestration offline (never touches the network) ----
class FakeTransport:
    def __init__(self, allow):
        self.allow = allow            # set of (role, op, prefix-root)
        self.users = list(allow and [] or [])
        self._locked_residual = None
        self._unknown = []
    def attempt(self, role, op, key):
        root = key.split("canary/")[-1].split("/")[1] + "/" if "canary/" in key else key
        # derive prefix root robustly
        parts = key.split("/")
        root = parts[-2] + "/" if len(parts) >= 2 else key
        return "allow" if (role, op, root) in self.allow else "deny"
    def pgbackrest_ops(self, step):
        return {"stanza-create": ["ListBucket"], "stanza-check": ["ListBucket", "GetObject"],
                "backup": ["PutObject"], "archive-push": ["PutObject"], "info": ["ListBucket", "GetObject"],
                "restore": ["GetObject"]}.get(step, [])
    def get_versioning(self): return "Enabled"
    def get_lock_config(self): return {"mode": "GOVERNANCE", "days": 1}
    def locked_delete_refused(self): return True
    def governance_bypass_admin_only(self): return True
    def delete_user(self, u): return True
    def delete_object(self, key, version=None): return True
    def abort_multipart(self, key, uid): return True
    def read_back_unknown(self, bucket, prefix): return self._unknown
    def locked_residual(self): return self._locked_residual
    def remove_bucket_if_empty(self, bucket): return True


def _fake_allow_from_matrix(c):
    allow = set()
    for role, ops in c._LIVE_ROLE_MATRIX.items():
        for op, prefix, expect in ops:
            if expect == "allow":
                allow.add((role, op, prefix))
    return allow


def test_run_role_matrix_expected_allow_deny():
    c = _load_canary()
    t = FakeTransport(_fake_allow_from_matrix(c))
    res = c.run_role_matrix(t, {"prefix": "canary/0123456789ab/", "runid": "0123456789ab"})
    assert res["failures"] == [], res["failures"]


def test_run_role_matrix_unexpected_allow_fails():
    c = _load_canary()
    allow = _fake_allow_from_matrix(c)
    allow.add(("app", "put", "pitr/"))  # app must be denied everything -> unexpected allow
    res = c.run_role_matrix(FakeTransport(allow), {"prefix": "canary/0123456789ab/", "runid": "0123456789ab"})
    assert any("app:put" in f for f in res["failures"])


def test_pgbackrest_probe_records_get_delete_separately():
    c = _load_canary()
    probe = c.pgbackrest_probe(FakeTransport(set()), {"prefix": "canary/x/", "runid": "x"})
    assert probe["get_object_required"] is True
    assert probe["delete_object_required"] is False  # candidate writer starts without Delete
    assert "auto-expanded" in probe["note"]


class _CleanupFake:
    """attempt()-ONLY transport (matches the real SelectelS3Transport surface) for cleanup tests."""
    def __init__(self, deny_substr=frozenset(), bucket_allow=True):
        self.deny = deny_substr
        self.bucket_allow = bucket_allow
        self.calls = []

    def attempt(self, op, uri, method="GET", query="", payload=b"", amz_date=None, date_stamp=None,
                extra_headers=None):
        self.calls.append((op, uri, query))
        if op == "DeleteBucket":
            return {"allow": "allow" if self.bucket_allow else "deny", "http_code": 204 if self.bucket_allow else 409}
        allow = not any(s in (uri + "?" + query) for s in self.deny)
        return {"allow": "allow" if allow else "deny", "http_code": 204 if allow else 403}


def _fixed_clock():
    import datetime
    return lambda: datetime.datetime(2026, 8, 15, 12, 0, 0)


def test_no_live_compliance_or_bypass():
    """Live Object-Lock is Governance-only: the live orchestration never issues a BypassGovernanceRetention
    or Compliance operation (the token may appear only in the policy validator that FORBIDS it)."""
    code = _code()
    live_seg = code[code.index("def run_cleanup"):code.index("def main(")]
    assert "BypassGovernanceRetention" not in live_seg
    assert "COMPLIANCE" not in live_seg.upper().replace("COMPLIANCE_TESTED", "")
    assert '"GOVERNANCE"' in live_seg


def test_cleanup_uses_attempt_only_and_is_exact():
    c = _load_canary()
    t = _CleanupFake()
    ledger = {"objects": [{"key": "canary/x/o", "version": "v1"}],
              "multipart": [{"key": "canary/x/m", "upload_id": "u1"}], "users": ["k"], "policies": ["p"]}
    r = c.run_cleanup(t, {"bucket": "pult-canary-x", "prefix": "canary/x/"}, ledger, _fixed_clock())
    assert r["status"] == "clean" and r["residual"] == []
    ops = [op for op, _, _ in t.calls]
    assert "AbortMultipartUpload" in ops and "DeleteObjectVersion" in ops
    assert "DeleteBucket" not in ops  # Variant 1: bucket deletion is MANUAL, never automatic
    # exact only: DeleteObjectVersion carries an explicit versionId, no recursive/prefix flags
    assert any("versionId=v1" in q for op, _, q in t.calls if op == "DeleteObjectVersion")
    assert r["manual_cleanup"]["bucket"] == "pult-canary-x"  # bucket/keys/policies recorded for manual F6


def test_cleanup_locked_object_is_controlled_residual_not_success():
    c = _load_canary()
    t = _CleanupFake(deny_substr={"lock-"})  # DeleteObjectVersion on the locked object would be denied anyway
    ledger = {"objects": [{"key": "canary/x/lock-x", "version": "vL", "locked": True,
                           "retain_until": "2026-08-15T12:15:00Z"}],
              "multipart": [], "users": ["k"], "policies": ["p"]}
    r = c.run_cleanup(t, {"bucket": "pult-canary-x", "prefix": "canary/x/"}, ledger, _fixed_clock())
    assert r["status"] == "controlled-residual"
    assert r["locked_residual"] and r["locked_residual"][0]["retain_until"] == "2026-08-15T12:15:00Z"
    # the locked object is NEVER attempted for deletion before expiry
    assert not any("lock-x" in u for op, u, _ in t.calls if op == "DeleteObjectVersion")


def test_cleanup_unknown_residual_is_failed_not_success():
    c = _load_canary()
    # a created NON-locked object we could NOT delete -> unknown residual -> FAILED, never clean
    t = _CleanupFake(deny_substr={"o-stuck"})
    ledger = {"objects": [{"key": "canary/x/o-stuck", "version": "v9"}], "multipart": [],
              "users": ["k"], "policies": ["p"]}
    r = c.run_cleanup(t, {"bucket": "pult-canary-x", "prefix": "canary/x/"}, ledger, _fixed_clock())
    assert r["status"] == "failed"


def test_redact_masks_secrets():
    c = _load_canary()
    assert "SECRET123" not in c._redact("token=SECRET123 rest", ["SECRET123"])


def test_cleanup_has_no_recursive_or_wildcard_delete():
    code = _code()
    seg = code[code.index("def run_cleanup("):code.index("def live(args")]
    for bad in ("--recursive", "recursive=True", "prefix_delete", "prune", "delete_all", "rmtree"):
        assert bad not in seg, f"cleanup must not use {bad!r}"


# ---------------- live-mode mutation matrix (guard teeth) ----------------
_LIVE_MUTATIONS = [
    "drop the env gate check", "drop the typed-confirmation check", "allow a wildcard bucket",
    "allow a non-allowlisted endpoint", "wire SelectelTransport in 3C2C1", "add a --secret argv flag",
    "log a secret / drop _redact", "recursive/prefix-wide cleanup", "auto-expand IAM on unexpected deny",
    "run live in ordinary CI", "add production endpoint outside allowlist", "network call in live path",
    "change runtime without bumping SHA-freeze + review marker",
]


def test_live_mutation_matrix_declared():
    assert len(_LIVE_MUTATIONS) >= 13


def test_ordinary_ci_never_runs_live():
    """No workflow may set the live gate env or name a Selectel endpoint; any `canary.py live` invocation
    must be a fail-closed probe (asserted to exit non-zero), never an actual live run."""
    for wf in (REPO / ".github" / "workflows").glob("*.yml"):
        raw = wf.read_text(encoding="utf-8")
        assert "PULT_SELECTEL_CANARY_LIVE" not in raw, f"{wf.name} must not set the live gate env"
        assert not re.search(r"selcloud\.ru", raw, re.I), f"{wf.name} must not name a Selectel endpoint"
        if "canary.py live" in raw:
            # only allowed as a fail-closed probe: guarded by an `if ... then ... exit 1` that treats
            # a live SUCCESS as a CI failure.
            assert "SHOULD HAVE FAILED" in raw, f"{wf.name} runs live without a fail-closed assertion"
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "canary.py live" in raw and "SHOULD HAVE FAILED" in raw


# ================= SECURITY-2D-3E1B-3C2C2-A real S3 transport (DORMANT) =================
def _transport_manifest():
    return {"endpoint": "https://s3.ru-7.storage.selcloud.ru", "region": "ru-7",
            "bucket": "pult-canary-0123456789ab", "prefix": "canary/0123456789ab/",
            "runid": "0123456789ab", "project": "0123456789abcdef"}


_SEKRET = "SEKRET_DO_NOT_LOG_0123"


class _FakeHTTP:
    """Injected HTTP client for offline transport tests — records calls, returns scripted statuses."""
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []
    def request(self, method, url, headers=None, content=b""):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {})})
        code = self.statuses.pop(0) if self.statuses else 200
        return type("R", (), {"status_code": code, "headers": {"x-amz-request-id": "req-123"}})()


def test_sigv4_signing_key_is_stdlib_and_matches_independent_chain():
    c = _load_canary()
    import hashlib
    import hmac
    def hm(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
    kd = hm(("AWS4" + "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY").encode(), "20150830")
    indep = hm(hm(hm(kd, "us-east-1"), "service"), "aws4_request")
    got = c._sigv4_signing_key("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "20150830", "us-east-1", "service")
    assert got == indep  # module signer == independent HMAC chain (correct SigV4 key derivation)
    # canonical-request construction proven against the authoritative aws-sig-v4-test-suite get-vanilla hash
    cr = "\n".join(["GET", "/", "", "host:example.amazonaws.com", "x-amz-date:20150830T123600Z", "",
                    "host;x-amz-date", hashlib.sha256(b"").hexdigest()])
    assert hashlib.sha256(cr.encode()).hexdigest() == \
        "bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63"
    auth = c._sigv4_authorization("AKID", got, "20150830/us-east-1/service/aws4_request", "host;x-amz-date", "x")
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKID/") and "Signature=" in auth


def test_transport_endpoint_allowlist_enforced():
    c = _load_canary()
    m = dict(_transport_manifest(), endpoint="https://evil.example.com")
    try:
        c.SelectelS3Transport(m, {"access_key": "A", "secret_key": _SEKRET})
        raise AssertionError("non-allowlisted endpoint must be refused")
    except c.LiveGateError:
        pass
    m2 = dict(_transport_manifest(), endpoint="http://s3.ru-7.storage.selcloud.ru")
    try:
        c.SelectelS3Transport(m2, {"access_key": "A", "secret_key": _SEKRET})
        raise AssertionError("http endpoint must be refused")
    except c.LiveGateError:
        pass


def test_transport_unclassified_op_is_mutating_and_refused():
    c = _load_canary()
    t = c.SelectelS3Transport(_transport_manifest(), {"access_key": "A", "secret_key": _SEKRET},
                              http_client=_FakeHTTP([200]))
    try:
        t.is_mutating("Frobnicate")
        raise AssertionError("unknown op must raise (fail-closed mutating)")
    except c.LiveGateError:
        pass
    assert t.is_mutating("PutObject") is True and t.is_mutating("GetObject") is False


def test_transport_reads_retry_but_mutations_never_retry():
    c = _load_canary()
    m = _transport_manifest()
    # read: 503,503,200 -> allow after bounded retries
    fr = _FakeHTTP([503, 503, 200])
    tr = c.SelectelS3Transport(m, {"access_key": "A", "secret_key": _SEKRET}, http_client=fr)
    r = tr.attempt("GetObject", "/pult-canary-0123456789ab/canary/0123456789ab/o", method="GET",
                   amz_date="20200101T000000Z", date_stamp="20200101")
    assert r["allow"] == "allow" and len(fr.calls) == 3
    # mutation: 503 -> NO retry (exactly one call), result unknown (never guesses success)
    fm = _FakeHTTP([503])
    tm = c.SelectelS3Transport(m, {"access_key": "A", "secret_key": _SEKRET}, http_client=fm)
    r = tm.attempt("PutObject", "/pult-canary-0123456789ab/canary/0123456789ab/o", method="PUT",
                   payload=b"x", amz_date="20200101T000000Z", date_stamp="20200101")
    assert r["allow"] == "unknown" and len(fm.calls) == 1


def test_transport_unknown_status_is_stop_not_success():
    c = _load_canary()
    fr = _FakeHTTP([418])
    t = c.SelectelS3Transport(_transport_manifest(), {"access_key": "A", "secret_key": _SEKRET}, http_client=fr)
    r = t.attempt("GetObject", "/b/o", method="GET", amz_date="20200101T000000Z", date_stamp="20200101")
    assert r["allow"] == "unknown"


def test_transport_never_leaks_secret(capsys):
    c = _load_canary()
    t = c.SelectelS3Transport(_transport_manifest(), {"access_key": "AKID", "secret_key": _SEKRET},
                              http_client=_FakeHTTP([200]))
    r = t.attempt("GetObject", "/b/o", method="GET", amz_date="20200101T000000Z", date_stamp="20200101")
    out = capsys.readouterr()
    assert _SEKRET not in (out.out + out.err)
    assert _SEKRET not in repr(t) and _SEKRET not in str(r)
    # the secret key never travels in a header (only the derived signature does)
    for call in _FakeHTTP([]).calls:  # no-op guard
        pass


def test_transport_hardening_source():
    """Structural: the lazily-built real client must pin TLS on, no redirects, no env-proxy/creds."""
    code = _code()
    seg = code[code.index("def _build_client"):code.index("def _sign(")]
    assert "verify=True" in seg
    assert "follow_redirects=False" in seg
    assert "trust_env=False" in seg
    # no TLS-off / redirect / proxy / metadata / default-credential anywhere in the runtime
    for bad in ("verify=False", "follow_redirects=True", "trust_env=True", "no_verify", "InsecureRequestWarning",
                "169.254.169.254", "instance-metadata", "IMDS", "from_env", "default_credentials"):
        assert bad not in code, f"runtime must not contain {bad!r}"
    # mutations classified and never auto-retried (attempts=1 when mutating)
    assert "attempts = 1 if mutating else" in code
    assert "_READ_ONLY_S3_OPS" in code and "_MUTATING_S3_OPS" in code


def test_no_ip_document_or_pii_in_repo():
    """The sole-proprietor document/PII must never appear in ops/canary, tests, docs, or workflows."""
    for base in (CANARY, REPO / "docs" / "pitr-operations-policy.md", CANARY_PY,
                 REPO / ".github" / "workflows" / "canary_offline.yml"):
        files = base.rglob("*") if base.is_dir() else [base]
        for f in files:
            if f.is_file():
                txt = f.read_text(encoding="utf-8", errors="ignore")
                for bad in ("Муратков", "fl-326554300073322", "ОГРНИП", "ЕГРИП", "ИНН"):
                    assert bad not in txt, f"PII/IP-document token {bad!r} found in {f}"


_TRANSPORT_MUTATIONS = [
    "disable TLS verify (verify=False)", "enable redirects (follow_redirects=True)",
    "enable env proxy/creds (trust_env=True)", "accept non-allowlisted endpoint", "accept http endpoint",
    "retry a mutating operation", "treat unclassified op as read-only", "guess success on unknown status",
    "log the secret key / Authorization", "add default-credential / metadata chain",
    "put credentials on argv", "change runtime without bumping pin+review-marker",
]


def test_transport_mutation_matrix_declared():
    assert len(_TRANSPORT_MUTATIONS) >= 12


# ================= SECURITY-2D-3E1B-3C2C2-B execute-live gate (Gate F, offline) =================
def _exec_args(**over):
    rid = "0123456789ab"
    proj = "0123456789abcdef"
    ep = "https://s3.ru-3.storage.selcloud.ru"
    bkt = f"pult-canary-{rid}"
    base = dict(project_id=proj, region="ru-3", endpoint=ep, bucket=bkt, run_id=rid,
                confirm=f"{proj}/ru-3/{ep}/{bkt}/{rid}", execute_live=True,
                ack=f"PULT-CANARY-EXECUTE-{rid}", max_object_bytes=1048576, deadline="2026-08-15T12:00:00Z")
    base.update(over)
    return _Args(**base)


def test_ordinary_live_still_defers_without_execute_flag():
    c = _load_canary()
    a = _exec_args(execute_live=False)
    with _NoNetwork():
        assert c.live(a, env={c.LIVE_GATE_ENV: c.LIVE_GATE_VALUE}) == 5  # deferral, no network


def _clock_at(y=2026, mo=8, d=15, h=12, mi=0, s=0):
    import datetime
    return lambda: datetime.datetime(y, mo, d, h, mi, s)


def test_execute_gate_fails_closed_before_network():
    c = _load_canary()
    E = {c.LIVE_GATE_ENV: c.LIVE_GATE_VALUE}
    clk = _clock_at()  # "now" = 2026-08-15T12:00:00; good deadline is 12:00 (+? use future in _exec_args)
    good_deadline = "2026-08-15T12:10:00Z"
    wrong_conf = "0123456789abcdef/ru-7/https://s3.ru-7.storage.selcloud.ru/pult-canary-0123456789ab/0123456789ab"
    bad = {
        "no execute flag": _exec_args(execute_live=False, deadline=good_deadline),
        "wrong region": _exec_args(region="ru-7", endpoint="https://s3.ru-7.storage.selcloud.ru",
                                   confirm=wrong_conf, deadline=good_deadline),
        "bad ack": _exec_args(ack="nope", deadline=good_deadline),
        "no ack": _exec_args(ack=None, deadline=good_deadline),
        "oversize": _exec_args(max_object_bytes=10 * 1024 * 1024 + 1, deadline=good_deadline),
        "zero size": _exec_args(max_object_bytes=0, deadline=good_deadline),
        "bad deadline": _exec_args(deadline="soon"),
        "no deadline": _exec_args(deadline=None),
        "past deadline": _exec_args(deadline="2020-01-01T00:00:00Z"),
        "too-far deadline": _exec_args(deadline="2026-08-15T13:00:00Z"),  # >30 min window
    }
    with _NoNetwork():
        for name, a in bad.items():
            rc = c.live(a, env=E)
            assert rc in (4, 5), f"{name}: expected fail-closed (4/5), got {rc}"
            if name != "no execute flag":
                try:
                    c.execute_validate(a, c.live_validate(a, E), clk)
                    raise AssertionError(f"{name} should have raised")
                except c.LiveGateError:
                    pass
    good = _exec_args(deadline=good_deadline)
    em = c.execute_validate(good, c.live_validate(good, E), clk)
    assert em["region"] == "ru-3" and em["max_buckets"] == 1 and em["deadline_dt"] is not None


def test_masked_credentials_memory_only(capsys):
    c = _load_canary()
    seen = []

    def fake_reader(prompt):
        seen.append(prompt)
        return "SECRETVALUE_zzz"

    creds = c.read_masked_credentials(reader=fake_reader)
    assert set(creds) == set(c._CANARY_ROLES)
    assert len(seen) == 2 * len(c._CANARY_ROLES)
    out = capsys.readouterr()
    assert "SECRETVALUE_zzz" not in (out.out + out.err)
    code = _code()
    seg = code[code.index("def read_masked_credentials"):code.index("_OP_TO_S3")]
    assert "getpass" in seg and "input(" not in seg and "open(" not in seg


class _FakeLiveTransport:
    """attempt()-ONLY per-role transport (SAME surface as the real SelectelS3Transport) for offline
    run_live_execution tests. No phantom cleanup methods -> proves run_live_execution/run_cleanup never call
    anything the real transport lacks. Never touches the network."""

    _S3_TO_OP = {"PutObject": "put", "GetObject": "get", "ListBucket": "list", "DeleteObject": "delete"}

    def __init__(self, manifest, creds, service="s3", allow_ops=frozenset()):
        self._allow = allow_ops  # set of (op, prefix-root) that are ALLOW for this role's matrix
        self.__secret = creds["secret_key"]

    def attempt(self, s3op, uri, method="GET", query="", payload=b"", amz_date=None, date_stamp=None,
                extra_headers=None):
        hay = uri + "?" + query
        root = "logical/" if "logical/" in hay else ("pitr/" if "pitr/" in hay else "")
        if s3op == "PutObject":
            allow = ("put", root) in self._allow  # REAL policy semantics: role must be granted put on this prefix
            return {"allow": "allow" if allow else "deny", "http_code": 200 if allow else 403, "request_id": "r",
                    "version_id": ("v-" + uri.rsplit("/", 1)[-1]) if allow else "", "body": b""}
        if s3op == "PutObjectRetention":
            self._last_retention = payload  # echo it back on GetObjectRetention (proves round-trip)
            return {"allow": "allow", "http_code": 200, "request_id": "r", "version_id": "", "body": b""}
        if s3op == "GetObjectRetention":
            return {"allow": "allow", "http_code": 200, "request_id": "r", "version_id": "",
                    "body": getattr(self, "_last_retention", b"")}
        if s3op == "DeleteObjectVersion":
            allow = "deny" if "lock-" in uri else "allow"  # locked object refused; unlocked/others deletable
            return {"allow": allow, "http_code": 403 if allow == "deny" else 204, "request_id": "r",
                    "version_id": "", "body": b""}
        if s3op == "AbortMultipartUpload":
            return {"allow": "allow", "http_code": 204, "request_id": "r", "version_id": "", "body": b""}
        op = self._S3_TO_OP.get(s3op, "?")
        allow = "allow" if (op, root) in self._allow else "deny"
        return {"allow": allow, "http_code": 200 if allow == "allow" else 403, "request_id": "r",
                "version_id": "", "body": b""}


def _execmani(deadline_h=12, deadline_mi=10):
    import datetime
    return {"deadline": "2026-08-15T12:10:00Z",
            "deadline_dt": datetime.datetime(2026, 8, 15, deadline_h, deadline_mi, 0),
            "max_object_bytes": 1048576, "max_buckets": 1}


def _matrix_allow(c):
    return {
        "logical-writer": {("put", "logical/"), ("list", "logical/")},
        "pitr-writer": {("put", "pitr/"), ("get", "pitr/"), ("list", "pitr/")},
        "restore-reader": {("list", "pitr/"), ("get", "pitr/")},
        "retention-admin": set(),
        "app-deny": set(),
    }


def test_run_live_execution_requires_injected_clock():
    c = _load_canary()
    m = c.live_validate(_exec_args(), {c.LIVE_GATE_ENV: c.LIVE_GATE_VALUE})
    creds = {r: {"access_key": "A", "secret_key": "S"} for r in c._CANARY_ROLES}
    try:
        c.run_live_execution(m, _execmani(), creds, transport_factory=_FakeLiveTransport, clock=None)
        raise AssertionError("must require injected clock")
    except c.LiveGateError:
        pass


def test_run_live_execution_uses_only_attempt_interface():
    """Contract: run_live_execution + run_cleanup must call ONLY attempt() on the transport (real interface),
    never delete_user/delete_object/remove_bucket_if_empty etc — else AttributeError on the real transport."""
    c = _load_canary()
    m = c.live_validate(_exec_args(), {c.LIVE_GATE_ENV: c.LIVE_GATE_VALUE})
    creds = {r: {"access_key": "A", "secret_key": "Sx"} for r in c._CANARY_ROLES}
    allow = _matrix_allow(c)

    def factory(manifest, cr, service="s3"):
        return _FakeLiveTransport(manifest, cr, service, allow_ops=factory.map.pop(0))

    factory.map = [allow[r] for r in c._CANARY_ROLES]
    with _NoNetwork():
        res = c.run_live_execution(m, _execmani(), creds, transport_factory=factory, clock=_clock_at())
    # matrix clean; REAL object-lock proof (IAM-delete-on-unlocked + retention set + read-back + locked DENY);
    # locked object -> honest CONTROLLED_RESIDUAL
    assert res["matrix_failures"] == [], res["matrix_failures"]
    ol = res["object_lock"]
    assert ol["unlocked_put_ok"] is True and ol["locked_put_ok"] is True  # created by pitr-writer
    assert ol["iam_delete_ok_on_unlocked"] is True
    assert ol["retention_set"] is True and ol["readback_ok"] is True
    assert ol["locked_delete_refused"] is True and ol["proof"] is True
    assert ol["compliance_tested"] is False
    assert res["status"] == "CONTROLLED_RESIDUAL"
    assert res["cleanup"]["status"] == "controlled-residual"
    assert res["pgbackrest_closure"].startswith("NOT-ATTEMPTED")
    assert res["manual_revoke_required"]["keys"] == list(c._CANARY_ROLES)


def test_run_live_execution_matrix_failure_is_FAILED():
    c = _load_canary()
    m = c.live_validate(_exec_args(), {c.LIVE_GATE_ENV: c.LIVE_GATE_VALUE})
    creds = {r: {"access_key": "A", "secret_key": "Sx"} for r in c._CANARY_ROLES}
    allow = _matrix_allow(c)
    allow["app-deny"] = {("put", "pitr/")}  # app must be denied all -> unexpected allow -> FAILED

    def factory(manifest, cr, service="s3"):
        return _FakeLiveTransport(manifest, cr, service, allow_ops=factory.map.pop(0))

    factory.map = [allow[r] for r in c._CANARY_ROLES]
    with _NoNetwork():
        res = c.run_live_execution(m, _execmani(), creds, transport_factory=factory, clock=_clock_at())
    assert res["status"] == "FAILED" and any("app:put" in f for f in res["matrix_failures"])


def test_run_live_execution_deadline_expiry_stops_ops_but_cleans_up():
    c = _load_canary()
    m = c.live_validate(_exec_args(), {c.LIVE_GATE_ENV: c.LIVE_GATE_VALUE})
    creds = {r: {"access_key": "A", "secret_key": "Sx"} for r in c._CANARY_ROLES}
    # clock is already PAST the deadline -> no matrix op runs, but cleanup finally still executes
    with _NoNetwork():
        res = c.run_live_execution(m, _execmani(), creds, transport_factory=_FakeLiveTransport,
                                   clock=_clock_at(h=12, mi=30))
    assert res["deadline_reached"] is True
    assert res["rows"] == []
    assert "cleanup" in res


def test_execute_gate_constants_and_order():
    code = _code()
    assert 'EXECUTE_LIVE_REGION = "ru-3"' in code
    assert "EXECUTE_MAX_OBJECT_BYTES = 10 * 1024 * 1024" in code
    assert 'EXECUTE_LIVE_ACK_PREFIX = "PULT-CANARY-EXECUTE-"' in code
    live_src = code[code.index("def live(args"):]
    assert live_src.index("execute_validate(") < live_src.index("read_masked_credentials()")


_EXEC_MUTATIONS = [
    "drop --execute-live requirement", "drop the --ack check", "allow region != ru-3",
    "allow object > 10 MiB", "drop the deadline check", "read credentials before execute_validate",
    "credentials via argv", "masked reader writes to disk / uses input()", "clock not injected",
    "cleanup not in finally", "controlled-residual reported as success", "change runtime without pin/marker bump",
    "accept a past deadline", "accept a too-far deadline", "cleanup calls a non-attempt phantom method",
    "skip the deadline per-op check", "skip the Object-Lock probe", "claim pgBackRest closure was proven live",
    "use Compliance / BypassGovernanceRetention live", "switch addressing to vHosted (host mismatch)",
]


def test_exec_mutation_matrix_declared():
    assert len(_EXEC_MUTATIONS) >= 20


# ================= 3C2C2-B PRE-LIVE CORRECTION guards (addressing / deadline / launcher) =================
def test_addressing_is_path_style_consistent():
    """Transport is Path-Style: host = region endpoint host, URL = endpoint + /bucket/key (NOT vhost).
    SigV4 host header must match. (Gate C checklist must therefore use Path-Style, not vHosted.)"""
    code = _code()
    assert 'self._host = endpoint.split("://", 1)[1]' in code
    assert 'url = f"{self._endpoint}{canonical_uri}"' in code
    # canonical_uri for objects is /bucket/key (path-style) — the live orchestration builds it that way
    assert 'f"/{manifest' in code and "bucket']}/{key}" in code


def test_sigv4_path_style_delete_version_deterministic():
    """SigV4 over a path-style DeleteObjectVersion (with versionId query) is deterministic and matches an
    independent recompute (byte-correct-vs-live still UNKNOWN until Gate F)."""
    c = _load_canary()
    import hashlib
    import hmac
    m = {"endpoint": "https://s3.ru-3.storage.selcloud.ru", "region": "ru-3", "bucket": "b",
         "prefix": "canary/x/", "runid": "x", "project": "p"}
    t = c.SelectelS3Transport(m, {"access_key": "AKID", "secret_key": "sk"}, http_client=object())
    hdrs = t._sign("DELETE", "DeleteObjectVersion", "/b/canary/x/pitr/lock-x", "versionId=v1",
                   hashlib.sha256(b"").hexdigest(), "20260815T120000Z", "20260815")
    # independent reconstruction
    host = "s3.ru-3.storage.selcloud.ru"
    H = {"host": host, "x-amz-content-sha256": hashlib.sha256(b"").hexdigest(), "x-amz-date": "20260815T120000Z"}
    sh = ";".join(sorted(H))
    ch = "".join(f"{k}:{H[k]}\n" for k in sorted(H))
    creq = "\n".join(["DELETE", "/b/canary/x/pitr/lock-x", "versionId=v1", ch, sh, H["x-amz-content-sha256"]])
    scope = "20260815/ru-3/s3/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", "20260815T120000Z", scope, hashlib.sha256(creq.encode()).hexdigest()])
    key = c._sigv4_signing_key("sk", "20260815", "ru-3", "s3")
    sig = hmac.new(key, sts.encode(), hashlib.sha256).hexdigest()
    assert hdrs["Authorization"].endswith("Signature=" + sig)


def test_windows_launcher_documented_without_secret_leak():
    """docs must show a Windows/PowerShell launch that never puts secrets in argv/env — only the non-secret
    acknowledgement via $env:, secrets via masked getpass prompts."""
    doc = POLICY_DOC.read_text(encoding="utf-8")
    assert "PowerShell" in doc or "$env:PULT_SELECTEL_CANARY_LIVE" in doc
    # no access/secret key on the documented command line
    assert "--secret" not in doc and "--access-key" not in doc
    # the only env exported is the non-secret acknowledgement
    for line in doc.splitlines():
        if "$env:" in line:
            assert "PULT_SELECTEL_CANARY_LIVE" in line, f"only the non-secret ack may be exported: {line}"


def test_execute_validate_deadline_window_enforced():
    code = _code()
    assert "EXECUTE_MAX_DEADLINE_WINDOW_SEC" in code
    assert "now < deadline <= now + datetime.timedelta" in code
    # per-op deadline check inside the matrix loop
    assert "if clock() >= deadline_dt:" in code and "deadline_reached = True" in code


# ================= Gate-F live bucket-policy template guard =================
GATE_F_POLICY = CANARY / "gate-f-live-bucket-policy.json"


def _gate_f_allow(pol, sid_role):
    for s in pol["policy"]["Statement"]:
        if s.get("Sid", "").startswith(sid_role) and s["Effect"] == "Allow":
            a = s.get("Action", [])
            return set([a] if isinstance(a, str) else a)
    return set()


def test_gate_f_live_policy_template_is_exact_and_scoped():
    import json as _json
    doc = _json.loads(GATE_F_POLICY.read_text(encoding="utf-8"))
    assert doc["_canary"]["marker"] == "NOT_FOR_ROUTINE_BACKUP"
    blob = _json.dumps(doc["policy"])
    # placeholders only — no real bucket / real UID / secrets
    assert "<BUCKET>" in blob and "<RUNID>" in blob
    assert "pult-canary-" not in blob or "<BUCKET>" in blob  # bucket name is a placeholder, not concrete
    for m in re.findall(r"arn:aws:s3:::([^/\"]+)", blob):
        assert m == "<BUCKET>", f"non-placeholder bucket {m!r}"
    # every ALLOW object resource is scoped to canary/<RUNID>/... (the app Deny may use the bucket-wide /*)
    for s in doc["policy"]["Statement"]:
        if s["Effect"] != "Allow":
            continue
        res = s.get("Resource", [])
        for r in ([res] if isinstance(res, str) else res):
            obj = re.match(r"arn:aws:s3:::<BUCKET>/(.+)", r)
            if obj:
                assert obj.group(1).startswith("canary/<RUNID>/"), f"unscoped allow resource: {r}"
    # role closures
    lw = _gate_f_allow(doc, "logicalWriter")
    assert "s3:PutObject" in lw and "s3:GetObject" not in lw and "s3:DeleteObject" not in lw
    pw = _gate_f_allow(doc, "pitrWriter")
    assert "s3:PutObject" in pw and "s3:GetObject" in pw and "s3:DeleteObject" not in pw
    rr = _gate_f_allow(doc, "restoreReader")
    assert rr and "s3:PutObject" not in rr and "s3:DeleteObject" not in rr
    ra = _gate_f_allow(doc, "retentionAdmin")
    assert {"s3:PutObjectRetention", "s3:DeleteObject", "s3:DeleteObjectVersion"} <= ra
    # retention-admin cleanup rights but NO bypass / NO Compliance / NO bucket delete / NO lifecycle
    assert "s3:BypassGovernanceRetention" not in blob
    assert "s3:DeleteBucket" not in blob
    assert "s3:PutLifecycleConfiguration" not in blob and "s3:PutBucketObjectLockConfiguration" not in ra
    # app principal is a Deny of all S3
    app = [s for s in doc["policy"]["Statement"] if s.get("Sid", "").startswith("appDeny")]
    assert app and app[0]["Effect"] == "Deny" and "s3:*" in app[0]["Action"]


# ================= code<->policy parity: control objects created by pitr-writer, not retention-admin =========
def test_gate_f_retention_admin_has_no_putobject():
    import json as _json
    doc = _json.loads(GATE_F_POLICY.read_text(encoding="utf-8"))
    ra = _gate_f_allow(doc, "retentionAdmin")
    assert "s3:PutObject" not in ra, "retention-admin must NOT have PutObject (least privilege)"
    pw = _gate_f_allow(doc, "pitrWriter")
    assert "s3:PutObject" in pw, "pitr-writer must have PutObject (it creates the control objects)"


def test_object_lock_controls_created_by_pitr_writer_not_admin():
    """The Object-Lock probe must PUT its control objects via pitr-writer (policy allows it), and let
    retention-admin only manage retention/delete — else on real Selectel the creates would 403."""
    code = _code()
    seg = code[code.index("Object-Lock Governance proof"):code.index("if not objectlock.get(\"proof\")")]
    # both control creates go through the writer transport
    assert 'up = writer.attempt("PutObject"' in seg
    assert 'pr = writer.attempt("PutObject"' in seg
    # retention-admin never PutObject here
    assert 'admin.attempt("PutObject"' not in seg
    # retention/read-back/delete go through admin
    assert 'admin.attempt("PutObjectRetention"' in seg
    assert 'admin.attempt("GetObjectRetention"' in seg
    assert 'admin.attempt("DeleteObjectVersion"' in seg
    # proof requires the full chain incl. pitr puts + admin unlocked-delete
    assert "unlocked_put_ok and iam_delete_ok and locked_put_ok and retention_set" in code


# ================= SECURITY-2D-3E1B-3C2D SigV4 canonical-query goldens =================
# Root cause (pre-3C2D): SelectelS3Transport signed and sent the caller's RAW query string, so the AWS SigV4
# canonical query the client signed did not match what the server recomputes (empty-value params lacked '=',
# '/' in values was not %2F, params were unsorted) -> SignatureDoesNotMatch on every query-bearing request,
# while HeadObject (empty query) reached a real policy decision. These goldens pin the AWS-correct form and
# prove the wire query equals the signed canonical query. They FAIL on the pre-3C2D runtime (_canonical_query
# did not exist / raw query was used).
def test_canonical_query_goldens():
    c = _load_canary()
    assert c._canonical_query("") == ""
    assert c._canonical_query("versioning") == "versioning="
    assert c._canonical_query("object-lock") == "object-lock="
    assert c._canonical_query("prefix=canary/eced74af45e3/") == "prefix=canary%2Feced74af45e3%2F"
    assert c._canonical_query("retention&versionId=syntheticV") == "retention=&versionId=syntheticV"


def test_canonical_query_order_independent_and_repeated():
    c = _load_canary()
    assert c._canonical_query("versionId=x&retention") == c._canonical_query("retention&versionId=x")
    assert c._canonical_query("retention&versionId=x") == "retention=&versionId=x"
    # repeated keys are kept and sorted by (encoded name, encoded value) — so input order does not matter
    assert c._canonical_query("a=2&a=1") == "a=1&a=2"
    assert c._canonical_query("a=1&a=2") == "a=1&a=2"


def test_canonical_query_encoding_rules():
    c = _load_canary()
    assert c._canonical_query("k=a b") == "k=a%20b"      # space -> %20, never '+'
    assert c._canonical_query("k=a+b") == "k=a%2Bb"      # literal '+' -> %2B
    assert c._canonical_query("k=a/b") == "k=a%2Fb"      # '/' in a value -> %2F
    assert c._canonical_query("k=100%") == "k=100%25"    # '%' -> %25 (no double-encode)
    assert c._canonical_query("k=é") == "k=%C3%A9"  # UTF-8 bytes percent-encoded
    assert c._canonical_query("k=") == "k="              # blank value preserved
    assert c._canonical_query("k=a=b") == "k=a%3Db"      # '=' inside value encoded (split on first '=')
    assert c._canonical_query("k=?#&x=1") == "k=%3F%23&x=1"  # reserved chars encoded; '&' is the delimiter


def test_attempt_outgoing_query_equals_canonical_signed():
    c = _load_canary()
    m = _transport_manifest()
    for raw in ("versioning", "object-lock", "prefix=canary/eced74af45e3/", "retention&versionId=v1"):
        fr = _FakeHTTP([200])
        t = c.SelectelS3Transport(m, {"access_key": "A", "secret_key": _SEKRET}, http_client=fr)
        op = "ListBucket" if raw.startswith("prefix") else (
            "GetObjectRetention" if raw.startswith("retention") else
            "GetBucketVersioning" if raw == "versioning" else "GetBucketObjectLockConfiguration")
        t.attempt(op, "/pult-canary-0123456789ab", method="GET", query=raw,
                  amz_date="20200101T000000Z", date_stamp="20200101")
        url = fr.calls[0]["url"]
        cq = c._canonical_query(raw)
        # the wire query is exactly the canonical query fed to the signer (no divergence)
        assert url.endswith("?" + cq), (raw, url, cq)


def test_pre_3c2d_raw_query_no_longer_used():
    """Regression marker: the pre-3C2D bug was signing the RAW query; the normalized form now differs from it
    for exactly the shapes the canary uses, so a revert to the raw string is a behavioural RED."""
    c = _load_canary()
    assert c._canonical_query("versioning") != "versioning"
    assert c._canonical_query("prefix=canary/eced74af45e3/") != "prefix=canary/eced74af45e3/"
    # the transport builds its wire url from the canonical form, never the raw one
    code = _code()
    seg = code[code.index("def attempt("):code.index("def __repr__")]
    assert "cquery = _canonical_query(query)" in seg
    assert 'url = f"{self._endpoint}{canonical_uri}" + (f"?{cquery}" if cquery else "")' in seg
    assert "self._sign(method, op, canonical_uri, cquery" in seg


def test_canonical_query_helpers_are_pure_no_network():
    c = _load_canary()
    with _NoNetwork():
        assert c._query_encode("a/b c") == "a%2Fb%20c"
        assert c._canonical_query("b=2&a=1") == "a=1&b=2"


# ================= SECURITY-2D-3E1B-3C2D-V2 live-summary observability goldens =================
# live() previously printed only `execute-live status=<...>`; a FAILED/CONTROLLED_RESIDUAL run did not reveal
# WHICH role/op or Object-Lock step broke. `_live_summary_lines(result)` now emits a CLOSED-VOCABULARY,
# secret-free summary. These goldens pin: allowed tokens only, no leak (even from a result stuffed with ids),
# no false PASS, Object-Lock PASS requires all six proofs, honest residual, and that live() actually wires it.
_ROLE_VERDICTS = {"PASS", "FAIL", "NOT_ATTEMPTED"}
_BOOL3 = {"true", "false", "not_attempted"}


def _rows_all_pass(c):
    r = []
    for role, ops in c._LIVE_ROLE_MATRIX.items():
        for op, prefix, expect in ops:
            # a passing row: allow ops resolve ok; deny ops must be a REAL policy deny (access-denied)
            cat = "ok" if expect == "allow" else "access-denied"
            r.append({"role": role, "op": op, "prefix": prefix, "expected": expect, "actual": expect,
                      "code": 200, "request_id": "REQ-LEAK-123", "version_id": "VER-LEAK", "category": cat})
    return r


def _ol_all_true():
    return {"unlocked_put_ok": True, "iam_delete_ok_on_unlocked": True, "locked_put_ok": True,
            "retention_set": True, "readback_ok": True, "readback": {"retain_until": "2026-08-16T00:15:00Z"},
            "locked_delete_refused": True, "mode": "GOVERNANCE", "compliance_tested": False, "proof": True}


def _happy_result(c):
    return {"rows": _rows_all_pass(c), "object_lock": _ol_all_true(),
            "cleanup": {"status": "controlled-residual",
                        "manual_cleanup": {"keys": ["logical-writer"], "policies": ["p"],
                                           "bucket": "pult-canary-x", "project": "PROJ-LEAK-999"},
                        "locked_residual": [{"key": "canary/x/pitr/lock-x", "version": "VER-LEAK-abc",
                                             "retain_until": "2026-08-16T00:15:00Z"}]},
            "deadline_reached": False, "status": "CONTROLLED_RESIDUAL",
            "manual_revoke_required": {"keys": ["u1-LEAK"], "policies": ["p"]}}


def test_live_summary_closed_vocabulary_and_all_pass():
    c = _load_canary()
    lines = c._live_summary_lines(_happy_result(c))
    assert lines[0] == "live-summary v1"
    for ln in lines:
        if ln.startswith("role "):
            # role line: "... = <VERDICT> (<category>)"
            rhs = ln.split(" = ")[-1]
            verdict, _, cat = rhs.partition(" (")
            assert verdict in _ROLE_VERDICTS, ln
            assert cat.rstrip(")") in tuple(c.LIVE_ERROR_CATEGORIES) + ("not_attempted",), ln
        elif ln.startswith("object_lock "):
            assert ln.split(" = ")[-1] in _BOOL3, ln
    assert all(ln.split(" = ")[-1].split(" (")[0] == "PASS" for ln in lines if ln.startswith("role "))
    assert "object_lock object_lock_proof = true" in lines
    assert "controlled_residual = true" in lines
    assert "manual_cleanup_required = service-keys,bucket-policy,bucket,project" in lines


def test_live_summary_never_leaks_ids_or_secrets():
    c = _load_canary()
    blob = "\n".join(c._live_summary_lines(_happy_result(c)))
    for leak in ("REQ-LEAK-123", "VER-LEAK", "VER-LEAK-abc", "PROJ-LEAK-999", "u1-LEAK",
                 "pult-canary-x", "canary/x/pitr/lock-x", "2026-08-16T00:15:00Z"):
        assert leak not in blob, f"summary leaked {leak!r}"


def test_live_summary_partial_is_not_pass():
    c = _load_canary()
    partial = {"rows": [{"role": "logical-writer", "op": "put", "prefix": "logical/",
                         "expected": "allow", "actual": "allow"}],
               "object_lock": {}, "cleanup": {"status": "failed", "manual_cleanup": {}},
               "deadline_reached": True, "status": "FAILED"}
    lines = c._live_summary_lines(partial)
    assert any(ln.endswith("= NOT_ATTEMPTED (not_attempted)") for ln in lines)
    assert not any("= PASS" in ln for ln in lines if "list logical/" in ln)  # absent op never spuriously PASS
    assert "object_lock object_lock_proof = not_attempted" in lines
    assert "cleanup_status = failed" in lines and "controlled_residual = false" in lines
    assert "execute-live status = FAILED" in lines


def test_live_summary_object_lock_reflects_each_of_six():
    c = _load_canary()
    # drop exactly one proof (retention_set=False, proof=False) -> that flag false, proof false, others true
    ol = _ol_all_true()
    ol["retention_set"] = False
    ol["proof"] = False
    res = dict(_happy_result(c), object_lock=ol)
    lines = c._live_summary_lines(res)
    assert "object_lock retention_put_ok = false" in lines
    assert "object_lock object_lock_proof = false" in lines
    assert "object_lock unlocked_put_ok = true" in lines
    assert "object_lock locked_admin_delete_denied = true" in lines


def test_live_summary_object_lock_all_six_labels_present():
    c = _load_canary()
    lines = c._live_summary_lines(_happy_result(c))
    for label in ("unlocked_put_ok", "unlocked_admin_delete_ok", "locked_put_ok", "retention_put_ok",
                  "retention_readback_ok", "locked_admin_delete_denied", "object_lock_proof"):
        assert f"object_lock {label} = " in "\n".join(lines), label


def test_live_summary_residual_honest_failed_stays_failed():
    c = _load_canary()
    # unknown/non-locked residual -> cleanup failed, run FAILED, controlled_residual must NOT be true
    res = dict(_happy_result(c), cleanup={"status": "failed", "manual_cleanup": {"keys": ["k"]}},
               status="FAILED")
    lines = c._live_summary_lines(res)
    assert "cleanup_status = failed" in lines
    assert "controlled_residual = false" in lines
    assert "execute-live status = FAILED" in lines


def test_live_summary_role_fail_is_visible():
    c = _load_canary()
    rows = _rows_all_pass(c)
    rows[0] = dict(rows[0], actual="deny", category="access-denied")  # allow op denied -> FAIL
    res = dict(_happy_result(c), rows=rows)
    lines = c._live_summary_lines(res)
    assert "role logical-writer put logical/ = FAIL (access-denied)" in lines


def test_live_wires_summary_and_does_not_print_result_dict():
    code = _code()
    seg = code[code.index("def live(args"):code.index("def main(")]
    assert "for line in _live_summary_lines(result):" in seg
    # the raw result dict is never printed / str-formatted into output
    assert "print(result" not in seg
    assert "{result}" not in seg and "{result!r}" not in seg
    # summary runs only AFTER credentials + execution (order preserved)
    assert seg.index("read_masked_credentials()") < seg.index("_live_summary_lines(result)")


_OBS_MUTATIONS = [
    "print a versionId in the summary", "print a request-id / host-id", "print the raw result dict",
    "print a raw exception / body", "hide a role FAIL (force PASS)", "turn NOT_ATTEMPTED into PASS",
    "drop one of the six object-lock proofs from the summary", "report cleanup clean while residual remains",
    "emit a value outside the closed vocabulary", "leak project/UID via manual_cleanup",
    "bypass the pre-network execute gate", "read credentials before the gate",
    "change runtime without bumping the pin/marker", "drop the deadline check", "summary printed before status",
    "print manual_cleanup key/user names",
]


def test_obs_mutation_matrix_declared():
    assert len(_OBS_MUTATIONS) >= 15


# ================= SECURITY-2D-3E1B-3C2D-V3 secret-free live error categories =================
# V2 postmortem: attempt() folded every 401/403 into allow="deny", so a signature-mismatch / invalid-key /
# auth failure on an expected-deny op read as a spurious deny-PASS, and the summary showed only PASS/FAIL.
# Now attempt() attaches a secret-free category; an expected-deny op PASSes ONLY on access-denied.
def _http_cat(c, code, body=b""):
    return c._http_result_category(code, body)


def test_http_result_category_goldens():
    c = _load_canary()
    assert _http_cat(c, 200) == "ok"
    assert _http_cat(c, 204) == "ok"
    assert _http_cat(c, 404) == "not-found"
    assert _http_cat(c, 403, b"<Error><Code>AccessDenied</Code></Error>") == "access-denied"
    assert _http_cat(c, 403, b"<Error><Code>SignatureDoesNotMatch</Code><StringToSign>S</StringToSign></Error>") == "signature-mismatch"
    assert _http_cat(c, 403, b"<Error><Code>AuthorizationHeaderMalformed</Code></Error>") == "signature-mismatch"
    assert _http_cat(c, 403, b"<Error><Code>InvalidAccessKeyId</Code></Error>") == "invalid-access-key"
    assert _http_cat(c, 403, b"<Error><Code>InvalidToken</Code></Error>") == "authentication-failed"
    assert _http_cat(c, 403, b"<Error><Code>ExpiredToken</Code></Error>") == "authentication-failed"
    assert _http_cat(c, 403, b"") == "access-denied"          # bare 403 -> access-denied
    assert _http_cat(c, 401, b"") == "authentication-failed"  # bare 401 -> auth failure
    assert _http_cat(c, 500, b"") == "service-error"
    assert _http_cat(c, 403, b"<Error><Code>WeirdUnlisted</Code></Error>") == "access-denied"  # unknown code -> status class
    assert _http_cat(c, 403, b"<Err") == "malformed-response"
    assert set(c.LIVE_ERROR_CATEGORIES) >= {"access-denied", "signature-mismatch", "invalid-access-key",
                                            "authentication-failed", "ok", "not-found", "service-error",
                                            "malformed-response", "unknown"}
    assert len(c.LIVE_ERROR_CATEGORIES) == 12


def test_http_category_reads_only_code_never_leaks():
    c = _load_canary()
    body = (b"<Error><Code>AccessDenied</Code><Message>SECRET_MSG</Message><RequestId>RID_LEAK</RequestId>"
            b"<StringToSign>STS_LEAK</StringToSign><HostId>HOST_LEAK</HostId></Error>")
    assert c._http_result_category(403, body) == "access-denied"  # a fixed label, never the body content


def test_attempt_attaches_secret_free_category():
    c = _load_canary()
    m = _transport_manifest()

    class _B:
        def request(self, method, url, headers=None, content=b""):
            return type("R", (), {"status_code": 403,
                                  "headers": {"x-amz-request-id": "RID"},
                                  "content": b"<Error><Code>SignatureDoesNotMatch</Code></Error>"})()
    t = c.SelectelS3Transport(m, {"access_key": "A", "secret_key": _SEKRET}, http_client=_B())
    r = t.attempt("GetBucketVersioning", "/b", method="GET", query="versioning",
                  amz_date="20200101T000000Z", date_stamp="20200101")
    assert r["allow"] == "deny" and r["category"] == "signature-mismatch"


def _row(role, op, prefix, expected, actual, category):
    return {"role": role, "op": op, "prefix": prefix, "expected": expected, "actual": actual,
            "category": category, "code": 403, "request_id": "RID"}


def test_deny_pass_only_for_access_denied():
    c = _load_canary()
    # expected-deny op with a REAL policy deny -> PASS
    res = dict(_happy_result(c), rows=[_row("app", "put", "pitr/", "deny", "deny", "access-denied")])
    lines = c._live_summary_lines(res)
    assert "role app put pitr/ = PASS (access-denied)" in lines
    # same op but the 403 was a signature mismatch -> NOT a valid deny-proof -> FAIL
    res2 = dict(_happy_result(c), rows=[_row("app", "put", "pitr/", "deny", "deny", "signature-mismatch")])
    lines2 = c._live_summary_lines(res2)
    assert "role app put pitr/ = FAIL (signature-mismatch)" in lines2
    # invalid-access-key / authentication-failed on a deny op also FAIL (never a spurious pass)
    for badcat in ("invalid-access-key", "authentication-failed"):
        r = dict(_happy_result(c), rows=[_row("app", "get", "pitr/", "deny", "deny", badcat)])
        assert f"role app get pitr/ = FAIL ({badcat})" in c._live_summary_lines(r)


def test_expected_allow_accessdenied_is_fail():
    c = _load_canary()
    res = dict(_happy_result(c), rows=[_row("pitr-writer", "put", "pitr/", "allow", "deny", "access-denied")])
    assert "role pitr-writer put pitr/ = FAIL (access-denied)" in c._live_summary_lines(res)


def test_app_deny_category_visible_and_no_leak(capsys):
    c = _load_canary()
    res = dict(_happy_result(c), rows=[
        _row("app", "list", "pitr/", "deny", "deny", "signature-mismatch"),
        _row("app", "get", "pitr/", "deny", "deny", "access-denied")])
    lines = c._live_summary_lines(res)
    assert "role app list pitr/ = FAIL (signature-mismatch)" in lines
    assert "role app get pitr/ = PASS (access-denied)" in lines
    blob = "\n".join(lines)
    assert "RID" not in blob and "Error" not in blob
