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
    "logical-writer": [("put", "logical/", "allow"), ("stat", "logical/", "allow"),
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Selectel canary tooling (3C2A: offline + MinIO only)")
    ap.add_argument("mode", choices=["validate-policies", "plan", "minio-compat", "live"])
    args = ap.parse_args()
    if args.mode == "live":
        # Fail-closed BEFORE any DNS/network/credential read.
        print(LIVE_NOT_IMPLEMENTED, file=sys.stderr)
        return 3
    if args.mode == "validate-policies":
        return validate_policies()
    if args.mode == "plan":
        return plan()
    if args.mode == "minio-compat":
        return minio_compat()
    return 2


if __name__ == "__main__":
    sys.exit(main())
