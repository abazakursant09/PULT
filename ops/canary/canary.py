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
CANARY_RUNTIME_REVIEW = "3C2D-v9-stale-safe-locked-delete-probe"
# live network safety knobs (used by the real transport in 3C2C2-B; enforced/asserted now)
LIVE_CONNECT_TIMEOUT = 10.0
LIVE_READ_TIMEOUT = 30.0
LIVE_READ_RETRIES = 2  # idempotent reads only; mutations are NEVER auto-retried
EXECUTE_MAX_DEADLINE_WINDOW_SEC = 1800  # deadline must be in (now, now+30min]
_READ_ONLY_S3_OPS = frozenset({
    "GetBucketVersioning", "GetObjectLockConfiguration", "GetBucketObjectLockConfiguration",
    "ListBucket", "ListBucketVersions", "ListMultipartUploads", "HeadObject", "GetObject",
    "GetObjectRetention", "GetObjectLegalHold",
})
_MUTATING_S3_OPS = frozenset({
    "CreateBucket", "PutBucketVersioning", "PutBucketObjectLockConfiguration", "PutObject",
    "CreateMultipartUpload", "UploadPart", "CompleteMultipartUpload", "AbortMultipartUpload",
    "PutObjectRetention", "DeleteObject", "DeleteObjectVersion", "DeleteBucket",
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


# AWS SigV4 canonical-query-string encoding (per the AWS "Create a canonical request" spec): every parameter
# name and value is URI-encoded with RFC 3986 unreserved chars left as-is and EVERYTHING else percent-encoded
# (space -> %20 not '+', '/' -> %2F in the query, no double-encoding); parameters are sorted by encoded name
# then encoded value; a parameter with no value is emitted as "name=". The SAME normalized string is signed
# AND sent on the wire, so the client-signed canonical query is byte-identical to what the server recomputes.
_QUERY_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")


def _query_encode(s: str) -> str:
    out = []
    for byte in s.encode("utf-8"):
        ch = chr(byte)
        out.append(ch if ch in _QUERY_UNRESERVED else f"%{byte:02X}")
    return "".join(out)


def _canonical_query(raw: str) -> str:
    """Normalize a caller's raw query (e.g. 'versioning', 'prefix=canary/x/', 'retention&versionId=v') into the
    exact AWS SigV4 canonical query string. Empty -> ''. Split on '&', split each token on the FIRST '=' (a
    value may itself contain '='), percent-encode name and value, sort by (encoded name, encoded value), keep
    repeated names, and always emit 'name=' (empty value included)."""
    if not raw:
        return ""
    pairs = []
    for tok in raw.split("&"):
        if tok == "":
            continue
        if "=" in tok:
            name, value = tok.split("=", 1)
        else:
            name, value = tok, ""
        pairs.append((_query_encode(name), _query_encode(value)))
    pairs.sort()
    return "&".join(f"{n}={v}" for n, v in pairs)


class SelectelTransport:
    """DORMANT alias retained for the gate — see SelectelS3Transport. The live CLI never constructs the
    real transport in 3C2C2-A; execution is gated until 3C2C2-B."""

    def __init__(self, manifest):
        raise LiveGateError(SELECTEL_EXECUTION_DEFERRED)


# Secret-free live error categories (closed set; parity with diagnose.py). A 401/403 alone does NOT prove an
# IAM policy deny — it can be an invalid key, a signature mismatch, or an auth failure. attempt() now attaches
# a category derived ONLY from the HTTP status class + an allowlisted S3 <Code> so the summary can distinguish
# a genuine policy deny from an auth/signature failure, WITHOUT emitting the raw body/Code/RequestId/anything.
LIVE_ERROR_CATEGORIES = (
    "ok", "not-found", "invalid-access-key", "signature-mismatch", "access-denied",
    "authentication-failed", "timeout", "tls-error", "network-error", "service-error",
    "malformed-response", "invalid-credential-format", "unknown",
)
# Access key must be printable ASCII with no whitespace/control chars (a non-ASCII/space char would raise a
# UnicodeEncodeError while httpx encodes the Authorization header). Validated BEFORE any transport/DNS/socket.
_CRED_ACCESS_RE = re.compile(r"^[\x21-\x7e]+$")


def _credential_format_ok(access, secret) -> bool:
    """True only if the access key is a non-empty printable-ASCII token (no whitespace/control) and the secret
    is non-empty with no control/CR/LF. The secret value itself is never inspected beyond char classes, never
    logged, and its length is never recorded."""
    if not isinstance(access, str) or not isinstance(secret, str) or not access or not secret:
        return False
    if not _CRED_ACCESS_RE.fullmatch(access):
        return False
    try:
        access.encode("ascii")
    except UnicodeEncodeError:
        return False
    return all(0x20 <= ord(ch) != 0x7f for ch in secret)
_S3_ERROR_CODE_CATEGORY = {
    "InvalidAccessKeyId": "invalid-access-key",
    "SignatureDoesNotMatch": "signature-mismatch",
    "AuthorizationHeaderMalformed": "signature-mismatch",
    "AccessDenied": "access-denied",
    "InvalidToken": "authentication-failed",
    "ExpiredToken": "authentication-failed",
}


def _s3_error_code_category(body):
    """Read ONLY the <Code> tag and map via the allowlist. Never returns Message/Resource/RequestId/HostId/
    StringToSign/CanonicalRequest or raw text. Malformed XML -> 'malformed-response'; unknown/absent Code -> None."""
    if not body:
        return None
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(body)
    except Exception:
        return "malformed-response"
    code = None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "Code":
            code = (el.text or "").strip()
            break
    if not code:
        return None
    return _S3_ERROR_CODE_CATEGORY.get(code)


def _http_result_category(code, body):
    """Map an HTTP status + body to ONE secret-free category (status class + allowlisted <Code> only)."""
    if code in (200, 204, 206):
        return "ok"
    if code == 404:
        return "not-found"
    xml_cat = _s3_error_code_category(body)
    if xml_cat:
        return xml_cat
    if code == 401:
        return "authentication-failed"
    if code == 403:
        return "access-denied"
    if isinstance(code, int) and 500 <= code <= 599:
        return "service-error"
    return "unknown"


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

    def _sign(self, method, op, canonical_uri, query, payload_hash, amz_date, date_stamp, extra_headers=None):
        headers = {"host": self._host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        for k, v in (extra_headers or {}).items():
            headers[k.lower()] = v  # e.g. content-md5, content-type — signed so the write is bound to its body
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

    def attempt(self, op, canonical_uri, method="GET", query="", payload=b"", amz_date=None, date_stamp=None,
                extra_headers=None):
        """Perform ONE signed request. Mutations are never auto-retried; reads retry up to LIVE_READ_RETRIES.
        `extra_headers` (e.g. content-md5, content-type) are SIGNED. Returns a normalized, secret-free result
        incl. the response body for read-backs. Any ambiguity -> UNKNOWN (no guessing, no retry of writes)."""
        import hashlib
        mutating = self.is_mutating(op)
        if amz_date is None or date_stamp is None:
            raise LiveGateError("amz_date/date_stamp must be supplied by the caller (no wall-clock in module)")
        payload_hash = hashlib.sha256(payload).hexdigest()
        if self._client is None:
            self._client = self._build_client()
        # ONE normalizer feeds BOTH the signature and the wire query, so they are byte-identical (SigV4 fix).
        cquery = _canonical_query(query)
        attempts = 1 if mutating else (1 + LIVE_READ_RETRIES)
        last = None
        for _ in range(attempts):
            try:
                headers = self._sign(method, op, canonical_uri, cquery, payload_hash, amz_date, date_stamp,
                                     extra_headers)
                url = f"{self._endpoint}{canonical_uri}" + (f"?{cquery}" if cquery else "")
                resp = self._client.request(method, url, headers=headers, content=payload)
            except UnicodeError:
                # a non-ASCII credential slipped past the pre-check -> fail closed, no traceback / no key leak
                return {"operation": op, "http_code": None, "request_id": "", "version_id": "", "body": b"",
                        "allow": "unknown", "mutating": mutating, "category": "invalid-credential-format"}
            code = getattr(resp, "status_code", None)
            rh = getattr(resp, "headers", {}) or {}
            reqid = rh.get("x-amz-request-id", "")
            vid = rh.get("x-amz-version-id", "")
            body = getattr(resp, "content", b"") or b""
            cat = _http_result_category(code, body)  # secret-free cause category (status class + <Code> only)
            last = {"operation": op, "http_code": code, "request_id": reqid, "version_id": vid,
                    "body": body, "allow": None, "mutating": mutating, "category": cat}
            if code is None:
                return {**last, "allow": "unknown", "category": "unknown", "error": "no status (ambiguous) -> STOP"}
            if code in (200, 204, 206):
                return {**last, "allow": "allow"}
            if code in (403, 401):
                return {**last, "allow": "deny", "error_code": code}
            if not mutating and code in (429, 500, 502, 503, 504):
                continue  # idempotent read: bounded retry only
            return {**last, "allow": "unknown", "error_code": code}
        return {**last, "allow": "unknown", "category": "unknown", "error": "read retries exhausted"}

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


# NOTE: pgbackrest_probe() above is an OFFLINE DESIGN helper only (FakeTransport). Gate F does NOT prove a
# live pgBackRest closure — that needs a real pgBackRest binary run and is a separate, later step. The live
# orchestration below deliberately does NOT call it.


def _amz_ds(dt):
    return dt.strftime("%Y%m%dT%H%M%SZ"), dt.strftime("%Y%m%d")


def run_cleanup(admin_transport, manifest, ledger, clock) -> dict:
    """EXACT data-plane cleanup via the transport's attempt() interface ONLY (no phantom methods).

    Contract = Variant 1 (manual post-expiry): the canary deletes ONLY the exact unlocked object versions and
    multipart uploads it created. It does NOT delete the bucket automatically (retention-admin is not granted
    DeleteBucket) and does NOT touch control-plane keys/users/policies/project — those are MANUAL (Gate F6),
    recorded here. A Governance-locked object cannot be deleted before retain-until -> the run ends
    CONTROLLED_RESIDUAL with a secret-free ledger (bucket/key/versionId/retainUntil) for the manual post-expiry
    cleanup. Any UNKNOWN or non-locked residual -> FAILED. Mutations are single-shot; never a false clean."""
    amz, ds = _amz_ds(clock())
    bucket = manifest["bucket"]
    deleted, residual, failures = [], [], []
    for up in ledger.get("multipart", []):
        admin_transport.attempt("AbortMultipartUpload", f"/{bucket}/{up['key']}", method="DELETE",
                                query=f"uploadId={up['upload_id']}", amz_date=amz, date_stamp=ds)
    for o in ledger.get("objects", []):
        if o.get("locked"):
            residual.append({"key": o["key"], "version": o.get("version"), "locked": True,
                             "retain_until": o.get("retain_until")})
            continue  # locked object cannot be deleted before expiry — never attempted here
        q = f"versionId={o['version']}" if o.get("version") else ""
        r = admin_transport.attempt("DeleteObjectVersion", f"/{bucket}/{o['key']}", method="DELETE",
                                    query=q, amz_date=amz, date_stamp=ds)
        if r.get("allow") == "allow":
            deleted.append(o["key"])
        else:
            residual.append({"key": o["key"], "version": o.get("version"), "reason": r.get("allow"),
                             "locked": False})  # could not delete a non-locked object -> unknown residual
    locked = [x for x in residual if x.get("locked")]
    nonlocked = [x for x in residual if not x.get("locked")]
    manual = {"keys": list(ledger.get("users", [])), "policies": list(ledger.get("policies", [])),
              "bucket": bucket, "project": manifest.get("project"),
              "note": "control-plane + bucket deletion are MANUAL (Gate F6); post-expiry object delete is MANUAL"}
    if nonlocked:
        return {"status": "failed", "deleted": deleted, "residual": residual, "locked_residual": locked,
                "manual_cleanup": manual, "failures": failures + ["unknown/non-locked residual remains"]}
    if locked:
        return {"status": "controlled-residual", "deleted": deleted, "locked_residual": locked,
                "manual_cleanup": manual, "failures": failures}
    return {"status": "clean", "deleted": deleted, "residual": [], "manual_cleanup": manual, "failures": failures}


# ------------------------- execute-live gate (Gate F) — fail-closed BEFORE any network/credential read ----
EXECUTE_LIVE_REGION = "ru-3"                      # canary is pinned to ru-3 (SPb)
EXECUTE_MAX_OBJECT_BYTES = 10 * 1024 * 1024       # 10 MiB hard cap
EXECUTE_LIVE_ACK_PREFIX = "PULT-CANARY-EXECUTE-"  # --ack must equal EXECUTE_LIVE_ACK_PREFIX + run_id
_CANARY_ROLES = ("logical-writer", "pitr-writer", "restore-reader", "retention-admin", "app-deny")
_DEADLINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _parse_deadline(s):
    import datetime
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def _utcnow():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _retention_xml(mode, retain_until):
    return (f'<Retention xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f'<Mode>{mode}</Mode><RetainUntilDate>{retain_until}</RetainUntilDate></Retention>').encode("utf-8")


def _content_md5(b):
    import base64
    import hashlib
    return base64.b64encode(hashlib.md5(b).digest()).decode("ascii")


def _parse_retention_xml(body):
    """Fail-closed parse of a GetObjectRetention response. Returns {mode, retain_until} or raises."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(body)
    except Exception:
        raise LiveGateError("malformed retention XML")

    def _t(tag):
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == tag:
                return (el.text or "").strip()
        return None

    mode, until = _t("Mode"), _t("RetainUntilDate")
    if not mode or not until:
        raise LiveGateError("retention XML missing Mode/RetainUntilDate")
    return {"mode": mode, "retain_until": until}


def _rfc3339_instant(s):
    """Parse an RFC3339 timestamp into a timezone-aware UTC datetime so two equivalent instants written in
    different textual forms (trailing Z vs +00:00, fractional seconds, other offsets) compare EQUAL. A naive
    (no-timezone), malformed, or out-of-range value raises LiveGateError (fail-closed — never treated as a
    match). No part of the string is ever logged."""
    import datetime
    if not isinstance(s, str) or not s.strip():
        raise LiveGateError("empty retain-until")
    text = s.strip()
    iso = (text[:-1] + "+00:00") if text[-1] in ("Z", "z") else text
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        raise LiveGateError("malformed retain-until")
    if dt.tzinfo is None:
        raise LiveGateError("naive retain-until (no timezone)")
    return dt.astimezone(datetime.timezone.utc)


def execute_validate(args, manifest, clock) -> dict:
    """Strict Gate-F execute gate. Raises LiveGateError BEFORE any DNS/socket/credential read unless every
    one-time condition holds. `clock` (injected, returns a UTC datetime) proves the deadline is in the future
    and within the max window. Ordinary `live` without --execute-live never reaches this."""
    import datetime
    if not getattr(args, "execute_live", False):
        raise LiveGateError("execute-live requires the explicit --execute-live flag")
    if manifest["region"] != EXECUTE_LIVE_REGION:
        raise LiveGateError("execute-live is pinned to region ru-3")
    if manifest["endpoint"] != LIVE_REGION_ENDPOINTS[EXECUTE_LIVE_REGION]:
        raise LiveGateError("endpoint must be the official ru-3 S3 endpoint")
    if getattr(args, "ack", None) != EXECUTE_LIVE_ACK_PREFIX + manifest["runid"]:
        raise LiveGateError("--ack must equal PULT-CANARY-EXECUTE-<runid> exactly")
    mob = getattr(args, "max_object_bytes", None)
    if not isinstance(mob, int) or not (0 < mob <= EXECUTE_MAX_OBJECT_BYTES):
        raise LiveGateError("--max-object-bytes must be an int in (0, 10 MiB]")
    if not _DEADLINE_RE.match(getattr(args, "deadline", "") or ""):
        raise LiveGateError("--deadline must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    deadline = _parse_deadline(args.deadline)
    now = clock()
    if not (now < deadline <= now + datetime.timedelta(seconds=EXECUTE_MAX_DEADLINE_WINDOW_SEC)):
        raise LiveGateError("--deadline must be in the future and within the max window (30 min)")
    if not manifest["bucket"].startswith("pult-canary-"):
        raise LiveGateError("bucket must be a pult-canary-<runid> disposable bucket")
    return {"region": EXECUTE_LIVE_REGION, "max_object_bytes": mob, "deadline": args.deadline,
            "deadline_dt": deadline, "ack": args.ack, "max_buckets": 1}


def read_masked_credentials(roles=_CANARY_ROLES, reader=None) -> dict:
    """Read one access/secret pair per role WITHOUT echo, into process memory ONLY. Never argv, never a file,
    never shell history, never Git, never logged. `reader` is injectable for tests (default getpass.getpass)."""
    if reader is None:
        import getpass
        reader = getpass.getpass
    creds = {}
    for role in roles:
        access = reader(f"{role} access-key id (hidden): ")
        secret = reader(f"{role} secret key (hidden): ")
        creds[role] = {"access_key": access, "secret_key": secret}
    return creds


_OP_TO_S3 = {"put": ("PutObject", "PUT"), "get": ("GetObject", "GET"),
             "list": ("ListBucket", "GET"), "delete": ("DeleteObject", "DELETE")}


def _live_uri(manifest, op, prefix):
    full = f"{manifest['prefix']}{prefix}"
    key = f"{full}probe-{manifest['runid']}"
    if op == "list":
        return f"/{manifest['bucket']}", f"prefix={full}", key
    return f"/{manifest['bucket']}/{key}", "", key


# PRE-MUTATION effective-role probes (read-only ONLY). Each supplied key must BEHAVE as its role before any
# Put/Delete: an allow probe must return allow(ok); a deny probe must return a REAL access-denied. A swapped
# key order, a wrong secret, a wrong project binding, or an over-broad policy (e.g. logical-writer able to list
# pitr/) surfaces here and aborts BEFORE the first mutating op. Uses only ListBucket/GetBucketVersioning/
# GetBucketObjectLockConfiguration (all read-only).
_IDENTITY_PROBES = {
    "logical-writer": (("ListBucket", "logical/", "allow"), ("ListBucket", "pitr/", "deny")),
    "pitr-writer": (("ListBucket", "pitr/", "allow"), ("ListBucket", "logical/", "deny")),
    "restore-reader": (("ListBucket", "pitr/", "allow"), ("ListBucket", "logical/", "allow")),
    "retention-admin": (("GetBucketVersioning", None, "allow"),
                        ("GetBucketObjectLockConfiguration", None, "allow"),
                        ("ListBucket", "", "allow")),
    "app-deny": (("ListBucket", "pitr/", "deny"), ("GetBucketVersioning", None, "deny")),
}


def run_identity_check(transports, manifest, clock, probes=None) -> dict:
    """Read-only proof that every supplied key behaves as its expected role, BEFORE any mutating op. An
    expected-deny probe counts ONLY when the category is exactly access-denied; a signature-mismatch /
    invalid-access-key / authentication failure (or any non-deny) is a FAIL, never a spurious deny-proof. No
    mutating op is issued here. Secret-free: rows carry only role/verdict/category. `probes` defaults to the
    full five-role set (unchanged); the two-role object-lock mode passes its own subset."""
    if probes is None:
        probes = _IDENTITY_PROBES
    bucket, prefix = manifest["bucket"], manifest["prefix"]
    rows, failures = [], []
    for role, role_probes in probes.items():
        verdict, cause = "PASS", "ok"
        for op, sub, expect in role_probes:
            amz, ds = _amz_ds(clock())
            if op == "ListBucket":
                res = transports[role].attempt("ListBucket", f"/{bucket}", method="GET",
                                               query=f"prefix={prefix}{sub}", amz_date=amz, date_stamp=ds)
            elif op == "GetBucketVersioning":
                res = transports[role].attempt("GetBucketVersioning", f"/{bucket}", method="GET",
                                               query="versioning", amz_date=amz, date_stamp=ds)
            elif op == "GetBucketObjectLockConfiguration":
                res = transports[role].attempt("GetBucketObjectLockConfiguration", f"/{bucket}", method="GET",
                                               query="object-lock", amz_date=amz, date_stamp=ds)
            else:  # pragma: no cover - only the three read-only ops above are ever used
                res = {"allow": "unknown", "category": "unknown"}
            allow, cat = res.get("allow"), res.get("category", "unknown")
            good = (expect == "allow" and allow == "allow" and cat == "ok") or \
                   (expect == "deny" and allow == "deny" and cat == "access-denied")
            if not good:
                verdict, cause = "FAIL", (cat if cat in LIVE_ERROR_CATEGORIES else "unknown")
                break
        rows.append({"role": role, "verdict": verdict, "category": cause})
        if verdict == "FAIL":
            failures.append(role)
    return {"rows": rows, "failures": failures, "passed": not failures}


# ================= SECURITY-2D-3E1B-3C2D-V7 minimal TWO-ROLE Object-Lock live mode =================
# A strictly-scoped live mode whose ONLY purpose is the final Governance Object-Lock proof, using ONLY
# pitr-writer + retention-admin. It does NOT run (or weaken) the full five-role matrix. Its own gate env /
# typed-confirm / ACK prefix keep it separate from `live`. The proof reuses the SAME rigour as the full mode:
# a locked-delete DENY counts as Object-Lock ONLY because the SAME retention-admin is first proven able to
# delete an UNLOCKED version (IAM-delete ALLOW) — otherwise a DENY could be IAM, not the lock. Both control
# objects are created by pitr-writer (retention-admin has no PutObject). GOVERNANCE mode only (never the
# stricter immutable mode, never a retention override).
OBJECT_LOCK_LIVE_GATE_ENV = "PULT_SELECTEL_OBJECTLOCK_LIVE"
OBJECT_LOCK_LIVE_GATE_VALUE = "YES_I_UNDERSTAND_OBJECTLOCK"
OBJECT_LOCK_LIVE_ACK_PREFIX = "PULT-CANARY-OBJECTLOCK-"
_OBJECTLOCK_ROLES = ("pitr-writer", "retention-admin")
_OBJECTLOCK_IDENTITY_PROBES = {
    "pitr-writer": (("ListBucket", "pitr/", "allow"),),
    "retention-admin": (("GetBucketVersioning", None, "allow"),
                        ("GetBucketObjectLockConfiguration", None, "allow"),
                        ("ListBucket", "", "allow")),
}


def objectlock_validate(args, manifest, clock) -> dict:
    """Strict gate for object-lock-live. Raises LiveGateError BEFORE any credential/DNS/socket unless every
    one-time condition holds. Mirrors execute_validate but with its own flag/env/ack so it can never be
    triggered by the ordinary `live` path."""
    import datetime
    if not getattr(args, "execute_object_lock", False):
        raise LiveGateError("object-lock-live requires the explicit --execute-object-lock flag")
    if manifest["region"] != EXECUTE_LIVE_REGION:
        raise LiveGateError("object-lock-live is pinned to region ru-3")
    if manifest["endpoint"] != LIVE_REGION_ENDPOINTS[EXECUTE_LIVE_REGION]:
        raise LiveGateError("endpoint must be the official ru-3 S3 endpoint")
    if getattr(args, "ack", None) != OBJECT_LOCK_LIVE_ACK_PREFIX + manifest["runid"]:
        raise LiveGateError("--ack must equal PULT-CANARY-OBJECTLOCK-<runid> exactly")
    mob = getattr(args, "max_object_bytes", None)
    if not isinstance(mob, int) or not (0 < mob <= EXECUTE_MAX_OBJECT_BYTES):
        raise LiveGateError("--max-object-bytes must be an int in (0, 10 MiB]")
    if not _DEADLINE_RE.match(getattr(args, "deadline", "") or ""):
        raise LiveGateError("--deadline must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    deadline = _parse_deadline(args.deadline)
    now = clock()
    if not (now < deadline <= now + datetime.timedelta(seconds=EXECUTE_MAX_DEADLINE_WINDOW_SEC)):
        raise LiveGateError("--deadline must be in the future and within the max window (30 min)")
    if not manifest["bucket"].startswith("pult-canary-"):
        raise LiveGateError("bucket must be a pult-canary-<runid> disposable bucket")
    return {"region": EXECUTE_LIVE_REGION, "max_object_bytes": mob, "deadline": args.deadline,
            "deadline_dt": deadline, "ack": args.ack, "max_buckets": 1}


def run_object_lock_live(manifest, execmani, creds_by_role, transport_factory=None, clock=None) -> dict:
    """Minimal TWO-ROLE (pitr-writer + retention-admin) Object-Lock Governance proof. Credential-format check
    -> transports -> two-role identity -> ONE object-lock sequence (unlocked control proves IAM-delete ALLOW;
    locked control proves DENY-by-lock) -> exact cleanup. Never runs the five-role matrix. All read-only until
    the object-lock sequence; mutations never retried; controlled-residual honest."""
    if clock is None:
        raise LiveGateError("run_object_lock_live requires an injected clock (no wall-clock in module)")
    if transport_factory is None:
        transport_factory = SelectelS3Transport
    bad_creds = [r for r in _OBJECTLOCK_ROLES
                 if not _credential_format_ok((creds_by_role.get(r) or {}).get("access_key"),
                                              (creds_by_role.get(r) or {}).get("secret_key"))]
    if bad_creds:
        return {"identity": {"rows": [{"role": r, "verdict": "FAIL",
                                       "category": "invalid-credential-format"} for r in bad_creds],
                             "failures": list(bad_creds), "passed": False},
                "matrix_skipped": True,
                "object_lock": {}, "matrix_failures": ["invalid credential format (pre-network): "
                                                       + ",".join(bad_creds)],
                "cleanup": {"status": "clean", "deleted": [], "residual": [], "failures": [],
                            "manual_cleanup": {"keys": list(_OBJECTLOCK_ROLES), "policies": list(_OBJECTLOCK_ROLES),
                                               "bucket": manifest["bucket"], "project": manifest.get("project")}},
                "deadline_reached": False, "pgbackrest_closure": "NOT-ATTEMPTED-live (separate step)",
                "manual_revoke_required": {"keys": list(_OBJECTLOCK_ROLES), "policies": list(_OBJECTLOCK_ROLES)},
                "mode_name": "object-lock-live", "status": "FAILED"}
    transports = {r: transport_factory(manifest, creds_by_role[r], service="s3") for r in _OBJECTLOCK_ROLES}
    identity = run_identity_check(transports, manifest, clock, probes=_OBJECTLOCK_IDENTITY_PROBES)
    empty_manual = {"keys": list(_OBJECTLOCK_ROLES), "policies": list(_OBJECTLOCK_ROLES),
                    "bucket": manifest["bucket"], "project": manifest.get("project")}
    if not identity["passed"]:
        return {"identity": identity, "matrix_skipped": True, "object_lock": {},
                "matrix_failures": ["pre-mutation identity check failed: " + ",".join(identity["failures"])],
                "cleanup": {"status": "clean", "deleted": [], "residual": [], "failures": [],
                            "manual_cleanup": empty_manual},
                "deadline_reached": False, "pgbackrest_closure": "NOT-ATTEMPTED-live (separate step)",
                "manual_revoke_required": {"keys": list(_OBJECTLOCK_ROLES), "policies": list(_OBJECTLOCK_ROLES)},
                "mode_name": "object-lock-live", "status": "FAILED"}
    admin = transports["retention-admin"]
    writer = transports["pitr-writer"]
    b = manifest["bucket"]
    retain_until = execmani.get("deadline")
    deadline_dt = execmani.get("deadline_dt") or _parse_deadline(execmani["deadline"])
    ledger = {"objects": [], "multipart": [], "users": list(_OBJECTLOCK_ROLES), "policies": list(_OBJECTLOCK_ROLES)}
    objectlock, deadline_reached = {}, False
    try:
        if clock() >= deadline_dt:
            deadline_reached = True
        else:
            # (1) unlocked control (pitr-writer PUT) -> retention-admin DeleteObjectVersion = ALLOW (IAM delete)
            uk = f"{manifest['prefix']}pitr/unlocked-{manifest['runid']}"
            amz, ds = _amz_ds(clock())
            up = writer.attempt("PutObject", f"/{b}/{uk}", method="PUT", payload=b"u", amz_date=amz, date_stamp=ds)
            u_ver = up.get("version_id", "")
            unlocked_put_ok = up.get("allow") == "allow" and bool(u_ver)
            iam_delete_ok = False
            if unlocked_put_ok:
                ud = admin.attempt("DeleteObjectVersion", f"/{b}/{uk}", method="DELETE",
                                   query=f"versionId={u_ver}", amz_date=amz, date_stamp=ds)
                iam_delete_ok = ud.get("allow") == "allow"
            # (2) locked control (pitr-writer PUT) -> retention-admin PutObjectRetention(GOVERNANCE) -> read-back
            lk = f"{manifest['prefix']}pitr/lock-{manifest['runid']}"
            amz, ds = _amz_ds(clock())
            pr = writer.attempt("PutObject", f"/{b}/{lk}", method="PUT", payload=b"x", amz_date=amz, date_stamp=ds)
            lv = pr.get("version_id", "")
            locked_put_ok = pr.get("allow") == "allow" and bool(lv)
            retention_set = readback_ok = locked_delete_refused = False
            readback = {}
            retention_put_category = retention_get_category = "not_attempted"
            retention_parse_status = retention_compare_status = "not_attempted"
            locked_delete_attempted = False
            locked_delete_category = "not_attempted"
            locked_delete_http_status = None
            if locked_put_ok:
                ledger["objects"].append({"key": lk, "version": lv, "locked": True, "retain_until": retain_until})
                xml = _retention_xml("GOVERNANCE", retain_until)
                eh = {"content-md5": _content_md5(xml), "content-type": "application/xml"}
                rr = admin.attempt("PutObjectRetention", f"/{b}/{lk}", method="PUT",
                                   query=f"retention&versionId={lv}", payload=xml, extra_headers=eh,
                                   amz_date=amz, date_stamp=ds)
                retention_set = rr.get("allow") == "allow"
                retention_put_category = rr.get("category", "unknown")
                if retention_set:
                    gr = admin.attempt("GetObjectRetention", f"/{b}/{lk}", method="GET",
                                       query=f"retention&versionId={lv}", amz_date=amz, date_stamp=ds)
                    retention_get_category = gr.get("category", "unknown")
                    if gr.get("allow") == "allow":
                        try:
                            readback = _parse_retention_xml(gr.get("body", b""))
                            retention_parse_status = "ok"
                            mode_ok = readback.get("mode") == "GOVERNANCE"
                            try:
                                same = (_rfc3339_instant(retain_until)
                                        == _rfc3339_instant(readback.get("retain_until")))
                                retention_compare_status = "match" if same else "mismatch"
                            except LiveGateError:
                                same = False
                                retention_compare_status = "malformed"
                            readback_ok = bool(mode_ok and same)
                        except LiveGateError:
                            retention_parse_status = "malformed"
                            readback_ok = False
                    if readback_ok:
                        dv = admin.attempt("DeleteObjectVersion", f"/{b}/{lk}", method="DELETE",
                                           query=f"versionId={lv}", amz_date=amz, date_stamp=ds)
                        locked_delete_attempted = True
                        locked_delete_category = dv.get("category", "unknown")
                        locked_delete_http_status = dv.get("http_code")
                        # Object-Lock proof invariant: a refusal counts as a PROVEN Object-Lock denial ONLY when
                        # the retention-admin (already shown able to delete an UNLOCKED version) is refused with an
                        # HTTP deny AND category access-denied. A signature/auth/service/network deny never counts.
                        locked_delete_refused = (dv.get("allow") == "deny"
                                                 and locked_delete_category == "access-denied")
            elif pr.get("allow") == "unknown":
                ledger["objects"].append({"key": lk, "version": lv, "locked": True, "retain_until": retain_until,
                                          "ambiguous": True})
            objectlock = {"unlocked_put_ok": unlocked_put_ok, "iam_delete_ok_on_unlocked": iam_delete_ok,
                          "locked_put_ok": locked_put_ok, "retention_set": retention_set,
                          "readback_ok": readback_ok, "readback": readback,
                          "locked_delete_refused": locked_delete_refused, "mode": "GOVERNANCE",
                          "compliance_tested": False,
                          "retention_put_category": retention_put_category,
                          "retention_get_category": retention_get_category,
                          "retention_parse_status": retention_parse_status,
                          "retention_compare_status": retention_compare_status,
                          "locked_delete_attempted": locked_delete_attempted,
                          "locked_delete_category": locked_delete_category,
                          "locked_delete_http_status": locked_delete_http_status,
                          "proof": bool(unlocked_put_ok and iam_delete_ok and locked_put_ok and retention_set
                                        and readback_ok and locked_delete_refused)}
    finally:
        cleanup = run_cleanup(admin, manifest, ledger, clock)
    result = {"identity": identity, "matrix_skipped": True, "object_lock": objectlock, "matrix_failures": [],
              "cleanup": cleanup, "deadline_reached": deadline_reached,
              "pgbackrest_closure": "NOT-ATTEMPTED-live (separate step)",
              "manual_revoke_required": {"keys": ledger["users"], "policies": ledger["policies"]},
              "mode_name": "object-lock-live"}
    if not objectlock.get("proof"):
        result["status"] = "FAILED"
    elif cleanup["status"] == "controlled-residual":
        result["status"] = "CONTROLLED_RESIDUAL"
    elif cleanup["status"] == "clean":
        result["status"] = "ok"
    else:
        result["status"] = "FAILED"
    return result


def _objectlock_summary_lines(result) -> list:
    """CLOSED-VOCABULARY, secret-free summary for object-lock-live: two-role identity + the Object-Lock proof
    booleans + retention observability + cleanup. Never a timestamp/versionId/request-id/URL/XML/exception/cred."""
    lines = ["object-lock-live-summary v1"]
    ident = {r.get("role"): (r.get("verdict"), r.get("category"))
             for r in ((result.get("identity") or {}).get("rows") or [])}
    for role in _OBJECTLOCK_ROLES:
        verdict, cat = ident.get(role, ("NOT_ATTEMPTED", "not_attempted"))
        if verdict not in ("PASS", "FAIL", "NOT_ATTEMPTED"):
            verdict = "NOT_ATTEMPTED"
        if cat not in LIVE_ERROR_CATEGORIES and cat != "not_attempted":
            cat = "unknown"
        lines.append(f"identity {role} = {verdict} ({cat})")
    ol = result.get("object_lock") or {}

    def _b(key):
        v = ol.get(key)
        return "true" if v is True else ("false" if v is False else "not_attempted")

    for key in ("unlocked_put_ok", "iam_delete_ok_on_unlocked", "locked_put_ok", "retention_set",
                "readback_ok", "locked_delete_refused", "proof"):
        lines.append(f"object_lock {key} = {_b(key)}")
    _rput = ol.get("retention_put_category", "not_attempted")
    _rget = ol.get("retention_get_category", "not_attempted")
    _rparse = ol.get("retention_parse_status", "not_attempted")
    _rcmp = ol.get("retention_compare_status", "not_attempted")
    _ldc = ol.get("locked_delete_category", "not_attempted")
    if _rput not in LIVE_ERROR_CATEGORIES and _rput != "not_attempted":
        _rput = "unknown"
    if _rget not in LIVE_ERROR_CATEGORIES and _rget != "not_attempted":
        _rget = "unknown"
    if _rparse not in ("ok", "malformed", "not_attempted"):
        _rparse = "not_attempted"
    if _rcmp not in ("match", "mismatch", "malformed", "not_attempted"):
        _rcmp = "not_attempted"
    if _ldc not in LIVE_ERROR_CATEGORIES and _ldc != "not_attempted":
        _ldc = "unknown"
    _lds = ol.get("locked_delete_http_status")
    _lds = _lds if isinstance(_lds, int) and 100 <= _lds <= 599 else "none"
    lines.append(f"retention_put_category = {_rput}")
    lines.append(f"retention_get_category = {_rget}")
    lines.append(f"retention_parse_status = {_rparse}")
    lines.append(f"retention_compare_status = {_rcmp}")
    lines.append(f"locked_delete_attempted = {'true' if ol.get('locked_delete_attempted') else 'false'}")
    lines.append(f"locked_delete_category = {_ldc}")
    lines.append(f"locked_delete_http_status = {_lds}")
    cleanup = result.get("cleanup") or {}
    status = cleanup.get("status", "not_attempted")
    lines.append(f"cleanup_status = {status}")
    lines.append(f"controlled_residual = {'true' if status == 'controlled-residual' else 'false'}")
    manual = cleanup.get("manual_cleanup") or {}
    cats = []
    if manual.get("keys"):
        cats.append("service-keys")
    if manual.get("policies"):
        cats.append("bucket-policy")
    if manual.get("bucket"):
        cats.append("bucket")
    if manual.get("project"):
        cats.append("project")
    lines.append(f"manual_cleanup_required = {','.join(cats) if cats else 'none'}")
    lines.append(f"deadline_reached = {'true' if result.get('deadline_reached') else 'false'}")
    lines.append(f"object-lock-live status = {result.get('status', 'unknown')}")
    return lines


def object_lock_live(args, env=None) -> int:
    """Two-role Object-Lock live CLI. Ordinary invocation (no --execute-object-lock) fails closed BEFORE any
    network; only the full one-time gate reaches credentials + the single object-lock sequence."""
    env = os.environ if env is None else env
    if env.get(OBJECT_LOCK_LIVE_GATE_ENV) != OBJECT_LOCK_LIVE_GATE_VALUE:
        print("OBJECT_LOCK_DEFERRED: object-lock-live requires its own env acknowledgement", file=sys.stderr)
        return 5
    if not getattr(args, "execute_object_lock", False):
        print("OBJECT_LOCK_DEFERRED: object-lock-live requires --execute-object-lock", file=sys.stderr)
        return 5
    try:
        # object-lock-live has its OWN env gate (checked above); reuse ONLY live_validate's run-id/bucket/
        # prefix/endpoint/project/confirm structural validation, satisfying its env check internally.
        manifest = live_validate(args, {LIVE_GATE_ENV: LIVE_GATE_VALUE})
    except LiveGateError as e:
        print(f"OBJECT-LOCK GATE REFUSED: {_redact(str(e), [])}", file=sys.stderr)
        return 4
    try:
        execmani = objectlock_validate(args, manifest, _utcnow)  # fail-closed BEFORE creds/DNS/socket
    except LiveGateError as e:
        print(f"OBJECT-LOCK EXECUTE GATE REFUSED: {_redact(str(e), [])}", file=sys.stderr)
        return 4
    creds = read_masked_credentials(roles=_OBJECTLOCK_ROLES)  # exactly two masked pairs
    result = run_object_lock_live(manifest, execmani, creds, clock=_utcnow)
    for line in _objectlock_summary_lines(result):
        print(line)
    print(f"object-lock-live status={result['status']}", file=sys.stderr)
    return 0 if result["status"] in ("ok", "CONTROLLED_RESIDUAL") else 6


# ================= SECURITY-2D-3E1B-3C2D-V9 stale-safe controlled locked-delete probe =================
# A strictly-scoped, SINGLE-ROLE (retention-admin) probe for the ONE existing residual locked version left by a
# prior object-lock-live run. It creates NO new object/version/project/bucket/user/key/run-id and touches ONLY
# the exact key canary/<runid>/pitr/lock-<runid>. It re-applies a FRESH GOVERNANCE retention (the old one has
# almost certainly expired, so a bare Delete could 2xx and prove nothing), verifies the read-back, and only then
# issues EXACTLY ONE DeleteObjectVersion of that exact versionId. No mapping guessing: PASS only on a proven
# access-denied; 400/409/unknown keep proof=false and surface the numeric HTTP status for a later evidence-based
# classifier PR. Own env/flag/ack keep it separate from `live` and `object-lock-live`.
LOCKED_DELETE_PROBE_GATE_ENV = "PULT_SELECTEL_LOCKED_DELETE_PROBE"
LOCKED_DELETE_PROBE_GATE_VALUE = "YES_I_UNDERSTAND_LOCKED_DELETE_PROBE"
LOCKED_DELETE_PROBE_ACK_PREFIX = "PULT-CANARY-LOCKEDDELETE-"
_LOCKED_DELETE_PROBE_ROLES = ("retention-admin",)
# Manual-only control-plane residue categories (labels ONLY — never a value); the locked version stays until the
# fresh deadline, then is manually removed in F6.
_LOCKED_DELETE_PROBE_MANUAL = ("service-key", "bucket-policy", "bucket", "users", "project",
                               "locked-version-after-expiry")


def locked_delete_probe_validate(args, manifest, clock) -> dict:
    """Strict gate for locked-delete-probe. Raises LiveGateError BEFORE any credential/DNS/socket unless every
    one-time condition holds. Its OWN flag/ack keep it un-triggerable by `live`/`object-lock-live`. No writes and
    no new objects are ever performed, so there is no --max-object-bytes."""
    import datetime
    if not getattr(args, "execute_locked_delete_probe", False):
        raise LiveGateError("locked-delete-probe requires the explicit --execute-locked-delete-probe flag")
    if manifest["region"] != EXECUTE_LIVE_REGION:
        raise LiveGateError("locked-delete-probe is pinned to region ru-3")
    if manifest["endpoint"] != LIVE_REGION_ENDPOINTS[EXECUTE_LIVE_REGION]:
        raise LiveGateError("endpoint must be the official ru-3 S3 endpoint")
    if getattr(args, "ack", None) != LOCKED_DELETE_PROBE_ACK_PREFIX + manifest["runid"]:
        raise LiveGateError("--ack must equal PULT-CANARY-LOCKEDDELETE-<runid> exactly")
    if not _DEADLINE_RE.match(getattr(args, "deadline", "") or ""):
        raise LiveGateError("--deadline must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    deadline = _parse_deadline(args.deadline)
    now = clock()
    if not (now < deadline <= now + datetime.timedelta(seconds=EXECUTE_MAX_DEADLINE_WINDOW_SEC)):
        raise LiveGateError("--deadline must be in the future and within the max window (30 min)")
    if not manifest["bucket"].startswith("pult-canary-"):
        raise LiveGateError("bucket must be a pult-canary-<runid> disposable bucket")
    return {"region": EXECUTE_LIVE_REGION, "deadline": args.deadline, "deadline_dt": deadline, "ack": args.ack}


def run_locked_delete_probe(manifest, execmani, creds_by_role, transport_factory=None, clock=None) -> dict:
    """SINGLE-ROLE (retention-admin) stale-safe probe of the ONE existing locked version. Credential-format check
    -> ONE transport -> HeadObject(exact key) for the in-memory versionId -> fresh PutObjectRetention(GOVERNANCE)
    on THAT versionId -> GetObjectRetention read-back (mode + instant compare) -> only then EXACTLY ONE
    DeleteObjectVersion of THAT versionId. No bucket/version enumeration, no new object, no retries on mutations,
    no cleanup (nothing new was created). Fail-closed at every step; secret-free."""
    if clock is None:
        raise LiveGateError("run_locked_delete_probe requires an injected clock (no wall-clock in module)")
    if transport_factory is None:
        transport_factory = SelectelS3Transport
    manual = list(_LOCKED_DELETE_PROBE_MANUAL)
    role = _LOCKED_DELETE_PROBE_ROLES[0]
    base = {"probe_head_category": "not_attempted", "probe_version_present": False,
            "retention_put_category": "not_attempted", "retention_get_category": "not_attempted",
            "retention_parse_status": "not_attempted", "retention_compare_status": "not_attempted",
            "delete_attempted": False, "delete_allow": "not_attempted", "delete_category": "not_attempted",
            "delete_http_status": None, "proof": False, "manual_cleanup": manual, "mode_name": "locked-delete-probe"}
    cred = (creds_by_role.get(role) or {})
    if not _credential_format_ok(cred.get("access_key"), cred.get("secret_key")):
        return {**base, "probe_status": "FAILED", "reason": "invalid credential format (pre-network)"}
    transport = transport_factory(manifest, creds_by_role[role], service="s3")
    b = manifest["bucket"]
    lock_key = f"{manifest['prefix']}pitr/lock-{manifest['runid']}"
    retain_until = execmani.get("deadline")
    deadline_dt = execmani.get("deadline_dt") or _parse_deadline(execmani["deadline"])
    tel = dict(base)
    if clock() >= deadline_dt:
        return {**tel, "probe_status": "FAILED", "reason": "deadline reached before probe"}
    # A. HeadObject exact key -> versionId in memory only
    amz, ds = _amz_ds(clock())
    hv = transport.attempt("HeadObject", f"/{b}/{lock_key}", method="HEAD", amz_date=amz, date_stamp=ds)
    tel["probe_head_category"] = hv.get("category", "unknown")
    version = hv.get("version_id", "") or ""
    tel["probe_version_present"] = bool(version)
    if not version:
        return {**tel, "probe_status": "FAILED", "reason": "no versionId from HeadObject -> no mutation"}
    # B. fresh PutObjectRetention(GOVERNANCE) on the exact versionId
    amz, ds = _amz_ds(clock())
    xml = _retention_xml("GOVERNANCE", retain_until)
    eh = {"content-md5": _content_md5(xml), "content-type": "application/xml"}
    rr = transport.attempt("PutObjectRetention", f"/{b}/{lock_key}", method="PUT",
                           query=f"retention&versionId={version}", payload=xml, extra_headers=eh,
                           amz_date=amz, date_stamp=ds)
    tel["retention_put_category"] = rr.get("category", "unknown")
    if rr.get("allow") != "allow":
        return {**tel, "probe_status": "FAILED", "reason": "PutObjectRetention not allowed -> no read-back/delete"}
    # C. GetObjectRetention read-back of the same exact versionId
    amz, ds = _amz_ds(clock())
    gr = transport.attempt("GetObjectRetention", f"/{b}/{lock_key}", method="GET",
                           query=f"retention&versionId={version}", amz_date=amz, date_stamp=ds)
    tel["retention_get_category"] = gr.get("category", "unknown")
    readback_ok = False
    if gr.get("allow") == "allow":
        try:
            rb = _parse_retention_xml(gr.get("body", b""))
            tel["retention_parse_status"] = "ok"
            mode_ok = rb.get("mode") == "GOVERNANCE"
            try:
                same = _rfc3339_instant(retain_until) == _rfc3339_instant(rb.get("retain_until"))
                tel["retention_compare_status"] = "match" if same else "mismatch"
            except LiveGateError:
                same = False
                tel["retention_compare_status"] = "malformed"
            readback_ok = bool(mode_ok and same)
        except LiveGateError:
            tel["retention_parse_status"] = "malformed"
            readback_ok = False
    if not readback_ok:
        return {**tel, "probe_status": "FAILED", "reason": "read-back failed/mismatch/malformed -> no delete"}
    # D. EXACTLY ONE DeleteObjectVersion of the same exact versionId (never retried; mutations no-retry in attempt)
    amz, ds = _amz_ds(clock())
    dv = transport.attempt("DeleteObjectVersion", f"/{b}/{lock_key}", method="DELETE",
                           query=f"versionId={version}", amz_date=amz, date_stamp=ds)
    tel["delete_attempted"] = True
    tel["delete_allow"] = dv.get("allow", "unknown")
    tel["delete_category"] = dv.get("category", "unknown")
    tel["delete_http_status"] = dv.get("http_code")
    tel["proof"] = bool(dv.get("allow") == "deny" and tel["delete_category"] == "access-denied")
    if tel["proof"]:
        probe_status = "PASS"                       # Object-Lock DENY proven; locked version remains (residual)
    elif dv.get("allow") == "allow":
        probe_status = "FAILED"                     # 2xx delete -> lock NOT enforced (stale/expired) -> breach
    else:
        probe_status = "CONTROLLED_RESIDUAL"        # deny-not-access-denied / 400 / 409 / unknown: object remains,
        #                                             proof false, exact status surfaced for the classifier PR
    return {**tel, "probe_status": probe_status}


def _locked_delete_probe_summary_lines(result) -> list:
    """CLOSED-VOCABULARY, secret-free summary for locked-delete-probe. Never emits versionId / retain-until /
    raw XML / body / Code / Message / RequestId / HostId / URL / exception / credentials / PROJECT_ID / UID."""
    r = result or {}

    def _cat(v):
        return v if (v in LIVE_ERROR_CATEGORIES or v == "not_attempted") else "unknown"

    lines = ["locked-delete-probe-summary v1"]
    lines.append(f"probe_head_category = {_cat(r.get('probe_head_category', 'not_attempted'))}")
    lines.append(f"probe_version_present = {'true' if r.get('probe_version_present') else 'false'}")
    lines.append(f"retention_put_category = {_cat(r.get('retention_put_category', 'not_attempted'))}")
    lines.append(f"retention_get_category = {_cat(r.get('retention_get_category', 'not_attempted'))}")
    _rp = r.get("retention_parse_status", "not_attempted")
    lines.append(f"retention_parse_status = {_rp if _rp in ('ok', 'malformed', 'not_attempted') else 'not_attempted'}")
    _rc = r.get("retention_compare_status", "not_attempted")
    lines.append("retention_compare_status = "
                 f"{_rc if _rc in ('match', 'mismatch', 'malformed', 'not_attempted') else 'not_attempted'}")
    lines.append(f"delete_attempted = {'true' if r.get('delete_attempted') else 'false'}")
    _da = r.get("delete_allow", "not_attempted")
    lines.append(f"delete_allow = {_da if _da in ('allow', 'deny', 'unknown', 'not_attempted') else 'unknown'}")
    lines.append(f"delete_category = {_cat(r.get('delete_category', 'not_attempted'))}")
    _ds = r.get("delete_http_status")
    lines.append(f"delete_http_status = {_ds if isinstance(_ds, int) and 100 <= _ds <= 599 else 'none'}")
    _st = r.get("probe_status", "FAILED")
    lines.append(f"probe_status = {_st if _st in ('PASS', 'FAILED', 'CONTROLLED_RESIDUAL') else 'FAILED'}")
    lines.append(f"manual_cleanup_required = {','.join(r.get('manual_cleanup') or _LOCKED_DELETE_PROBE_MANUAL)}")
    return lines


def locked_delete_probe(args, env=None) -> int:
    """Single-role stale-safe locked-delete-probe CLI. Ordinary invocation fails closed BEFORE any network; only
    the full one-time gate reaches the single masked credential pair and the exact-key probe sequence."""
    env = os.environ if env is None else env
    if env.get(LOCKED_DELETE_PROBE_GATE_ENV) != LOCKED_DELETE_PROBE_GATE_VALUE:
        print("LOCKED_DELETE_PROBE_DEFERRED: requires its own env acknowledgement", file=sys.stderr)
        return 5
    if not getattr(args, "execute_locked_delete_probe", False):
        print("LOCKED_DELETE_PROBE_DEFERRED: requires --execute-locked-delete-probe", file=sys.stderr)
        return 5
    try:
        manifest = live_validate(args, {LIVE_GATE_ENV: LIVE_GATE_VALUE})  # structural run-id/bucket/endpoint/project
    except LiveGateError as e:
        print(f"LOCKED-DELETE-PROBE GATE REFUSED: {_redact(str(e), [])}", file=sys.stderr)
        return 4
    try:
        execmani = locked_delete_probe_validate(args, manifest, _utcnow)  # fail-closed BEFORE creds/DNS/socket
    except LiveGateError as e:
        print(f"LOCKED-DELETE-PROBE EXECUTE GATE REFUSED: {_redact(str(e), [])}", file=sys.stderr)
        return 4
    creds = read_masked_credentials(roles=_LOCKED_DELETE_PROBE_ROLES)  # exactly one masked pair
    result = run_locked_delete_probe(manifest, execmani, creds, clock=_utcnow)
    for line in _locked_delete_probe_summary_lines(result):
        print(line)
    print(f"locked-delete-probe status={result['probe_status']}", file=sys.stderr)
    return 0 if result["probe_status"] in ("PASS", "CONTROLLED_RESIDUAL") else 6


def run_live_execution(manifest, execmani, creds_by_role, transport_factory=None, clock=None) -> dict:
    """Gate-F orchestration over the attempt()-ONLY transport interface (factory/clock injected offline).

    (1) role allow/deny matrix; (2) Object-Lock Governance probe (create 1 synthetic object, PutObjectRetention
    with retain-until = deadline, prove a writer's DeleteObjectVersion is DENIED while locked); then EXACT
    data-plane cleanup in a finally. Deadline is enforced per operation (clock() < deadline_dt) — after it the
    remaining test operations STOP but cleanup still runs. Control-plane revocation is MANUAL/recorded.
    pgBackRest live closure is NOT attempted here (separate step). CONTROLLED_RESIDUAL/FAILED never hidden."""
    if transport_factory is None:
        transport_factory = SelectelS3Transport
    if clock is None:
        raise LiveGateError("run_live_execution requires an injected clock (no wall-clock in module)")
    deadline_dt = execmani.get("deadline_dt") or _parse_deadline(execmani["deadline"])
    # INPUT SAFETY: validate every credential's FORMAT before constructing any transport, so a non-ASCII /
    # whitespace / control-char access key (which would raise UnicodeEncodeError in the HTTP header build) or a
    # CR/LF/control secret fails closed with ZERO DNS/socket and a closed category — never a traceback/leak.
    bad_creds = [r for r in _CANARY_ROLES
                 if not _credential_format_ok((creds_by_role.get(r) or {}).get("access_key"),
                                              (creds_by_role.get(r) or {}).get("secret_key"))]
    if bad_creds:
        return {"identity": {"rows": [{"role": r, "verdict": "FAIL",
                                       "category": "invalid-credential-format"} for r in bad_creds],
                             "failures": list(bad_creds), "passed": False},
                "rows": [], "matrix_failures": ["invalid credential format (pre-network): " + ",".join(bad_creds)],
                "object_lock": {},
                "cleanup": {"status": "clean", "deleted": [], "residual": [], "failures": [],
                            "manual_cleanup": {"keys": list(_CANARY_ROLES), "policies": list(_CANARY_ROLES),
                                               "bucket": manifest["bucket"], "project": manifest.get("project")}},
                "deadline_reached": False, "pgbackrest_closure": "NOT-ATTEMPTED-live (separate step)",
                "manual_revoke_required": {"keys": list(_CANARY_ROLES), "policies": list(_CANARY_ROLES)},
                "status": "FAILED"}
    transports = {r: transport_factory(manifest, creds_by_role[r], service="s3") for r in _CANARY_ROLES}
    # PRE-MUTATION effective-role check: prove each key behaves as its role using read-only ops ONLY, before
    # any Put/Delete. On ANY mismatch -> abort with an EMPTY ledger (0 objects/versions/multipart created),
    # role matrix + Object-Lock NOT_ATTEMPTED, cleanup trivially clean, status FAILED.
    identity = run_identity_check(transports, manifest, clock)
    if not identity["passed"]:
        empty = {"objects": [], "multipart": [], "users": list(_CANARY_ROLES), "policies": list(_CANARY_ROLES)}
        cleanup = run_cleanup(transports["retention-admin"], manifest, empty, clock)  # no ledger -> no op issued
        return {"identity": identity, "rows": [],
                "matrix_failures": ["pre-mutation identity check failed: " + ",".join(identity["failures"])],
                "object_lock": {}, "cleanup": cleanup, "deadline_reached": False,
                "pgbackrest_closure": "NOT-ATTEMPTED-live (separate step)",
                "manual_revoke_required": {"keys": list(_CANARY_ROLES), "policies": list(_CANARY_ROLES)},
                "status": "FAILED"}
    ledger = {"objects": [], "multipart": [], "users": list(_CANARY_ROLES), "policies": list(_CANARY_ROLES)}
    rows, failures, objectlock = [], [], {}
    deadline_reached = False
    try:
        for role, ops in _LIVE_ROLE_MATRIX.items():
            t = transports[role if role != "app" else "app-deny"]
            for op, prefix, expect in ops:
                if clock() >= deadline_dt:
                    deadline_reached = True
                    break
                s3op, method = _OP_TO_S3[op]
                uri, query, key = _live_uri(manifest, op, prefix)
                amz, ds = _amz_ds(clock())
                res = t.attempt(s3op, uri, method=method, query=query, amz_date=amz, date_stamp=ds)
                allow = res.get("allow")
                ok = (expect == "allow" and allow == "allow") or (expect == "deny" and allow == "deny")
                rows.append({"role": role, "op": op, "prefix": prefix, "expected": expect, "actual": allow,
                             "code": res.get("http_code"), "request_id": res.get("request_id"),
                             "category": res.get("category", "unknown")})
                if op == "put" and expect == "allow" and allow == "allow":
                    ledger["objects"].append({"key": key, "version": res.get("version_id", "")})
                if not ok:
                    failures.append(f"{role}:{op}:{prefix} expected={expect} actual={allow}")
            if deadline_reached:
                break
        # Object-Lock Governance proof (only if deadline not yet reached). CONTROL OBJECTS ARE CREATED BY
        # pitr-writer (its policy grants PutObject on canary/<runid>/pitr/*); retention-admin (no PutObject —
        # least privilege) only manages retention + delete. A final DeleteObjectVersion DENY is attributable to
        # Object Lock, NOT to IAM — because the SAME retention-admin is first proven able to delete an UNLOCKED
        # version. GOVERNANCE mode only; no Bypass.
        if not deadline_reached and clock() < deadline_dt:
            admin = transports["retention-admin"]
            writer = transports["pitr-writer"]
            b = manifest["bucket"]
            retain_until = execmani.get("deadline")  # ≤ now+30min window, already validated
            # (1) unlocked control: pitr-writer creates it; retention-admin must be able to delete its version
            uk = f"{manifest['prefix']}pitr/unlocked-{manifest['runid']}"
            amz, ds = _amz_ds(clock())
            up = writer.attempt("PutObject", f"/{b}/{uk}", method="PUT", payload=b"u", amz_date=amz, date_stamp=ds)
            u_ver = up.get("version_id", "")
            unlocked_put_ok = up.get("allow") == "allow" and bool(u_ver)
            iam_delete_ok = False
            if unlocked_put_ok:
                ud = admin.attempt("DeleteObjectVersion", f"/{b}/{uk}", method="DELETE",
                                   query=f"versionId={u_ver}", amz_date=amz, date_stamp=ds)
                iam_delete_ok = ud.get("allow") == "allow"  # proven deletable (so a later lock-deny != IAM)
            # (2) locked control: pitr-writer creates it -> retention-admin PutObjectRetention(GOVERNANCE) -> read-back
            lk = f"{manifest['prefix']}pitr/lock-{manifest['runid']}"
            amz, ds = _amz_ds(clock())
            pr = writer.attempt("PutObject", f"/{b}/{lk}", method="PUT", payload=b"x", amz_date=amz, date_stamp=ds)
            lv = pr.get("version_id", "")
            locked_put_ok = pr.get("allow") == "allow" and bool(lv)
            retention_set = readback_ok = locked_delete_refused = False
            readback = {}
            retention_put_category = retention_get_category = "not_attempted"
            retention_parse_status = retention_compare_status = "not_attempted"
            locked_delete_attempted = False
            locked_delete_category = "not_attempted"
            locked_delete_http_status = None
            if locked_put_ok:
                ledger["objects"].append({"key": lk, "version": lv, "locked": True, "retain_until": retain_until})
                xml = _retention_xml("GOVERNANCE", retain_until)
                eh = {"content-md5": _content_md5(xml), "content-type": "application/xml"}
                rr = admin.attempt("PutObjectRetention", f"/{b}/{lk}", method="PUT",
                                   query=f"retention&versionId={lv}", payload=xml, extra_headers=eh,
                                   amz_date=amz, date_stamp=ds)
                retention_set = rr.get("allow") == "allow"
                retention_put_category = rr.get("category", "unknown")
                if retention_set:
                    gr = admin.attempt("GetObjectRetention", f"/{b}/{lk}", method="GET",
                                       query=f"retention&versionId={lv}", amz_date=amz, date_stamp=ds)
                    retention_get_category = gr.get("category", "unknown")
                    if gr.get("allow") == "allow":
                        try:
                            readback = _parse_retention_xml(gr.get("body", b""))
                            retention_parse_status = "ok"
                            mode_ok = readback.get("mode") == "GOVERNANCE"
                            # compare the two RetainUntilDate values as UTC INSTANTS, not raw strings, so an
                            # equivalent RFC3339 form echoed by Selectel still counts; malformed/naive -> fail.
                            try:
                                same = (_rfc3339_instant(retain_until)
                                        == _rfc3339_instant(readback.get("retain_until")))
                                retention_compare_status = "match" if same else "mismatch"
                            except LiveGateError:
                                same = False
                                retention_compare_status = "malformed"
                            readback_ok = bool(mode_ok and same)
                        except LiveGateError:
                            retention_parse_status = "malformed"
                            readback_ok = False
                    if readback_ok:
                        dv = admin.attempt("DeleteObjectVersion", f"/{b}/{lk}", method="DELETE",
                                           query=f"versionId={lv}", amz_date=amz, date_stamp=ds)
                        locked_delete_attempted = True
                        locked_delete_category = dv.get("category", "unknown")
                        locked_delete_http_status = dv.get("http_code")
                        # Object-Lock proof invariant: a refusal counts as a PROVEN Object-Lock denial ONLY when
                        # the retention-admin (already shown able to delete an UNLOCKED version) is refused with an
                        # HTTP deny AND category access-denied. A signature/auth/service/network deny never counts.
                        locked_delete_refused = (dv.get("allow") == "deny"
                                                 and locked_delete_category == "access-denied")
            elif pr.get("allow") == "unknown":
                # ambiguous locked Put — the object may exist; record it once so cleanup does not lose it
                ledger["objects"].append({"key": lk, "version": lv, "locked": True, "retain_until": retain_until,
                                          "ambiguous": True})
            objectlock = {"unlocked_put_ok": unlocked_put_ok, "iam_delete_ok_on_unlocked": iam_delete_ok,
                          "locked_put_ok": locked_put_ok, "retention_set": retention_set,
                          "readback_ok": readback_ok, "readback": readback,
                          "locked_delete_refused": locked_delete_refused, "mode": "GOVERNANCE",
                          "compliance_tested": False,
                          "retention_put_category": retention_put_category,
                          "retention_get_category": retention_get_category,
                          "retention_parse_status": retention_parse_status,
                          "retention_compare_status": retention_compare_status,
                          "locked_delete_attempted": locked_delete_attempted,
                          "locked_delete_category": locked_delete_category,
                          "locked_delete_http_status": locked_delete_http_status,
                          "proof": bool(unlocked_put_ok and iam_delete_ok and locked_put_ok and retention_set
                                        and readback_ok and locked_delete_refused)}
            if not objectlock.get("proof"):
                failures.append("object-lock: proof incomplete (need IAM-delete-on-unlocked + retention set + "
                                "read-back + locked-delete DENY by the same retention-admin)")
    finally:
        cleanup = run_cleanup(transports["retention-admin"], manifest, ledger, clock)
    result = {"identity": identity, "rows": rows, "matrix_failures": failures, "object_lock": objectlock,
              "cleanup": cleanup, "deadline_reached": deadline_reached,
              "pgbackrest_closure": "NOT-ATTEMPTED-live (separate step)",
              "manual_revoke_required": {"keys": ledger["users"], "policies": ledger["policies"]}}
    if failures or cleanup["status"] != "clean":
        result["status"] = "CONTROLLED_RESIDUAL" if cleanup["status"] == "controlled-residual" and not failures \
            else "FAILED"
    else:
        result["status"] = "ok"
    return result


# object-lock proof keys (internal name -> public secret-free label). Values are true/false/not_attempted only.
_OBJECT_LOCK_SUMMARY = (
    ("unlocked_put_ok", "unlocked_put_ok"),
    ("iam_delete_ok_on_unlocked", "unlocked_admin_delete_ok"),
    ("locked_put_ok", "locked_put_ok"),
    ("retention_set", "retention_put_ok"),
    ("readback_ok", "retention_readback_ok"),
    ("locked_delete_refused", "locked_admin_delete_denied"),
    ("proof", "object_lock_proof"),
)


def _live_summary_lines(result) -> list:
    """Build a CLOSED-VOCABULARY, secret-free summary of a live run so a human sees WHICH role/op or Object-Lock
    step failed — WITHOUT any Access/Secret, PROJECT_ID/UID/account id, versionId, request-id/host-id, URL, raw
    HTTP body/XML, canonical-request/string-to-sign, exception text/repr/args/traceback, object content, or the
    internal result-dict. Every emitted value is drawn from a fixed vocabulary; nothing else is ever printed."""
    lines = ["live-summary v1"]
    # (0) PRE-MUTATION identity check: each key proven to behave as its role before any Put/Delete.
    ident_rows = {r.get("role"): (r.get("verdict"), r.get("category"))
                  for r in ((result.get("identity") or {}).get("rows") or [])}
    for role in _CANARY_ROLES:
        verdict, cat = ident_rows.get(role, ("NOT_ATTEMPTED", "not_attempted"))
        if verdict not in ("PASS", "FAIL", "NOT_ATTEMPTED"):
            verdict = "NOT_ATTEMPTED"
        if cat not in LIVE_ERROR_CATEGORIES and cat != "not_attempted":
            cat = "unknown"
        lines.append(f"identity {role} = {verdict} ({cat})")
    # (A) role allow/deny matrix (covers logical-writer/pitr-writer/restore-reader/app incl. wrong-prefix rows);
    # a role/op present with actual==expected -> PASS, present but mismatched -> FAIL, absent (deadline/stopped)
    # -> NOT_ATTEMPTED. retention-admin behaviour is reported in the Object-Lock section below.
    # A 401/403 alone is NOT proof of an IAM policy deny. An expected-deny op counts as PASS ONLY when the
    # cause category is exactly access-denied; a signature-mismatch / invalid-access-key / authentication
    # failure (or any non-deny result) for an expected-deny op is a FAIL, never a spurious deny-proof. Each
    # line also prints the secret-free cause category so the reader sees WHY, not just PASS/FAIL.
    seen = {}
    for row in (result.get("rows") or []):
        key = (row.get("role"), row.get("op"), row.get("prefix"))
        expected, actual = row.get("expected"), row.get("actual")
        cat = row.get("category") or "unknown"
        if cat not in LIVE_ERROR_CATEGORIES:
            cat = "unknown"
        if expected == "allow":
            verdict = "PASS" if actual == "allow" else "FAIL"
        else:  # expected deny -> only a real access-denied counts
            verdict = "PASS" if (actual == "deny" and cat == "access-denied") else "FAIL"
        seen[key] = (verdict, cat)
    for role, ops in _LIVE_ROLE_MATRIX.items():
        for op, prefix, _expect in ops:
            verdict, cat = seen.get((role, op, prefix), ("NOT_ATTEMPTED", "not_attempted"))
            lines.append(f"role {role} {op} {prefix} = {verdict} ({cat})")
    # (B) Object-Lock proof — the six required booleans + the overall proof, true/false/not_attempted only
    ol = result.get("object_lock") or {}
    for internal, label in _OBJECT_LOCK_SUMMARY:
        if not ol:
            value = "not_attempted"
        else:
            b = ol.get(internal)
            value = "true" if b is True else ("false" if b is False else "not_attempted")
        lines.append(f"object_lock {label} = {value}")
    # (B2) retention read-back observability — closed vocabulary only (categories / status / true|false), so a
    # readback failure shows WHY (put/get category, XML parse, instant compare, whether the locked delete ran)
    # WITHOUT ever printing the timestamp, versionId, request-id, URL or raw XML.
    _rput = ol.get("retention_put_category", "not_attempted")
    _rget = ol.get("retention_get_category", "not_attempted")
    _rparse = ol.get("retention_parse_status", "not_attempted")
    _rcmp = ol.get("retention_compare_status", "not_attempted")
    _ldel_att = ol.get("locked_delete_attempted", False)
    _ldel_cat = ol.get("locked_delete_category", "not_attempted")
    _ldel_status = ol.get("locked_delete_http_status")
    _ldel_status = _ldel_status if isinstance(_ldel_status, int) and 100 <= _ldel_status <= 599 else "none"
    if _rput not in LIVE_ERROR_CATEGORIES and _rput != "not_attempted":
        _rput = "unknown"
    if _rget not in LIVE_ERROR_CATEGORIES and _rget != "not_attempted":
        _rget = "unknown"
    if _rparse not in ("ok", "malformed", "not_attempted"):
        _rparse = "not_attempted"
    if _rcmp not in ("match", "mismatch", "malformed", "not_attempted"):
        _rcmp = "not_attempted"
    if _ldel_cat not in LIVE_ERROR_CATEGORIES and _ldel_cat != "not_attempted":
        _ldel_cat = "unknown"
    lines.append(f"retention_put_category = {_rput}")
    lines.append(f"retention_get_category = {_rget}")
    lines.append(f"retention_parse_status = {_rparse}")
    lines.append(f"retention_compare_status = {_rcmp}")
    lines.append(f"locked_delete_attempted = {'true' if _ldel_att else 'false'}")
    lines.append(f"locked_delete_category = {_ldel_cat}")
    lines.append(f"locked_delete_http_status = {_ldel_status}")
    # (C) cleanup — status + honest residual + which control-plane CATEGORIES need manual F6 (labels, no values)
    cleanup = result.get("cleanup") or {}
    status = cleanup.get("status", "not_attempted")
    lines.append(f"cleanup_status = {status}")
    lines.append(f"controlled_residual = {'true' if status == 'controlled-residual' else 'false'}")
    manual = cleanup.get("manual_cleanup") or {}
    categories = []
    if manual.get("keys"):
        categories.append("service-keys")
    if manual.get("policies"):
        categories.append("bucket-policy")
    if manual.get("bucket"):
        categories.append("bucket")
    if manual.get("project"):
        categories.append("project")
    lines.append(f"manual_cleanup_required = {','.join(categories) if categories else 'none'}")
    lines.append(f"deadline_reached = {'true' if result.get('deadline_reached') else 'false'}")
    lines.append(f"execute-live status = {result.get('status', 'unknown')}")
    return lines


def live(args, env=None) -> int:
    """Live CLI. Ordinary `live` (no --execute-live) fails closed BEFORE any network. Only --execute-live with
    the full one-time confirmation reaches real execution (Gate F, isolated machine)."""
    env = os.environ if env is None else env
    try:
        manifest = live_validate(args, env)
    except LiveGateError as e:
        print(f"LIVE GATE REFUSED: {_redact(str(e), [])}", file=sys.stderr)
        return 4
    if not getattr(args, "execute_live", False):
        # ordinary live still stops here — real execution needs the explicit --execute-live gate below
        print(SELECTEL_EXECUTION_DEFERRED, file=sys.stderr)
        return 5
    try:
        execmani = execute_validate(args, manifest, _utcnow)  # fail-closed BEFORE creds/DNS/socket
    except LiveGateError as e:
        print(f"EXECUTE GATE REFUSED: {_redact(str(e), [])}", file=sys.stderr)
        return 4
    # Only here — after the full one-time gate — are credentials read and the network touched (Gate F only).
    creds = read_masked_credentials()
    result = run_live_execution(manifest, execmani, creds, clock=_utcnow)
    for line in _live_summary_lines(result):  # secret-free, closed-vocabulary diagnosis of WHAT failed
        print(line)
    print(f"execute-live status={result['status']}", file=sys.stderr)
    return 0 if result["status"] == "ok" else 6


def main() -> int:
    ap = argparse.ArgumentParser(description="Selectel canary tooling (offline/MinIO; live gated + dormant)")
    ap.add_argument("mode", choices=["validate-policies", "plan", "minio-compat", "live", "object-lock-live",
                                     "locked-delete-probe"])
    # Non-secret live parameters only. Credentials are NEVER accepted on argv (env/file-descriptor only).
    ap.add_argument("--project-id", dest="project_id")
    ap.add_argument("--region")
    ap.add_argument("--endpoint")
    ap.add_argument("--bucket")
    ap.add_argument("--run-id", dest="run_id")
    ap.add_argument("--confirm")
    # execute-live (Gate F) — non-secret confirmation params only; credentials are read masked, never argv
    ap.add_argument("--execute-live", dest="execute_live", action="store_true")
    # object-lock-live (V7, two-role) — separate explicit flag; credentials still masked, never argv
    ap.add_argument("--execute-object-lock", dest="execute_object_lock", action="store_true")
    # locked-delete-probe (V9, single-role stale-safe) — separate explicit flag; credentials masked, never argv
    ap.add_argument("--execute-locked-delete-probe", dest="execute_locked_delete_probe", action="store_true")
    ap.add_argument("--ack")
    ap.add_argument("--max-object-bytes", dest="max_object_bytes", type=int)
    ap.add_argument("--deadline")
    args = ap.parse_args()
    if args.mode == "live":
        return live(args)
    if args.mode == "object-lock-live":
        return object_lock_live(args)
    if args.mode == "locked-delete-probe":
        return locked_delete_probe(args)
    if args.mode == "validate-policies":
        return validate_policies()
    if args.mode == "plan":
        return plan()
    if args.mode == "minio-compat":
        return minio_compat()
    return 2


if __name__ == "__main__":
    sys.exit(main())
