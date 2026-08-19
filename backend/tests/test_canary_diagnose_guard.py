"""SECURITY-2D-3E1B-3C2C2-B-DIAG — offline guard for the DORMANT read-only canary diagnostic.

Proves ops/canary/diagnose.py is a fail-closed, read-only, secret-free inspector of the ALREADY-completed
canary run: full gate before any getpass/transport/network; exactly five read-only S3 ops against the exact
bucket/prefix/keys; no writes/deletes/retention-mutation reachable; output is a fixed allowlist that never
carries a secret, version id, request id, UID/PROJECT_ID, body, URI, or stack trace. No network, no
credentials, no Docker; the frozen canary.py runtime must stay byte-identical (SHA-256 pinned).
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import importlib.util as _ilu
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CANARY = REPO / "ops" / "canary"
CANARY_PY = CANARY / "canary.py"
DIAG_PY = CANARY / "diagnose.py"

# canary.py must remain byte-identical to the pinned runtime (3C2D SigV4 fix) — this diagnostic never edits it.
_CANARY_RUNTIME_SHA256 = "aa4b357acc6e908341c5523db8f93a32fdf3c0305a7ddc36c4c68842fbe3ee4f"
_CANARY_RUNTIME_REVIEW = "3C2D-v10-contextual-http400-lock-denial"

# S3 operations that MUST NEVER appear in the diagnostic (as an op literal or an attempt target).
_FORBIDDEN_OPS = frozenset({
    "PutObject", "DeleteObject", "DeleteObjectVersion", "PutObjectRetention", "AbortMultipartUpload",
    "DeleteBucket", "CreateBucket", "PutBucketVersioning", "PutBucketObjectLockConfiguration",
    "CreateMultipartUpload", "UploadPart", "CompleteMultipartUpload", "PutObjectLegalHold",
    "BypassGovernanceRetention",
})

# Every listed mutation, applied to a disposable copy, must flip at least one assertion in this file RED.
DIAG_MUTATION_MATRIX = [
    "drop the env-ack check", "drop the typed-confirm check", "drop the --ack check",
    "accept a wrong bucket", "accept a wrong run-id", "accept a wrong endpoint",
    "accept a past deadline", "accept a too-far deadline", "read credentials before the gate",
    "add a credential CLI argument", "issue a PutObject / write", "issue a DeleteObject / delete",
    "issue a PutObjectRetention mutation", "widen ListBucket beyond the exact prefix",
    "HeadObject a non-allowlisted key", "GetObjectRetention on a non-lock key",
    "print a version id / request id / secret", "print an unlisted output field",
    "re-run the canary", "change canary.py without bumping the pin",
    # error-classification correction (3C2C2-B-DIAG-CORRECTION)
    "emit a non-allowlisted error category", "read Message/RequestId/StringToSign from the error body",
    "print the raw exception text on a per-op failure", "classify an exception by its message not its type",
    "turn an access-denied read into success",
]


def _diag_code() -> str:
    return DIAG_PY.read_text(encoding="utf-8")


def _load(path: Path, name: str):
    spec = _ilu.spec_from_file_location(name, str(path))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_canary():
    return _load(CANARY_PY, "canary_runtime_diagtest")


def _load_diag():
    return _load(DIAG_PY, "diagnose_runtime_test")


# ---------------- args / clock / no-network helpers ----------------
class _Args:
    def __init__(self, **kw):
        for k in ("mode", "run_id", "region", "endpoint", "bucket", "ack", "confirm", "deadline"):
            setattr(self, k, None)
        self.execute_diagnose = False
        self.with_restore_reader = False
        for k, v in kw.items():
            setattr(self, k, v)


GOOD_DEADLINE = "2026-08-16T00:20:00Z"
RUN_ID = "eced74af45e3"
BUCKET = "pult-canary-eced74af45e3"
ENDPOINT = "https://s3.ru-3.storage.selcloud.ru"
PREFIX = "canary/eced74af45e3/"
CONFIRM = f"diagnose/{BUCKET}/ru-3/{ENDPOINT}/{RUN_ID}"
ACK = "PULT-CANARY-DIAGNOSE-eced74af45e3"


def _diag_args(**over):
    base = dict(mode="diagnose", run_id=RUN_ID, region="ru-3", endpoint=ENDPOINT, bucket=BUCKET,
                ack=ACK, confirm=CONFIRM, deadline=GOOD_DEADLINE, execute_diagnose=True,
                with_restore_reader=False)
    base.update(over)
    return _Args(**base)


def _clock(y=2026, mo=8, d=16, h=0, mi=0, s=0):
    import datetime
    return lambda: datetime.datetime(y, mo, d, h, mi, s)


def _ok_env(d):
    return {d.DIAG_ENV: d.DIAG_ENV_VALUE}


class _NoNetwork:
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


def _boom_reader(prompt):
    raise AssertionError("getpass reader called before it should be")


def _reader(secret="SEKRET_zzz_DO_NOT_LEAK"):
    seen = []

    def r(prompt):
        seen.append(prompt)
        return secret

    r.seen = seen
    return r


# ---------------- FakeState + FakeReadTransport (attempt()-only, never a socket) ----------------
class FakeState:
    def __init__(self, versioning="Enabled", object_lock=True, present=(), retention=None,
                 deny=(), force_unknown=None, versions=None, request_id="req-123-DONOTLEAK",
                 error=None, raise_on=None, listed=None, list_truncated=False, list_extra_keys=(),
                 list_body=None):
        self.versioning = versioning
        self.object_lock = object_lock             # True / False / None(=404 not-found)
        self.present = set(present)                # keys that exist (drive Head + default ListBucket contents)
        self.retention = retention                 # None or {"mode","until","malformed"}
        self.deny = set(deny)                      # ops forced to 403 deny (empty body)
        self.force_unknown = force_unknown or {}   # {op or ("HeadObject",key): http_code}
        self.versions = versions or {}             # key -> version id (memory-only echo)
        self.request_id = request_id
        self.error = error or {}                   # {op: (http_code, body_bytes)} — custom error response
        self.raise_on = raise_on or {}             # {op: Exception instance} — transport raises (net/TLS/...)
        # ListBucket controls: `listed` overrides which keys the listing returns (default = present); extra
        # (unexpected) key names to prove they are ignored; truncation flag; or a fully custom XML body.
        self.listed = set(listed) if listed is not None else None
        self.list_truncated = list_truncated
        self.list_extra_keys = tuple(list_extra_keys)
        self.list_body = list_body


class FakeReadTransport:
    def __init__(self, bucket, creds, state, record):
        self.bucket = bucket
        self.__secret = creds["secret_key"]        # kept private; must never leak
        self.state = state
        self.record = record

    def _r(self, allow, code, body=b"", version_id=""):
        return {"allow": allow, "http_code": code, "version_id": version_id, "body": body,
                "request_id": self.state.request_id}

    def _key(self, uri):
        head = f"/{self.bucket}/"
        return uri[len(head):] if uri.startswith(head) else uri

    def attempt(self, op, uri, method="GET", query="", payload=b"", amz_date=None, date_stamp=None,
                extra_headers=None):
        self.record.append({"op": op, "uri": uri, "query": query, "method": method})
        st = self.state
        if op in st.raise_on:
            raise st.raise_on[op]
        if op in st.error:
            code, body = st.error[op]
            allow = "deny" if code in (401, 403) else "unknown"
            return self._r(allow, code, body)
        if op in st.force_unknown:
            return self._r("unknown", st.force_unknown[op])
        if op in st.deny:
            return self._r("deny", 403)
        if op == "GetBucketVersioning":
            body = (f"<VersioningConfiguration><Status>{st.versioning}</Status></VersioningConfiguration>"
                    .encode() if st.versioning else b"<VersioningConfiguration></VersioningConfiguration>")
            return self._r("allow", 200, body)
        if op == "GetBucketObjectLockConfiguration":
            if st.object_lock is None:
                return self._r("unknown", 404)
            body = (b"<ObjectLockConfiguration><ObjectLockEnabled>Enabled</ObjectLockEnabled>"
                    b"</ObjectLockConfiguration>" if st.object_lock
                    else b"<ObjectLockConfiguration></ObjectLockConfiguration>")
            return self._r("allow", 200, body)
        if op == "ListBucket":
            if st.list_body is not None:
                return self._r("allow", 200, st.list_body)
            keys = st.listed if st.listed is not None else st.present
            contents = "".join(f"<Contents><Key>{k}</Key></Contents>"
                               for k in sorted(keys) + list(st.list_extra_keys))
            trunc = "true" if st.list_truncated else "false"
            body = (f"<ListBucketResult>{contents}<IsTruncated>{trunc}</IsTruncated>"
                    f"</ListBucketResult>").encode()
            return self._r("allow", 200, body)
        if op == "HeadObject":
            key = self._key(uri)
            fk = ("HeadObject", key)
            if fk in st.force_unknown:
                return self._r("unknown", st.force_unknown[fk])
            if key in st.present:
                return self._r("allow", 200, version_id=st.versions.get(key, ""))
            return self._r("unknown", 404)
        if op == "GetObjectRetention":
            if st.retention is None:
                return self._r("deny", 403)
            if st.retention.get("malformed"):
                return self._r("allow", 200, b"<Retention><Mode>GOV")
            mode = st.retention.get("mode", "GOVERNANCE")
            until = st.retention.get("until", "2026-08-16T00:15:00Z")
            body = (f'<Retention xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Mode>{mode}</Mode>'
                    f"<RetainUntilDate>{until}</RetainUntilDate></Retention>").encode()
            return self._r("allow", 200, body)
        raise AssertionError(f"non-allowlisted op reached the transport: {op}")


def _factory(*states, record):
    seq = list(states)

    def factory(manifest, creds, service="s3"):
        return FakeReadTransport(manifest["bucket"], creds, seq.pop(0), record)

    return factory


def _run(d, state, over=None, reader=None, record=None, extra_states=()):
    record = [] if record is None else record
    args = _diag_args(**(over or {}))
    canary = _load_canary()
    fac = _factory(state, *extra_states, record=record)
    rc = d.diagnose(args, env=_ok_env(d), canary=canary, transport_factory=fac,
                    clock=_clock(), reader=reader or _reader())
    return rc, record


# ================= gate: fail-closed BEFORE any getpass / transport / network =================
def test_gate_off_is_prenetwork_no_getpass_no_transport():
    d = _load_diag()
    canary = _load_canary()

    def bad_factory(*a, **k):
        raise AssertionError("transport constructed on a refused gate")

    with _NoNetwork():
        rc = d.diagnose(_diag_args(), env={}, canary=canary, transport_factory=bad_factory,
                        clock=_clock(), reader=_boom_reader)
    assert rc == 4


def test_wrong_params_fail_closed_prenetwork():
    d = _load_diag()
    canary = _load_canary()
    bad = {
        "wrong ack": _diag_args(ack="nope"),
        "wrong deadline fmt": _diag_args(deadline="soon"),
        "past deadline": _diag_args(deadline="2020-01-01T00:00:00Z"),
        "too-far deadline": _diag_args(deadline="2026-08-16T01:00:00Z"),
        "wrong endpoint": _diag_args(endpoint="https://s3.ru-1.storage.selcloud.ru"),
        "wrong bucket": _diag_args(bucket="prod-backup"),
        "wrong run-id": _diag_args(run_id="0123456789ab"),
        "wrong region": _diag_args(region="ru-1"),
        "confirm mismatch": _diag_args(confirm="wrong"),
    }

    def bad_factory(*a, **k):
        raise AssertionError("transport constructed on a refused gate")

    with _NoNetwork():
        for name, a in bad.items():
            rc = d.diagnose(a, env=_ok_env(d), canary=canary, transport_factory=bad_factory,
                            clock=_clock(), reader=_boom_reader)
            assert rc == 4, f"{name} must be refused pre-network (got {rc})"
            try:
                d.diag_validate(a, _ok_env(d), _clock())
                raise AssertionError(f"{name} should have raised")
            except d.DiagGateError:
                pass


def test_validation_runs_before_credentials():
    d = _load_diag()
    canary = _load_canary()
    # ordinary invocation (no --execute-diagnose): gate validated, then deferral BEFORE any getpass
    with _NoNetwork():
        rc = d.diagnose(_diag_args(execute_diagnose=False), env=_ok_env(d), canary=canary,
                        transport_factory=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no transport")),
                        clock=_clock(), reader=_boom_reader)
    assert rc == 5


def test_main_bare_invocation_is_fail_closed(monkeypatch):
    d = _load_diag()
    monkeypatch.delenv(d.DIAG_ENV, raising=False)
    with _NoNetwork():
        assert d.main(["diagnose"]) == 4  # no env-ack -> refuse before creds/network


# ================= happy read-only path =================
def test_happy_all_present_pass():
    d = _load_diag()
    st = FakeState(versioning="Enabled", object_lock=True,
                   present=[d.KEY_LOGICAL_PROBE, d.KEY_PITR_PROBE, d.KEY_UNLOCKED, d.KEY_LOCKED],
                   retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"},
                   versions={d.KEY_LOCKED: "vid-SECRETish"})
    with _NoNetwork():
        rc, rec = _run(d, st)
    assert rc == 0


def test_each_key_existence_states():
    d = _load_diag()
    # ListBucket (not truncated) is authoritative: listed -> yes, absent -> no (Head agrees here).
    ok = _diag_run_report(d, FakeState(present=[d.KEY_PITR_PROBE, d.KEY_LOCKED],
                                       retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"}))
    assert ok["logical_probe_exists"] == "no"
    assert ok["pitr_probe_exists"] == "yes"
    assert ok["unlocked_control_exists"] == "no"
    assert ok["locked_control_exists"] == "yes"
    # Truncated listing: a key seen in the page -> yes; a key NOT seen -> unknown (never a false 'no').
    tr = _diag_run_report(d, FakeState(listed=[d.KEY_LOGICAL_PROBE], list_truncated=True))
    assert tr["logical_probe_exists"] == "yes"
    assert tr["pitr_probe_exists"] == "unknown"
    assert tr["unlocked_control_exists"] == "unknown"
    assert tr["diagnostic_status"] in ("PARTIAL", "FAILED")


def test_retention_variants():
    d = _load_diag()
    ca = _load_canary()
    man = d.diag_validate(_diag_args(), _ok_env(d), _clock())
    admin = {"retention-admin": {"access_key": "A", "secret_key": "S"}}

    def run(state):
        return d.run_diagnostic(man, admin, ca, transport_factory=_factory(state, record=[]), clock=_clock())

    gov = run(FakeState(present=[d.KEY_LOCKED], retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"}))
    assert gov["lock_retention_mode"] == "GOVERNANCE" and gov["lock_retain_until_utc"] == "2026-08-16T00:15:00Z"
    denied = run(FakeState(present=[d.KEY_LOCKED], retention=None))
    assert denied["lock_retention_mode"] == "unknown" and denied["lock_retain_until_utc"] == "unknown"
    malformed = run(FakeState(present=[d.KEY_LOCKED], retention={"malformed": True}))
    assert malformed["lock_retention_mode"] == "unknown"
    missing = run(FakeState(present=[]))
    assert missing["lock_retention_mode"] == "none" and missing["lock_retain_until_utc"] == "none"


# ================= exact-scope enforcement (ops actually issued) =================
def test_listbucket_uses_only_the_exact_prefix():
    d = _load_diag()
    with _NoNetwork():
        _, rec = _run(d, FakeState(present=[d.KEY_LOCKED],
                                   retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"}))
    lists = [r for r in rec if r["op"] == "ListBucket"]
    assert lists, "ListBucket must be issued"
    for r in lists:
        assert r["uri"] == f"/{BUCKET}" and r["query"] == f"prefix={PREFIX}", r


def test_headobject_only_the_four_exact_keys():
    d = _load_diag()
    with _NoNetwork():
        _, rec = _run(d, FakeState(present=[d.KEY_PITR_PROBE, d.KEY_LOCKED],
                                   retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"}))
    exact = {f"/{BUCKET}/{k}" for k in (d.KEY_LOGICAL_PROBE, d.KEY_PITR_PROBE, d.KEY_UNLOCKED, d.KEY_LOCKED)}
    heads = {r["uri"] for r in rec if r["op"] == "HeadObject"}
    assert heads <= exact, f"HeadObject hit a non-exact key: {heads - exact}"


def test_getobjectretention_only_on_lock_key_and_only_when_present():
    d = _load_diag()
    with _NoNetwork():
        _, rec_present = _run(d, FakeState(present=[d.KEY_LOCKED],
                                           retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"}))
        _, rec_absent = _run(d, FakeState(present=[]))
    gets = [r for r in rec_present if r["op"] == "GetObjectRetention"]
    assert gets and all(r["uri"] == f"/{BUCKET}/{d.KEY_LOCKED}" for r in gets)
    assert not [r for r in rec_absent if r["op"] == "GetObjectRetention"], "no retention read when lock absent"


def test_only_read_ops_ever_issued():
    d = _load_diag()
    with _NoNetwork():
        _, rec = _run(d, FakeState(present=[d.KEY_LOGICAL_PROBE, d.KEY_PITR_PROBE, d.KEY_UNLOCKED, d.KEY_LOCKED],
                                   retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"}))
    issued = {r["op"] for r in rec}
    assert issued <= d.DIAG_READ_ONLY_OPS, f"non-read op issued: {issued - d.DIAG_READ_ONLY_OPS}"
    assert not (issued & _FORBIDDEN_OPS)


def test_readonly_allowlist_is_exactly_five_and_disjoint_from_mutations():
    d = _load_diag()
    assert d.DIAG_READ_ONLY_OPS == {
        "GetBucketVersioning", "GetBucketObjectLockConfiguration", "ListBucket", "HeadObject",
        "GetObjectRetention"}
    assert not (d.DIAG_READ_ONLY_OPS & _FORBIDDEN_OPS)


def test_restore_reader_conflict_is_fail_closed():
    d = _load_diag()
    ca = _load_canary()
    man = d.diag_validate(_diag_args(with_restore_reader=True), _ok_env(d), _clock())
    creds = {"retention-admin": {"access_key": "A", "secret_key": "S"},
             "restore-reader": {"access_key": "B", "secret_key": "S2"}}
    # ListBucket UNAVAILABLE (denied) for both, so HeadObject is the only source; admin Head says pitr present,
    # restore-reader Head says pitr absent -> two successful Heads disagree -> fail-closed unknown.
    admin_state = FakeState(present=[d.KEY_PITR_PROBE, d.KEY_LOCKED], deny={"ListBucket"})
    reader_state = FakeState(present=[d.KEY_LOCKED], deny={"ListBucket"})
    report = d.run_diagnostic(man, creds, ca,
                              transport_factory=_factory(admin_state, reader_state, record=[]), clock=_clock())
    assert report["pitr_probe_exists"] == "unknown"  # conflict -> fail-closed unknown


# ================= AST: no mutating call, every op literal in the allowlist =================
def test_ast_no_mutating_op_and_every_issued_op_is_allowlisted():
    d = _load_diag()
    src = _diag_code()
    tree = ast.parse(src)
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    leaked = consts & _FORBIDDEN_OPS
    assert not leaked, f"mutating op literal present: {leaked}"
    # every op passed to the internal read helper call("...") is a literal in the frozen allowlist
    call_ops = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "call":
            assert len(n.args) >= 2 and isinstance(n.args[1], ast.Constant), "op must be a literal"
            call_ops.append(n.args[1].value)
    assert call_ops, "no read ops issued via call()"
    assert set(call_ops) <= d.DIAG_READ_ONLY_OPS, f"op outside allowlist: {set(call_ops) - d.DIAG_READ_ONLY_OPS}"
    # exactly one attempt() site, guarded by the allowlist membership check
    assert src.count(".attempt(") == 1, "attempt() must be called from exactly one guarded site"
    assert "if op not in DIAG_READ_ONLY_OPS" in src


def test_no_write_or_retry_helpers():
    src = _diag_code()
    for bad in ("@retry", "tenacity", "backoff", "while True", "--recursive", "rmtree", "shutil"):
        assert bad not in src, f"diagnostic must not contain {bad!r}"


# ================= credentials only via getpass; no argv/env/file =================
def test_credentials_only_masked_getpass():
    src = _diag_code()
    seg = src[src.index("def read_masked_credentials"):src.index("def _xml_text")]
    assert "getpass" in seg and "input(" not in seg and "open(" not in seg
    # no credential CLI flags in the parser
    flags = set(re.findall(r'add_argument\("(--[a-z-]+)"', src))
    allowed = {"--run-id", "--region", "--endpoint", "--bucket", "--ack", "--confirm", "--deadline",
               "--execute-diagnose", "--with-restore-reader"}
    assert flags <= allowed, f"unexpected argv flags: {flags - allowed}"
    for bad in ("--secret", "--access-key", "--secret-key", "--key", "--password", "--token", "--access"):
        assert bad not in src, f"credential CLI arg {bad!r} forbidden"


def test_masked_reader_is_memory_only(capsys):
    d = _load_diag()
    r = _reader("SEKRET_zzz_DO_NOT_LEAK")
    creds = d.read_masked_credentials(with_restore_reader=True, reader=r)
    assert set(creds) == {"retention-admin", "restore-reader"}
    assert len(r.seen) == 4  # 2 roles x (access + secret)
    out = capsys.readouterr()
    assert "SEKRET_zzz_DO_NOT_LEAK" not in (out.out + out.err)


def test_no_environ_dump():
    src = _diag_code()
    assert "print(os.environ" not in src and "os.environ)" not in src.replace("os.environ.get", "")


# ================= output allowlist; no secret/id in any path =================
def test_output_is_exactly_the_field_allowlist(capsys):
    d = _load_diag()
    st = FakeState(present=[d.KEY_LOGICAL_PROBE, d.KEY_PITR_PROBE, d.KEY_UNLOCKED, d.KEY_LOCKED],
                   retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"})
    with _NoNetwork():
        _run(d, st)
    out = capsys.readouterr().out.strip().splitlines()
    keys = [ln.split(":", 1)[0] for ln in out if ln.strip()]
    assert keys == list(d._OUTPUT_FIELDS), keys


def test_no_secret_version_or_request_id_in_output_happy(capsys):
    d = _load_diag()
    st = FakeState(present=[d.KEY_LOCKED], versions={d.KEY_LOCKED: "vid-LEAK-9999"},
                   retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"},
                   request_id="req-123-DONOTLEAK")
    with _NoNetwork():
        _run(d, st, reader=_reader("SEKRET_zzz_DO_NOT_LEAK"))
    blob = capsys.readouterr()
    combined = blob.out + blob.err
    for leak in ("SEKRET_zzz_DO_NOT_LEAK", "vid-LEAK-9999", "req-123-DONOTLEAK"):
        assert leak not in combined, f"leaked {leak!r}"


def test_error_path_never_leaks(capsys):
    """Top-level backstop: an UNEXPECTED error (transport construction fails) -> fixed category + all-unknown
    FAILED report, never the raw exception text."""
    d = _load_diag()
    canary = _load_canary()

    def boom_factory(*a, **k):
        raise RuntimeError("https://s3.ru-3.storage.selcloud.ru/b?x secret=SEKRET_LEAK "
                           "reqid=req-123 versionId=vid-LEAK")

    with _NoNetwork():
        rc = d.diagnose(_diag_args(), env=_ok_env(d), canary=canary,
                        transport_factory=boom_factory, clock=_clock(), reader=_reader())
    combined = capsys.readouterr()
    text = combined.out + combined.err
    assert rc == 6
    assert "DIAG ERROR: read-or-transport-error" in combined.err
    assert "diagnostic_status: FAILED" in combined.out
    for leak in ("SEKRET_LEAK", "req-123", "vid-LEAK", "https://s3.ru-3"):
        assert leak not in text, f"error path leaked {leak!r}"


def test_all_unknown_reads_status_failed():
    d = _load_diag()
    ca = _load_canary()
    man = d.diag_validate(_diag_args(), _ok_env(d), _clock())
    admin = {"retention-admin": {"access_key": "A", "secret_key": "S"}}
    # every bucket-level + list + head read forced unknown -> everything unknown -> FAILED
    st = FakeState(versioning=None, object_lock=None,
                   force_unknown={"GetBucketVersioning": 500, "GetBucketObjectLockConfiguration": 500,
                                  "ListBucket": 500,
                                  ("HeadObject", d.KEY_LOGICAL_PROBE): 500,
                                  ("HeadObject", d.KEY_PITR_PROBE): 500,
                                  ("HeadObject", d.KEY_UNLOCKED): 500,
                                  ("HeadObject", d.KEY_LOCKED): 500})
    report = d.run_diagnostic(man, admin, ca, transport_factory=_factory(st, record=[]), clock=_clock())
    assert report["diagnostic_status"] == "FAILED"


def test_status_logic_unit():
    d = _load_diag()
    base = {k: "unknown" for k in d._OUTPUT_FIELDS}
    allknown = dict(base, versioning="enabled", object_lock="enabled", logical_probe_exists="no",
                    pitr_probe_exists="no", unlocked_control_exists="no", locked_control_exists="no")
    assert d._status_from(allknown) == "PASS"
    partial = dict(allknown, pitr_probe_exists="unknown")
    assert d._status_from(partial) == "PARTIAL"


# ================= no files written =================
def test_no_files_written_during_diagnose():
    d = _load_diag()
    canary = _load_canary()  # preload so diagnose() does not import canary via open() during the trap
    st = FakeState(present=[d.KEY_LOCKED],
                   retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"})
    rec = []
    fac = _factory(st, record=rec)
    orig_open = builtins.open
    opened = []

    def trap(*a, **k):
        opened.append(a[0] if a else None)
        return orig_open(*a, **k)

    builtins.open = trap
    try:
        with _NoNetwork():
            d.diagnose(_diag_args(), env=_ok_env(d), canary=canary, transport_factory=fac,
                       clock=_clock(), reader=_reader())
    finally:
        builtins.open = orig_open
    # any file opened during the run must NOT be for writing
    assert opened == [], f"diagnose opened files: {opened}"


# ================= frozen canary.py runtime is untouched =================
def test_canary_runtime_byte_frozen_and_marker():
    digest = hashlib.sha256(CANARY_PY.read_bytes()).hexdigest()
    assert digest == _CANARY_RUNTIME_SHA256, f"canary.py changed (got {digest}) — this task must not touch it"
    assert f'CANARY_RUNTIME_REVIEW = "{_CANARY_RUNTIME_REVIEW}"' in CANARY_PY.read_text(encoding="utf-8")


def test_existing_canary_guard_still_pins_same_runtime():
    guard = (REPO / "backend" / "tests" / "test_canary_tooling_guard.py").read_text(encoding="utf-8")
    assert f'_CANARY_RUNTIME_SHA256 = "{_CANARY_RUNTIME_SHA256}"' in guard
    assert f'_CANARY_RUNTIME_REVIEW = "{_CANARY_RUNTIME_REVIEW}"' in guard


def test_diagnose_never_imports_httpx_at_module_load():
    src = _diag_code()
    # httpx must not be imported at module top-level (only the reused transport imports it lazily on a request)
    assert "import httpx" not in src


def test_mutation_matrix_declared():
    assert len(DIAG_MUTATION_MATRIX) >= 15


# ================= 3C2C2-B-DIAG-CORRECTION: secret-free error classification =================
def _res(allow=None, code=None, body=b""):
    return {"allow": allow, "http_code": code, "body": body}


def test_classify_xml_error_codes():
    d = _load_diag()
    cases = {
        "InvalidAccessKeyId": "invalid-access-key",
        "SignatureDoesNotMatch": "signature-mismatch",
        "AuthorizationHeaderMalformed": "signature-mismatch",
        "AccessDenied": "access-denied",
        "InvalidToken": "authentication-failed",
        "ExpiredToken": "authentication-failed",
    }
    for xml_code, cat in cases.items():
        body = (f"<Error><Code>{xml_code}</Code><Message>secret msg</Message>"
                f"<RequestId>RID</RequestId><StringToSign>STS</StringToSign></Error>").encode()
        assert d._classify_result(_res("deny", 403, body)) == cat, xml_code
    # a Code not in the allowlist falls back to the numeric status class (403 -> access-denied)
    assert d._classify_result(_res("deny", 403, b"<Error><Code>WeirdUnlisted</Code></Error>")) == "access-denied"


def test_classify_status_classes():
    d = _load_diag()
    assert d._classify_result(_res("allow", 200)) == "ok"
    assert d._classify_result(_res("unknown", 404)) == "not-found"
    assert d._classify_result(_res("deny", 401, b"")) == "authentication-failed"
    assert d._classify_result(_res("deny", 403, b"")) == "access-denied"
    assert d._classify_result(_res("unknown", 500, b"")) == "service-error"
    assert d._classify_result(_res("unknown", 503, b"")) == "service-error"
    assert d._classify_result(_res("unknown", None, b"")) == "unknown"
    assert d._classify_result("not-a-dict") == "unknown"


def test_classify_malformed_xml_is_malformed_response():
    d = _load_diag()
    assert d._classify_result(_res("deny", 403, b"<Error><Code>AccessDen")) == "malformed-response"


def test_all_categories_are_in_the_allowlist():
    d = _load_diag()
    produced = set(d._XML_CODE_CATEGORY.values()) | {
        "ok", "not-found", "access-denied", "authentication-failed", "service-error",
        "malformed-response", "unknown", "timeout", "tls-error", "network-error"}
    assert produced <= set(d.DIAG_ERROR_CATEGORIES)
    assert len(d.DIAG_ERROR_CATEGORIES) == 12


def test_exc_category_by_type_name_only():
    d = _load_diag()
    T = type("ReadTimeout", (Exception,), {})
    S = type("SSLCertVerificationError", (Exception,), {})
    C = type("ConnectError", (Exception,), {})
    N = type("NameResolutionError", (Exception,), {})
    assert d._exc_category(T("http://x secret=AKIA")) == "timeout"
    assert d._exc_category(S("cert secret")) == "tls-error"
    assert d._exc_category(C("secret url")) == "network-error"
    assert d._exc_category(N("secret")) == "network-error"
    assert d._exc_category(ValueError("http://x?token=SECRET")) == "unknown"


def test_error_summary_none_single_mixed():
    d = _load_diag()
    assert d._summary_from({"a": "ok", "b": d._NOT_READ}) == "none"
    assert d._summary_from({"a": "ok", "b": "access-denied", "c": "access-denied"}) == "access-denied"
    assert d._summary_from({"a": "access-denied", "b": "timeout"}) == "mixed"


def _diag_run_report(d, state, over=None, extra_states=()):
    man = d.diag_validate(_diag_args(**(over or {})), _ok_env(d), _clock())
    creds = {"retention-admin": {"access_key": "A", "secret_key": "S"}}
    if over and over.get("with_restore_reader"):
        creds["restore-reader"] = {"access_key": "B", "secret_key": "S2"}
    return d.run_diagnostic(man, creds, _load_canary(),
                            transport_factory=_factory(state, *extra_states, record=[]), clock=_clock())


def test_all_access_denied_yields_failed_and_single_summary():
    d = _load_diag()
    ad = (b"<Error><Code>AccessDenied</Code><Message>Access Denied SECRETMSG</Message>"
          b"<RequestId>RID-LEAK</RequestId></Error>")
    err = {op: (403, ad) for op in ("GetBucketVersioning", "GetBucketObjectLockConfiguration",
                                    "ListBucket", "HeadObject", "GetObjectRetention")}
    report = _diag_run_report(d, FakeState(error=err))
    # state cannot be read -> unknown, never falsely "no"/"disabled"
    for f in ("versioning", "object_lock", "logical_probe_exists", "pitr_probe_exists",
              "unlocked_control_exists", "locked_control_exists"):
        assert report[f] == "unknown", f
    for f in ("versioning_read_status", "object_lock_read_status", "list_prefix_read_status",
              "logical_probe_read_status", "pitr_probe_read_status", "unlocked_control_read_status",
              "locked_control_read_status"):
        assert report[f] == "access-denied", f
    assert report["diagnostic_error_summary"] == "access-denied"
    assert report["diagnostic_status"] == "FAILED"


def test_signature_mismatch_surfaced():
    d = _load_diag()
    sig = (b"<Error><Code>SignatureDoesNotMatch</Code><StringToSign>STS_SECRET</StringToSign>"
           b"<CanonicalRequest>CR_SECRET</CanonicalRequest></Error>")
    report = _diag_run_report(d, FakeState(error={"GetBucketVersioning": (403, sig)}))
    assert report["versioning_read_status"] == "signature-mismatch"
    assert report["diagnostic_error_summary"] in ("signature-mismatch", "mixed")


def test_mixed_categories_summary():
    d = _load_diag()
    report = _diag_run_report(d, FakeState(
        error={"GetBucketVersioning": (403, b"<Error><Code>AccessDenied</Code></Error>")},
        raise_on={"GetBucketObjectLockConfiguration": type("ReadTimeout", (Exception,), {})("secret")}))
    assert report["versioning_read_status"] == "access-denied"
    assert report["object_lock_read_status"] == "timeout"
    assert report["diagnostic_error_summary"] == "mixed"


def test_per_op_exception_classified_not_leaked(capsys):
    d = _load_diag()
    exc = type("ConnectTimeout", (Exception,), {})("https://s3.ru-3... key=AKIALEAK secret=SEKRETLEAK")
    st = FakeState(present=[d.KEY_LOGICAL_PROBE], raise_on={"GetBucketVersioning": exc})
    with _NoNetwork():
        _run(d, st)
    blob = capsys.readouterr()
    combined = blob.out + blob.err
    assert "versioning_read_status: timeout" in blob.out
    for leak in ("AKIALEAK", "SEKRETLEAK", "https://s3.ru-3"):
        assert leak not in combined, f"leaked {leak!r}"


def test_malicious_error_body_never_leaks_message_or_ids(capsys):
    d = _load_diag()
    nasty = (b"<Error><Code>AccessDenied</Code><Message>SECRET_MESSAGE_LEAK</Message>"
             b"<RequestId>RID_LEAK</RequestId><HostId>HOST_LEAK</HostId>"
             b"<StringToSign>STS_LEAK</StringToSign><CanonicalRequest>CR_LEAK</CanonicalRequest>"
             b"<AWSAccessKeyId>AKIA_LEAK</AWSAccessKeyId></Error>")
    err = {op: (403, nasty) for op in ("GetBucketVersioning", "GetBucketObjectLockConfiguration",
                                       "ListBucket", "HeadObject", "GetObjectRetention")}
    with _NoNetwork():
        _run(d, FakeState(error=err))
    combined = capsys.readouterr()
    text = combined.out + combined.err
    for leak in ("SECRET_MESSAGE_LEAK", "RID_LEAK", "HOST_LEAK", "STS_LEAK", "CR_LEAK", "AKIA_LEAK"):
        assert leak not in text, f"error body leaked {leak!r}"


def test_read_status_fields_in_output_are_categories(capsys):
    d = _load_diag()
    st = FakeState(present=[d.KEY_LOGICAL_PROBE, d.KEY_PITR_PROBE, d.KEY_UNLOCKED, d.KEY_LOCKED],
                   retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"})
    with _NoNetwork():
        _run(d, st)
    out = {ln.split(":", 1)[0]: ln.split(":", 1)[1].strip()
           for ln in capsys.readouterr().out.strip().splitlines() if ":" in ln}
    for f in ("versioning_read_status", "object_lock_read_status", "list_prefix_read_status",
              "logical_probe_read_status", "pitr_probe_read_status", "unlocked_control_read_status",
              "locked_control_read_status", "lock_retention_read_status", "diagnostic_error_summary"):
        assert f in out, f
    for f in ("versioning_read_status", "object_lock_read_status", "list_prefix_read_status"):
        assert out[f] in d.DIAG_ERROR_CATEGORIES, (f, out[f])
    assert out["lock_retention_read_status"] in tuple(d.DIAG_ERROR_CATEGORIES) + (d._NOT_READ,)
    assert out["diagnostic_error_summary"] in ("none", "mixed") + tuple(d.DIAG_ERROR_CATEGORIES)
    assert out["diagnostic_error_summary"] == "none"


def test_missing_objects_are_not_errors():
    d = _load_diag()
    report = _diag_run_report(d, FakeState(present=[]))  # empty bucket, all reads succeed
    assert report["logical_probe_read_status"] == "ok"
    assert report["lock_retention_read_status"] == d._NOT_READ
    assert report["diagnostic_error_summary"] == "none"


# ================= 3C2D ListBucket existence fallback (Head-denied on real Selectel) =================
_ACCESS_DENIED = (b"<Error><Code>AccessDenied</Code><Message>SECRET_M</Message>"
                  b"<RequestId>RID_LEAK</RequestId></Error>")


def test_head_denied_list_success_trusts_list():
    d = _load_diag()
    # HeadObject AccessDenied on every key (real Selectel behaviour), but ListBucket succeeds and lists pitr.
    st = FakeState(listed=[d.KEY_PITR_PROBE], error={"HeadObject": (403, _ACCESS_DENIED)})
    report = _diag_run_report(d, st)
    assert report["pitr_probe_exists"] == "yes"          # trusted from List despite denied Head
    assert report["logical_probe_exists"] == "no"
    assert report["unlocked_control_exists"] == "no"
    assert report["locked_control_exists"] == "no"
    # a denied Head does NOT turn the trusted List result into unknown / an error
    assert report["pitr_probe_read_status"] == "ok"
    assert report["logical_probe_read_status"] == "ok"


def test_list_denied_and_head_denied_is_unknown():
    d = _load_diag()
    st = FakeState(deny={"ListBucket"}, error={"HeadObject": (403, _ACCESS_DENIED)})
    report = _diag_run_report(d, st)
    for f in ("logical_probe_exists", "pitr_probe_exists", "unlocked_control_exists", "locked_control_exists"):
        assert report[f] == "unknown", f
    for f in ("logical_probe_read_status", "pitr_probe_read_status"):
        assert report[f] == "access-denied", f


def test_list_vs_successful_head_conflict_is_unknown():
    d = _load_diag()
    # List says pitr ABSENT (listed=[]) but a successful Head says PRESENT -> conflict -> unknown
    st = FakeState(listed=[], present=[d.KEY_PITR_PROBE])
    report = _diag_run_report(d, st)
    assert report["pitr_probe_exists"] == "unknown"
    assert report["pitr_probe_read_status"] == "unknown"


def test_truncated_listing_never_false_no():
    d = _load_diag()
    st = FakeState(listed=[d.KEY_LOCKED], list_truncated=True)
    report = _diag_run_report(d, st)
    assert report["locked_control_exists"] == "yes"      # present in the page
    assert report["logical_probe_exists"] == "unknown"   # not seen + truncated -> not a false 'no'
    assert report["pitr_probe_exists"] == "unknown"


def test_list_ignores_unexpected_keys_and_never_prints_them(capsys):
    d = _load_diag()
    body = (b"<ListBucketResult>"
            b"<Contents><Key>canary/eced74af45e3/pitr/probe-eced74af45e3</Key></Contents>"
            b"<Contents><Key>SECRET_OTHER_KEY_LEAK</Key></Contents>"
            b"<Contents><Key>../../etc/passwd</Key></Contents>"
            b"<IsTruncated>false</IsTruncated></ListBucketResult>")
    status, keys, trunc = d._list_keys({"allow": "allow", "http_code": 200, "body": body})
    assert status == "ok" and trunc is False
    assert keys == {d.KEY_PITR_PROBE}  # ONLY the frozen exact key retained; unexpected keys dropped
    # full run: the unexpected key names must never reach stdout/stderr
    with _NoNetwork():
        _run(d, FakeState(list_body=body))
    out = capsys.readouterr()
    for leak in ("SECRET_OTHER_KEY_LEAK", "etc/passwd", "RID_LEAK"):
        assert leak not in (out.out + out.err)


def test_list_keys_malformed_and_unavailable():
    d = _load_diag()
    assert d._list_keys({"allow": "allow", "http_code": 200, "body": b"<ListBucketResult><Contents"}) == \
        ("malformed", set(), False)
    assert d._list_keys({"allow": "deny", "http_code": 403, "body": b""}) == ("unavailable", set(), False)
    assert d._list_keys("not-a-dict") == ("unavailable", set(), False)


def test_retention_only_when_lock_resolved_present():
    d = _load_diag()
    rec = []
    # lock present via List -> retention IS read
    _diag_run_report(d, FakeState(present=[d.KEY_LOCKED],
                                  retention={"mode": "GOVERNANCE", "until": "2026-08-16T00:15:00Z"}))
    # lock absent via List -> retention NOT read (no GetObjectRetention issued)
    with _NoNetwork():
        _, rec = _run(d, FakeState(listed=[]))
    assert not [r for r in rec if r["op"] == "GetObjectRetention"]
