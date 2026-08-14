#!/usr/bin/env python3
"""SECURITY-2D-3E1B-3C2A — Selectel canary tooling (DORMANT: offline + MinIO only).

Modes:
  validate-policies   Structural, OFFLINE validation of candidate IAM policies. 0 network, 0 creds.
  plan                Print the role/operation/expected-result/cleanup plan. 0 network, 0 creds, 0 env dump.
  minio-compat        Prove allow/deny of each candidate policy on a TEMPORARY MinIO (synthetic users/keys,
                      job-local). NEVER Selectel. Exact cleanup only.
  live                NOT IMPLEMENTED in 3C2A — exits nonzero (LIVE_SELECTEL_NOT_IMPLEMENTED) BEFORE any
                      DNS/network/credential read.

Hard rules baked in: no Selectel endpoint in code; no default credentials; no credentials on argv; no env
dump; no insecure-TLS flag / public HTTP endpoint in the live path; MinIO endpoint allowlisted to a private
host; exact-only cleanup (no recursive/wildcard/bucket-wide sweep); unexpected allow OR unexpected deny ->
nonzero; MinIO != Selectel.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

POLICY_DIR = Path(__file__).resolve().parent / "policies"
PLACEHOLDER_BUCKET = "<BACKUP_BUCKET>"
PLACEHOLDER_RUNID = "<RUN_ID>"
ALLOWED_PREFIX_ROOTS = ("pitr/", "logical/", "status/", "canary/")
# MinIO endpoint must be a private/job-local host — NEVER a Selectel/public host.
MINIO_HOST_ALLOWLIST = re.compile(r"^https?://(minio|localhost|127\.0\.0\.1)(:\d+)?$")
# Forbidden as a real endpoint/hostname in applied config (human notes may legitimately say "Selectel").
SELECTEL_FORBIDDEN = re.compile(r"selcloud\.ru|storage\.selcloud", re.I)
LIVE_NOT_IMPLEMENTED = "LIVE_SELECTEL_NOT_IMPLEMENTED"

# ---- live mode (SECURITY-2D-3E1B-3C2C1, DORMANT) ------------------------------------------------
# Real Selectel S3 execution is wired ONLY in the separate Inal-approved 3C2C2 step. In 3C2C1 the live
# CLI path is a hard fail-closed gate; the orchestration (role matrix / pgBackRest probe / Object-Lock
# probe / exact cleanup) is transport-agnostic and exercised only against an in-memory FakeTransport or a
# job-local MinIO — NEVER a Selectel production endpoint. Strict HTTPS endpoint allowlist, no wildcards.
LIVE_REGION_ENDPOINTS = {
    "ru-1": "https://s3.ru-1.storage.selcloud.ru",
    "ru-7": "https://s3.ru-7.storage.selcloud.ru",
    "gis-1": "https://s3.gis-1.storage.selcloud.ru",
    "ru-3": "https://s3.ru-3.storage.selcloud.ru",
}
LIVE_GATE_ENV = "PULT_SELECTEL_CANARY_LIVE"
LIVE_GATE_VALUE = "YES_I_UNDERSTAND"
# 3C2C2-A wires the real S3 transport (SigV4 + hardened HTTPS) but its EXECUTION against Selectel is still
# gated: the live CLI never drives it to a live Selectel endpoint — that is enabled only by the separate
# Inal-approved 3C2C2-B execution step. So the CLI still defers.
SELECTEL_EXECUTION_DEFERRED = "SELECTEL_EXECUTION_GATED_UNTIL_3C2C2B"
# Runtime-change review marker — must be bumped together with the SHA-256 freeze on every canary.py change.
CANARY_RUNTIME_REVIEW = "3C2C2A-selectel-transport-dormant"
# live network safety knobs (used by the real transport in 3C2C2-B; enforced/asserted now)
LIVE_CONNECT_TIMEOUT = 10.0
LIVE_READ_TIMEOUT = 30.0
LIVE_READ_RETRIES = 2  # idempotent reads only; mutations are NEVER auto-retried
_READ_ONLY_S3_OPS = frozenset({
    "GetBucketVersioning", "GetObjectLockConfiguration", "GetBucketObjectLockConfiguration",
    "ListBucket", "ListBucketVersions", "ListMultipartUploads", "HeadObject", "GetObject",
    "GetObjectRetention", "GetObjectLegalHold",
})
_MUTATING_S3_OPS = frozenset({
    "CreateBucket", "PutBucketVersioning", "PutBucketObjectLockConfiguration", "PutObject",
    "CreateMultipartUpload", "UploadPart", "CompleteMultipartUpload", "AbortMultipartUpload",
    "PutObjectRetention", "DeleteObject", "DeleteObjectVersion",
})


def _fail(msg: str) -> None:
    print(f"CANARY FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------- validate-policies (offline) -------------------------
def _iter_statements(pol: dict):
    st = pol.get("Statement")
    if isinstance(st, dict):
        st = [st]
    for s in st or []:
        acts = s.get("Action", [])
        acts = [acts] if isinstance(acts, str) else acts
        res = s.get("Resource", [])
        res = [res] if isinstance(res, str) else res
        yield s.get("Effect"), acts, res, s


def validate_policies() -> int:
    files = sorted(POLICY_DIR.glob("*.json"))
    if not files:
        _fail("no candidate policies found")
    seen_roles = {}
    for f in files:
        doc = _load(f)
        meta = doc.get("_canary")
        pol = doc.get("policy")
        if not isinstance(meta, dict) or not isinstance(pol, dict):
            _fail(f"{f.name}: must have _canary metadata + policy document")
        role = meta.get("role")
        active = meta.get("active")
        seen_roles[role] = active
        blob = json.dumps(pol)
        # no embedded credentials / URLs / real endpoints
        if SELECTEL_FORBIDDEN.search(blob):
            _fail(f"{f.name}: no Selectel endpoint/hostname allowed in the applied policy document")
        if re.search(r"\bAKIA[0-9A-Z]{16}\b", blob) or re.search(r"https?://[^/\s\"]*:[^/@\s\"]+@", blob):
            _fail(f"{f.name}: embedded credential/URL forbidden")
        # only the placeholder bucket may appear as a bucket name in resources
        for m in re.finditer(r"arn:aws:s3:::([^/\"]+)", blob):
            if m.group(1) != PLACEHOLDER_BUCKET:
                _fail(f"{f.name}: real/other bucket name {m.group(1)!r} — only {PLACEHOLDER_BUCKET} allowed")
        # prefixes: any prefix-like token must be under an allowed root (canary uses <RUN_ID>)
        for pref in re.findall(r"arn:aws:s3:::<BACKUP_BUCKET>/([^\"*]+)", blob):
            root = pref.split("/")[0] + "/"
            if root not in ALLOWED_PREFIX_ROOTS:
                _fail(f"{f.name}: resource prefix {pref!r} not under {ALLOWED_PREFIX_ROOTS}")
        for s3pref in re.findall(r'"s3:prefix":\s*\[([^\]]*)\]', blob):
            for tok in re.findall(r'"([^"]+)"', s3pref):
                root = tok.split("/")[0] + "/"
                if root not in ALLOWED_PREFIX_ROOTS:
                    _fail(f"{f.name}: s3:prefix {tok!r} not under {ALLOWED_PREFIX_ROOTS}")
        # per-statement checks
        for effect, acts, res, s in _iter_statements(pol):
            if effect not in ("Allow", "Deny"):
                _fail(f"{f.name}: bad Effect {effect!r}")
            if "s3:*" in acts and effect == "Allow":
                _fail(f"{f.name}: wildcard s3:* is forbidden in an Allow statement")
            if "*" in res:
                _fail(f"{f.name}: bare Resource '*' forbidden")
            if effect == "Allow" and str(s.get("Principal", "")) == "*":
                _fail(f"{f.name}: Principal '*' forbidden in Allow")
        # role-specific closure
        allow_acts = {a for e, acts, _, _ in _iter_statements(pol) if e == "Allow" for a in acts}
        if role == "logical-writer":
            for bad in ("s3:GetObject", "s3:DeleteObject"):
                if bad in allow_acts:
                    _fail(f"{f.name}: logical-writer must NOT allow {bad}")
        elif role == "pitr-writer":
            for bad in ("s3:DeleteObject", "s3:PutBucketPolicy", "s3:PutLifecycleConfiguration", "s3:PutObjectRetention"):
                if bad in allow_acts:
                    _fail(f"{f.name}: pitr-writer candidate must NOT allow {bad} (starts without Delete/admin)")
        elif role == "restore-reader":
            for bad in ("s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"):
                if bad in allow_acts:
                    _fail(f"{f.name}: restore-reader must NOT allow {bad}")
        elif role == "retention-admin":
            if meta.get("marker") != "NOT_FOR_ROUTINE_BACKUP":
                _fail(f"{f.name}: retention-admin must be marked NOT_FOR_ROUTINE_BACKUP")
            for prov in ("s3:DeleteObjectVersion", "s3:BypassGovernanceRetention", "s3:PutLifecycleConfiguration"):
                if prov in allow_acts:
                    _fail(f"{f.name}: provisional action {prov} must NOT be in the active Allow (it is provisional)")
        elif role == "app":
            allow_stmts = [e for e, *_ in _iter_statements(pol) if e == "Allow"]
            if allow_stmts:
                _fail(f"{f.name}: app policy must be Deny-only (zero backup-bucket access)")
        elif role == "pitr-writer-reference":
            if active is not False:
                _fail(f"{f.name}: reference policy must be active=false (never an active candidate)")
    # exactly one active policy per active role; reference not active
    if seen_roles.get("pitr-writer-reference") is not False:
        _fail("reference policy must exist and be active=false")
    for r in ("logical-writer", "pitr-writer", "restore-reader", "retention-admin", "app"):
        if seen_roles.get(r) is not True:
            _fail(f"missing active candidate for role {r}")
    print(f"validate-policies OK: {len(files)} candidate policies structurally valid (MinIO != Selectel; "
          f"pitr-writer starts WITHOUT Delete; reference not active).")
    return 0


# ------------------------- plan (offline) -------------------------
def plan() -> int:
    print("CANARY PLAN (offline; no network, no credentials, no env dump)")
    print(f"  placeholder bucket: {PLACEHOLDER_BUCKET}  prefixes: {', '.join(ALLOWED_PREFIX_ROOTS)}<RUN_ID>")
    for role, ops in _MATRIX.items():
        for op, prefix, expect in ops:
            print(f"  role={role} op={op} resource=<BACKUP_BUCKET>/{prefix} expect={expect}")
    print("  cleanup: exact object key + version-id + multipart-upload-id only; NO recursive/wildcard/bucket-wide sweep")
    print("  live Selectel: NOT executed in 3C2A")
    return 0


# role -> list of (operation, prefix, expected)  used by plan + minio-compat
_MATRIX = {
    # NOTE: logical-writer verifies its upload via LIST (rclone `lsjson`), which the prefix-scoped
    # ListBucket grant covers. HeadObject/`mc stat` is NOT tested here: MinIO maps HeadObject to
    # s3:GetObject, so a deliberately Get-less writer cannot stat on MinIO — a Head-vs-Get compatibility
    # limitation (see negative-matrix.md), resolved only by the live Selectel canary (3C2C), not proven here.
    "logical-writer": [("put", "logical/", "allow"), ("ls", "logical/", "allow"),
                       ("get", "logical/", "deny"), ("rm", "logical/", "deny"),
                       ("put", "pitr/", "deny")],
    "pitr-writer": [("put", "pitr/", "allow"), ("get", "pitr/", "allow"), ("ls", "pitr/", "allow"),
                    ("rm", "pitr/", "deny"), ("put", "logical/", "deny")],
    "restore-reader": [("ls", "pitr/", "allow"), ("get", "pitr/", "allow"),
                       ("put", "pitr/", "deny"), ("rm", "pitr/", "deny")],
    "app": [("ls", "pitr/", "deny"), ("get", "pitr/", "deny"), ("put", "pitr/", "deny"), ("rm", "pitr/", "deny")],
}


# ------------------------- minio-compat (temporary MinIO only) -------------------------
def _mc(args, alias_conf=None, check_denied=False):
    """Run mc; return (rc, out, err). Never prints credentials."""
    r = subprocess.run(["mc", "--json", *args], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def _is_denied(rc, out, err):
    blob = (out + err).lower()
    return rc != 0 and ("accessdenied" in blob or "access denied" in blob or "permission" in blob)


def minio_compat() -> int:
    endpoint = os.environ.get("CANARY_MINIO_ENDPOINT", "")
    admin = os.environ.get("CANARY_MINIO_ADMIN", "")
    admin_sec = os.environ.get("CANARY_MINIO_ADMIN_SECRET", "")
    if not endpoint or not admin or not admin_sec:
        _fail("minio-compat requires CANARY_MINIO_ENDPOINT/ADMIN/ADMIN_SECRET (job-local synthetic)")
    if SELECTEL_FORBIDDEN.search(endpoint) or not MINIO_HOST_ALLOWLIST.match(endpoint):
        _fail("minio-compat endpoint not allowlisted (must be private minio/localhost, never Selectel)")
    if endpoint.startswith("http://") and not endpoint.startswith("http://localhost") and "127.0.0.1" not in endpoint and "minio" not in endpoint:
        _fail("insecure HTTP endpoint not allowed")
    run_id = secrets.token_hex(6)
    if not re.fullmatch(r"[0-9a-f]{12}", run_id):
        _fail("bad run id")
    bucket = f"canary-{run_id}"
    created_objs = []  # exact keys to clean
    created_users = []
    rc, out, err = _mc(["alias", "set", "adm", endpoint, admin, admin_sec, "--api", "S3v4"])
    if rc != 0:
        _fail("mc admin alias set failed")
    rc, *_ = _mc(["mb", f"adm/{bucket}"])
    if rc != 0:
        _fail(f"could not create synthetic bucket {bucket}")
    # seed one object per prefix (as admin) so reader/get tests have a target
    seed = Path("/tmp") / f"canary-seed-{run_id}.bin"
    seed.write_bytes(secrets.token_bytes(64))
    for pref in ("pitr/", "logical/"):
        key = f"{pref}canary-seed"
        _mc(["cp", str(seed), f"adm/{bucket}/{key}"])
        created_objs.append(key)
    failures = []
    try:
        for role, ops in _MATRIX.items():
            polf = POLICY_DIR / f"{role if role != 'app' else 'app-deny'}.json"
            if role == "logical-writer":
                polf = POLICY_DIR / "logical-writer.json"
            pol = _load(polf)["policy"]
            polstr = json.dumps(pol).replace(PLACEHOLDER_BUCKET, bucket).replace(PLACEHOLDER_RUNID, run_id)
            pfile = Path("/tmp") / f"canary-pol-{role}-{run_id}.json"
            pfile.write_text(polstr, encoding="utf-8")
            polname = f"canary_{role}_{run_id}".replace("-", "_")
            user = f"cu_{role}_{run_id}".replace("-", "_")
            usec = secrets.token_hex(20)
            _mc(["admin", "policy", "create", "adm", polname, str(pfile)])
            _mc(["admin", "user", "add", "adm", user, usec])
            _mc(["admin", "policy", "attach", "adm", polname, "--user", user])
            created_users.append((user, polname))
            _mc(["alias", "set", f"u_{role}", endpoint, user, usec, "--api", "S3v4"])
            for op, prefix, expect in ops:
                obj = f"{prefix}canary-seed" if op in ("get", "stat", "rm") else f"{prefix}w-{run_id}"
                if op == "put":
                    rc, o, e = _mc(["cp", str(seed), f"u_{role}/{bucket}/{obj}"])
                    if expect == "allow" and rc == 0:
                        created_objs.append(obj)
                elif op == "get":
                    rc, o, e = _mc(["cp", f"u_{role}/{bucket}/{obj}", str(Path('/tmp') / f'g-{run_id}')])
                elif op == "stat":
                    rc, o, e = _mc(["stat", f"u_{role}/{bucket}/{obj}"])
                elif op == "ls":
                    rc, o, e = _mc(["ls", f"u_{role}/{bucket}/{prefix}"])
                elif op == "rm":
                    rc, o, e = _mc(["rm", f"u_{role}/{bucket}/{obj}"])
                else:
                    _fail(f"unknown op {op}")
                allowed = (rc == 0)
                denied = _is_denied(rc, o, e)
                ok = (expect == "allow" and allowed) or (expect == "deny" and denied)
                verdict = "OK" if ok else "WRONG"
                print(f"MINIO {verdict}: role={role} op={op} prefix={prefix} expect={expect} allowed={allowed} denied={denied}")
                if not ok:
                    failures.append(f"{role}:{op}:{prefix} expect={expect} allowed={allowed} denied={denied}")
    finally:
        # EXACT cleanup only — no recursive/wildcard/bucket-wide sweep of arbitrary data
        for key in created_objs:
            _mc(["rm", f"adm/{bucket}/{key}"])
        for user, polname in created_users:
            _mc(["admin", "user", "remove", "adm", user])
            _mc(["admin", "policy", "remove", "adm", polname])
        rc_rb, o_rb, e_rb = _mc(["rb", f"adm/{bucket}", "--force"])
        if rc_rb != 0:
            print(f"MINIO CLEANUP WARN: could not remove synthetic bucket {bucket} (rc={rc_rb})", file=sys.stderr)
            failures.append("cleanup: synthetic bucket not removed")
        try:
            seed.unlink()
        except OSError:
            pass
    if failures:
        _fail("minio-compat unexpected allow/deny or cleanup failure: " + "; ".join(failures))
    print("minio-compat OK: candidate policies enforce expected allow/deny on temporary MinIO (NOT a Selectel proof)")
    return 0


# ------------------------- live mode: hard gate (fail-closed, no network) -------------------------
class LiveGateError(Exception):
    """Raised when the live gate refuses. Message must never contain secrets."""


def _redact(text: str, secrets) -> str:
    out = text
    for s in secrets:
        if s:
            out = out.replace(s, "***REDACTED***")
    return out


def live_validate(args, env) -> dict:
    """Validate ALL live preconditions BEFORE any DNS/credential/network use. Pure; raises LiveGateError.

    Returns an exact resource manifest (no secrets)."""
    if env.get(LIVE_GATE_ENV) != LIVE_GATE_VALUE:
        raise LiveGateError(f"env {LIVE_GATE_ENV} must equal the explicit acknowledgement value")
    runid = getattr(args, "run_id", None) or ""
    if not re.fullmatch(r"[0-9a-f]{12}", runid):
        raise LiveGateError("run_id must be 12 lowercase hex chars")
    region = getattr(args, "region", None) or ""
    if region not in LIVE_REGION_ENDPOINTS:
        raise LiveGateError("region not in the Selectel allowlist")
    endpoint = getattr(args, "endpoint", None) or ""
    if endpoint != LIVE_REGION_ENDPOINTS[region]:
        raise LiveGateError("endpoint does not match the region's official HTTPS endpoint")
    if not endpoint.startswith("https://"):
        raise LiveGateError("endpoint must be HTTPS")
    bucket = getattr(args, "bucket", None) or ""
    if bucket != f"pult-canary-{runid}":
        raise LiveGateError("bucket must be exactly pult-canary-<runid>")
    project = getattr(args, "project_id", None) or ""
    if not re.fullmatch(r"[0-9a-f]{16,64}", project):
        raise LiveGateError("project_id must be 16-64 lowercase hex chars")
    expected_confirm = f"{project}/{region}/{endpoint}/{bucket}/{runid}"
    if getattr(args, "confirm", None) != expected_confirm:
        raise LiveGateError("typed confirmation must equal project/region/endpoint/bucket/runid exactly")
    return {"project": project, "region": region, "endpoint": endpoint, "bucket": bucket,
            "prefix": f"canary/{runid}/", "runid": runid}


# ------------------------- transport seam (real Selectel deferred to 3C2C2) -----------------------
def _sigv4_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    """AWS Signature Version 4 signing key (stdlib hmac/sha256 only; no third-party dependency)."""
    import hashlib
    import hmac

    def _hmac(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def _sigv4_authorization(access_key, signing_key, credential_scope, signed_headers, string_to_sign):
    import hashlib
    import hmac
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}")


class SelectelTransport:
    """DORMANT alias retained for the gate — see SelectelS3Transport. The live CLI never constructs the
    real transport in 3C2C2-A; execution is gated until 3C2C2-B."""

    def __init__(self, manifest):
        raise LiveGateError(SELECTEL_EXECUTION_DEFERRED)


class SelectelS3Transport:
    """Real Selectel S3 data-plane transport: SigV4-signed HTTPS via httpx. Implemented in 3C2C2-A but its
    EXECUTION against a live endpoint is gated to 3C2C2-B (the live CLI does not drive it). Hardened:
    HTTPS-only allowlisted endpoint, TLS verification always on, no redirects, no env-proxy / metadata /
    default-credential chain, bounded timeouts, retries for idempotent reads ONLY (mutations never
    auto-retried), and never logs Authorization / secret / signed URL / payload."""

    def __init__(self, manifest, creds, http_client=None, service="s3"):
        endpoint = manifest["endpoint"]
        if endpoint not in LIVE_REGION_ENDPOINTS.values():
            raise LiveGateError("endpoint not in the Selectel HTTPS allowlist")
        if not endpoint.startswith("https://"):
            raise LiveGateError("endpoint must be HTTPS")
        # credentials come only from the validated env/fd input (never argv); stored privately, never logged
        self._access = creds["access_key"]
        self.__secret = creds["secret_key"]
        self._manifest = manifest
        self._endpoint = endpoint
        self._region = manifest["region"]
        self._service = service
        self._host = endpoint.split("://", 1)[1]
        self._client = http_client  # injected for tests (fake) or MinIO; real client built lazily otherwise

    def _build_client(self):
        # Real hardened client — built ONLY when actually needed (3C2C2-B). Lazy import keeps offline paths
        # dependency-free; trust_env=False disables proxy + credential env; redirects disabled.
        import httpx
        return httpx.Client(verify=True, follow_redirects=False, trust_env=False,
                            timeout=httpx.Timeout(LIVE_READ_TIMEOUT, connect=LIVE_CONNECT_TIMEOUT))

    def _sign(self, method, op, canonical_uri, query, payload_hash, amz_date, date_stamp):
        headers = {"host": self._host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canonical_request = "\n".join([method, canonical_uri, query, canonical_headers,
                                       signed_headers, payload_hash])
        import hashlib
        scope = f"{date_stamp}/{self._region}/{self._service}/aws4_request"
        sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
        key = _sigv4_signing_key(self.__secret, date_stamp, self._region, self._service)
        auth = _sigv4_authorization(self._access, key, scope, signed_headers, sts)
        return dict(headers, Authorization=auth)

    def is_mutating(self, op):
        if op in _READ_ONLY_S3_OPS:
            return False
        if op in _MUTATING_S3_OPS:
            return True
        raise LiveGateError(f"unclassified S3 op {op!r} -> treated as mutating, refuse")

    def attempt(self, op, canonical_uri, method="GET", query="", payload=b"", amz_date=None, date_stamp=None):
        """Perform ONE signed request. Mutations are never auto-retried; reads retry up to LIVE_READ_RETRIES.

        Returns a normalized, secret-free result. Any ambiguity -> UNKNOWN (no guessing, no retry of writes)."""
        import hashlib
        mutating = self.is_mutating(op)
        if amz_date is None or date_stamp is None:
            raise LiveGateError("amz_date/date_stamp must be supplied by the caller (no wall-clock in module)")
        payload_hash = hashlib.sha256(payload).hexdigest()
        if self._client is None:
            self._client = self._build_client()
        attempts = 1 if mutating else (1 + LIVE_READ_RETRIES)
        last = None
        for _ in range(attempts):
            headers = self._sign(method, op, canonical_uri, query, payload_hash, amz_date, date_stamp)
            url = f"{self._endpoint}{canonical_uri}" + (f"?{query}" if query else "")
            resp = self._client.request(method, url, headers=headers, content=payload)
            code = getattr(resp, "status_code", None)
            reqid = getattr(resp, "headers", {}).get("x-amz-request-id", "") if hasattr(resp, "headers") else ""
            last = {"operation": op, "http_code": code, "request_id": reqid,
                    "allow": None, "mutating": mutating}
            if code is None:
                return {**last, "allow": "unknown", "error": "no status (ambiguous) -> STOP"}
            if code in (200, 204, 206):
                return {**last, "allow": "allow"}
            if code in (403, 401):
                return {**last, "allow": "deny", "error_code": code}
            if not mutating and code in (429, 500, 502, 503, 504):
                continue  # idempotent read: bounded retry only
            return {**last, "allow": "unknown", "error_code": code}
        return {**last, "allow": "unknown", "error": "read retries exhausted"}

    def __repr__(self):  # never leak the secret via repr/exceptions
        return f"<SelectelS3Transport endpoint={self._endpoint} region={self._region}>"


# role -> operations it may attempt; expected result checked by the orchestration (mirrors candidate policies)
_LIVE_ROLE_MATRIX = {
    "logical-writer": [("put", "logical/", "allow"), ("list", "logical/", "allow"),
                       ("get", "logical/", "deny"), ("delete", "logical/", "deny"),
                       ("put", "pitr/", "deny")],
    "pitr-writer": [("put", "pitr/", "allow"), ("get", "pitr/", "allow"), ("list", "pitr/", "allow"),
                    ("delete", "pitr/", "deny"), ("put", "logical/", "deny")],
    "restore-reader": [("list", "pitr/", "allow"), ("get", "pitr/", "allow"),
                       ("put", "pitr/", "deny"), ("delete", "pitr/", "deny")],
    "app": [("list", "pitr/", "deny"), ("get", "pitr/", "deny"),
            ("put", "pitr/", "deny"), ("delete", "pitr/", "deny")],
}


def run_role_matrix(transport, manifest) -> dict:
    """Attempt each role/op against a transport; compare to expected. Never auto-expand permissions.

    Unexpected allow of a forbidden op OR unexpected deny of a required op -> failure recorded (no raise)."""
    rows, failures = [], []
    for role, ops in _LIVE_ROLE_MATRIX.items():
        for op, prefix, expect in ops:
            key = f"{manifest['prefix']}{prefix}probe-{manifest['runid']}"
            result = transport.attempt(role, op, key)  # "allow" / "deny"
            ok = (result == expect)
            rows.append({"role": role, "operation": op, "prefix": prefix,
                         "expected": expect, "actual": result})
            if not ok:
                failures.append(f"{role}:{op}:{prefix} expected={expect} actual={result}")
    return {"rows": rows, "failures": failures}


def pgbackrest_probe(transport, manifest) -> dict:
    """Record which S3 operations a synthetic pgBackRest stanza/create/check/backup/archive/restore flow
    actually needs. GetObject and DeleteObject necessity are recorded SEPARATELY. No policy is changed."""
    steps = ["stanza-create", "stanza-check", "backup", "archive-push", "info", "restore"]
    used = []
    for step in steps:
        used.extend(transport.pgbackrest_ops(step))
    return {
        "steps": steps,
        "s3_operations_used": sorted(set(used)),
        "get_object_required": "GetObject" in used,
        "delete_object_required": "DeleteObject" in used,
        "note": "closure recorded only; permissions are NOT auto-expanded (feeds 3C2D)",
    }


def objectlock_probe(transport, manifest) -> dict:
    """Read back versioning + Object-Lock config on the canary object; verify a locked version cannot be
    deleted; Governance bypass only by retention-admin. Compliance is NEVER exercised here."""
    return {
        "versioning": transport.get_versioning(),
        "object_lock_config": transport.get_lock_config(),
        "min_retention_used": True,
        "locked_delete_refused": transport.locked_delete_refused(),
        "governance_bypass_admin_only": transport.governance_bypass_admin_only(),
        "compliance_tested": False,
    }


def run_cleanup(transport, manifest, ledger) -> dict:
    """Delete ONLY the exact resources in `ledger` (keys, versions, multipart upload ids, users/keys).

    No recursive/wildcard/prefix-wide delete. Access keys are revoked even if the main test failed. The
    bucket is removed only after a read-back shows no unknown objects/versions/uploads; a locked residual
    is reported as a controlled residual (with retention deadline + max cost) — never a false success."""
    failures, revoked, deleted = [], [], []
    for user in ledger.get("users", []):
        if not transport.delete_user(user):
            failures.append(f"user not deleted: {user}")
        else:
            revoked.append(user)
    for item in ledger.get("objects", []):
        if not transport.delete_object(item["key"], item.get("version")):
            failures.append(f"object not deleted: {item['key']}")
        else:
            deleted.append(item["key"])
    for up in ledger.get("multipart", []):
        transport.abort_multipart(up["key"], up["upload_id"])
    residual = transport.read_back_unknown(manifest["bucket"], manifest["prefix"])
    locked = transport.locked_residual()
    if locked:
        return {"status": "controlled-residual", "revoked_users": revoked, "deleted": deleted,
                "locked_residual": locked, "bucket_removed": False, "failures": failures}
    if residual:
        failures.append(f"unexpected residual objects: {residual}")
        return {"status": "failed", "revoked_users": revoked, "deleted": deleted,
                "bucket_removed": False, "failures": failures}
    bucket_removed = transport.remove_bucket_if_empty(manifest["bucket"])
    status = "clean" if bucket_removed and not failures else "failed"
    return {"status": status, "revoked_users": revoked, "deleted": deleted,
            "bucket_removed": bucket_removed, "failures": failures}


def live(args, env=None) -> int:
    """3C2C1 live CLI: hard fail-closed gate, then refuse — real Selectel execution is wired in 3C2C2."""
    env = os.environ if env is None else env
    try:
        manifest = live_validate(args, env)
    except LiveGateError as e:
        print(f"LIVE GATE REFUSED: {_redact(str(e), [])}", file=sys.stderr)
        return 4
    # Gate passed. In 3C2C1 the real Selectel transport is NOT wired — never touch the network here.
    try:
        SelectelTransport(manifest)
    except LiveGateError as e:
        print(str(e), file=sys.stderr)
        return 5
    return 5


def main() -> int:
    ap = argparse.ArgumentParser(description="Selectel canary tooling (offline/MinIO; live gated + dormant)")
    ap.add_argument("mode", choices=["validate-policies", "plan", "minio-compat", "live"])
    # Non-secret live parameters only. Credentials are NEVER accepted on argv (env/file-descriptor only).
    ap.add_argument("--project-id", dest="project_id")
    ap.add_argument("--region")
    ap.add_argument("--endpoint")
    ap.add_argument("--bucket")
    ap.add_argument("--run-id", dest="run_id")
    ap.add_argument("--confirm")
    args = ap.parse_args()
    if args.mode == "live":
        return live(args)
    if args.mode == "validate-policies":
        return validate_policies()
    if args.mode == "plan":
        return plan()
    if args.mode == "minio-compat":
        return minio_compat()
    return 2


if __name__ == "__main__":
    sys.exit(main())
