#!/usr/bin/env python3
"""SECURITY-2D-3E1B-3C2C2-B-DIAG — read-only post-run diagnostic for the completed Selectel canary.

DORMANT. The single Gate-F live run ended status=FAILED / exit 6 and its in-memory detail (role matrix,
Object-Lock booleans, cleanup ledger) is gone — nothing was ever written to a file or log. This tool
inspects the CURRENT state of the disposable canary bucket using ONLY read-only S3 operations, so a human
can see which synthetic objects still exist and whether the lock object carries GOVERNANCE retention.

It NEVER writes, removes, mutates retention, re-runs the canary, or claims to reconstruct the past role
matrix. Current state is not proof of the past FAILED cause; an empty bucket does not explain the failure.

Hard rules baked in:
  - exactly five read-only S3 ops (GetBucketVersioning, GetBucketObjectLockConfiguration, ListBucket with
    the exact prefix, HeadObject on the four exact synthetic keys, GetObjectRetention on the lock key);
  - bucket / run-id / prefix / region / endpoint are hard-pinned constants — no argv or env can widen scope;
  - credentials only via masked getpass (retention-admin required; restore-reader optional cross-check);
    never argv, env, file, stdout/stderr or log; PROJECT_ID / UID / account-id neither needed nor accepted;
  - full gate validation (env-ack + typed confirm + ack + deadline in (now, now+30min]) runs BEFORE any
    getpass, transport construction, DNS or socket; ordinary invocation fails closed pre-network; real
    execution needs the explicit --execute-diagnose flag;
  - output is a fixed secret-free allowlist: never an Access/Secret, version id, request id, UID/PROJECT_ID,
    HTTP body, full URI, raw transport result, or a stack trace carrying request data;
  - the frozen ops/canary/canary.py read transport is reused unchanged — canary.py is NOT modified.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# NOTE: httpx is NEVER imported here. The reused transport imports it lazily only when it actually issues a
# request (real execution). Ordinary/gate-refused invocations therefore touch no network dependency at all.

_CANARY_PY = Path(__file__).resolve().parent / "canary.py"

# --- hard-pinned scope (the already-completed run) --------------------------------------------------
DIAG_RUN_ID = "eced74af45e3"
DIAG_BUCKET = "pult-canary-eced74af45e3"
DIAG_REGION = "ru-3"
DIAG_ENDPOINT = "https://s3.ru-3.storage.selcloud.ru"
DIAG_PREFIX = "canary/eced74af45e3/"
DIAG_ENV = "PULT_SELECTEL_CANARY_DIAGNOSE"
DIAG_ENV_VALUE = "YES_READ_ONLY_DIAGNOSE"
DIAG_ACK_PREFIX = "PULT-CANARY-DIAGNOSE-"
DIAG_ACK = DIAG_ACK_PREFIX + DIAG_RUN_ID
DIAG_MAX_DEADLINE_WINDOW_SEC = 1800  # deadline must be in (now, now+30min]

# the four exact synthetic keys the original live orchestration could have created
KEY_LOGICAL_PROBE = DIAG_PREFIX + "logical/probe-" + DIAG_RUN_ID
KEY_PITR_PROBE = DIAG_PREFIX + "pitr/probe-" + DIAG_RUN_ID
KEY_UNLOCKED = DIAG_PREFIX + "pitr/unlocked-" + DIAG_RUN_ID
KEY_LOCKED = DIAG_PREFIX + "pitr/lock-" + DIAG_RUN_ID
_EXACT_KEYS = (KEY_LOGICAL_PROBE, KEY_PITR_PROBE, KEY_UNLOCKED, KEY_LOCKED)

# the ONLY S3 operations this diagnostic may ever perform (frozen read-only allowlist)
DIAG_READ_ONLY_OPS = frozenset({
    "GetBucketVersioning", "GetBucketObjectLockConfiguration",
    "ListBucket", "HeadObject", "GetObjectRetention",
})

_DEADLINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# secret-free error categories — the ONLY strings a *_read_status or diagnostic_error_summary may take.
# Derived exclusively from: transport `allow`, numeric HTTP status class, an allowlisted XML <Code>, or a
# fixed local exception-type category. NEVER from a raw body/URI/header/request-id/version-id/message.
DIAG_ERROR_CATEGORIES = (
    "ok", "not-found", "invalid-access-key", "signature-mismatch", "access-denied",
    "authentication-failed", "timeout", "tls-error", "network-error", "service-error",
    "malformed-response", "unknown",
)
# allowlisted S3 error <Code> -> category. Nothing else from the body is ever read or emitted.
_XML_CODE_CATEGORY = {
    "InvalidAccessKeyId": "invalid-access-key",
    "SignatureDoesNotMatch": "signature-mismatch",
    "AuthorizationHeaderMalformed": "signature-mismatch",
    "AccessDenied": "access-denied",
    "InvalidToken": "authentication-failed",
    "ExpiredToken": "authentication-failed",
}
_NOT_READ = "not-read"  # benign sentinel: an op intentionally not attempted (never an error, never counted)

# the exact, fixed set of fields printed — no other output is ever emitted
_OUTPUT_FIELDS = (
    "versioning", "object_lock",
    "logical_probe_exists", "pitr_probe_exists", "unlocked_control_exists", "locked_control_exists",
    "lock_retention_mode", "lock_retain_until_utc",
    "versioning_read_status", "object_lock_read_status", "list_prefix_read_status",
    "logical_probe_read_status", "pitr_probe_read_status", "unlocked_control_read_status",
    "locked_control_read_status", "lock_retention_read_status",
    "diagnostic_error_summary", "diagnostic_status",
)


class DiagGateError(Exception):
    """Raised when the diagnostic gate refuses. Message must never carry a secret or identifier."""


def _load_canary_runtime():
    """Load the frozen ops/canary/canary.py as a module WITHOUT modifying it (reuse its read transport)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("canary_runtime_for_diag", str(_CANARY_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------- gate (fail-closed, pure, no network / no credentials) --------------------
def diag_validate(args, env, clock) -> dict:
    """Validate every precondition BEFORE any getpass/transport/DNS/socket. Pure; raises DiagGateError.

    Scope comes from the hard-pinned constants, not from argv: a mismatching argument is refused, and even a
    matching argument cannot widen the scope (the returned manifest is built from the constants)."""
    import datetime
    if env.get(DIAG_ENV) != DIAG_ENV_VALUE:
        raise DiagGateError("env acknowledgement missing or wrong")
    if (getattr(args, "run_id", None) or "") != DIAG_RUN_ID:
        raise DiagGateError("run-id must equal the frozen diagnostic run id")
    if (getattr(args, "region", None) or "") != DIAG_REGION:
        raise DiagGateError("region must be ru-3")
    if (getattr(args, "endpoint", None) or "") != DIAG_ENDPOINT:
        raise DiagGateError("endpoint must be the official ru-3 endpoint")
    if not DIAG_ENDPOINT.startswith("https://"):
        raise DiagGateError("endpoint must be HTTPS")
    if (getattr(args, "bucket", None) or "") != DIAG_BUCKET:
        raise DiagGateError("bucket must equal the disposable canary bucket")
    if (getattr(args, "ack", None) or "") != DIAG_ACK:
        raise DiagGateError("ack must equal the diagnostic acknowledgement")
    expected_confirm = f"diagnose/{DIAG_BUCKET}/{DIAG_REGION}/{DIAG_ENDPOINT}/{DIAG_RUN_ID}"
    if (getattr(args, "confirm", None) or "") != expected_confirm:
        raise DiagGateError("typed confirmation must equal diagnose/bucket/region/endpoint/runid exactly")
    deadline_s = getattr(args, "deadline", None) or ""
    if not _DEADLINE_RE.match(deadline_s):
        raise DiagGateError("deadline must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    deadline = datetime.datetime.strptime(deadline_s, "%Y-%m-%dT%H:%M:%SZ")
    now = clock()
    if not (now < deadline <= now + datetime.timedelta(seconds=DIAG_MAX_DEADLINE_WINDOW_SEC)):
        raise DiagGateError("deadline must be in the future and within the max window (30 min)")
    return {"bucket": DIAG_BUCKET, "region": DIAG_REGION, "endpoint": DIAG_ENDPOINT,
            "prefix": DIAG_PREFIX, "runid": DIAG_RUN_ID, "deadline_dt": deadline}


def read_masked_credentials(with_restore_reader=False, reader=None) -> dict:
    """Read the retention-admin pair (and optionally the restore-reader pair) WITHOUT echo, into process
    memory ONLY. Never argv, never env, never a file, never shell history, never logged. `reader` is
    injectable for tests (default getpass.getpass)."""
    if reader is None:
        import getpass
        reader = getpass.getpass
    creds = {}
    a = reader("retention-admin access-key id (hidden): ")
    s = reader("retention-admin secret key (hidden): ")
    creds["retention-admin"] = {"access_key": a, "secret_key": s}
    if with_restore_reader:
        a2 = reader("restore-reader access-key id (hidden): ")
        s2 = reader("restore-reader secret key (hidden): ")
        creds["restore-reader"] = {"access_key": a2, "secret_key": s2}
    return creds


# ------------------------- secret-free parsers (state only, never identifiers) ----------------------
def _xml_text(body: bytes, tag: str):
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(body or b"")
    except Exception:
        return None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            return (el.text or "").strip()
    return None


def _xml_error_code_category(body: bytes):
    """Read ONLY the <Code> tag and map via the allowlist. Never returns Message/Resource/RequestId/HostId/
    StringToSign/CanonicalRequest or any raw text. Malformed XML -> 'malformed-response'; a <Code> not in the
    allowlist (or no <Code>) -> None so the caller falls back to the numeric status class."""
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
    return _XML_CODE_CATEGORY.get(code)  # None if the Code value is not allowlisted


def _classify_result(result) -> str:
    """Map a transport result dict to a secret-free category from ONLY: allow, numeric HTTP status class, and
    an allowlisted XML <Code>. Never reads body text beyond the <Code> tag; never emits an identifier."""
    if not isinstance(result, dict):
        return "unknown"
    if result.get("allow") == "allow":
        return "ok"
    code = result.get("http_code")
    if code == 404:
        return "not-found"
    xml_cat = _xml_error_code_category(result.get("body", b""))
    if xml_cat:  # allowlisted code OR 'malformed-response'
        return xml_cat
    if code == 401:
        return "authentication-failed"
    if code == 403:
        return "access-denied"
    if isinstance(code, int) and 500 <= code <= 599:
        return "service-error"
    return "unknown"


def _exc_category(exc) -> str:
    """Classify a caught transport exception by TYPE NAME ONLY (its MRO), never its message/args, so no URL,
    credential, or identifier can leak. Unknown shapes fail safe to 'unknown'."""
    joined = " ".join(c.__name__.lower() for c in type(exc).__mro__)
    if "timeout" in joined:
        return "timeout"
    if "ssl" in joined or "certificate" in joined or "tls" in joined:
        return "tls-error"
    if any(k in joined for k in ("connect", "network", "socket", "proxy", "resolution", "nameresolution")):
        return "network-error"
    return "unknown"


def _summary_from(read_status: dict) -> str:
    errs = sorted({c for c in read_status.values() if c not in ("ok", _NOT_READ)})
    if not errs:
        return "none"
    return errs[0] if len(errs) == 1 else "mixed"


def _list_keys(result):
    """Parse a ListBucket response into (status, keys, truncated). status in {ok, unavailable, malformed}.
    Reads ONLY <Key> and <IsTruncated>; retains ONLY keys that are one of the four frozen exact keys (an
    unexpected key name is never kept, never printed); never returns raw XML/body."""
    if not isinstance(result, dict) or result.get("allow") != "allow":
        return "unavailable", set(), False
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(result.get("body", b"") or b"")
    except Exception:
        return "malformed", set(), False
    keys, truncated = set(), False
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "Key":
            k = (el.text or "").strip()
            if k in _EXACT_KEYS:  # ONLY the four frozen keys are ever retained
                keys.add(k)
        elif tag == "IsTruncated":
            truncated = (el.text or "").strip().lower() == "true"
    return "ok", keys, truncated


def _exists_from(result) -> str:
    if not isinstance(result, dict):
        return "unknown"
    allow = result.get("allow")
    code = result.get("http_code")
    if allow == "allow":
        return "yes"
    if allow == "unknown" and code == 404:
        return "no"
    return "unknown"


def _versioning_from(result) -> str:
    if not isinstance(result, dict) or result.get("allow") != "allow":
        return "unknown"
    status = _xml_text(result.get("body", b""), "Status")
    if status == "Enabled":
        return "enabled"
    # an un-versioned/suspended bucket returns empty or Suspended
    return "disabled"


def _object_lock_from(result) -> str:
    if not isinstance(result, dict):
        return "unknown"
    if result.get("allow") == "allow":
        enabled = _xml_text(result.get("body", b""), "ObjectLockEnabled")
        return "enabled" if enabled == "Enabled" else "disabled"
    # a bucket without object-lock config responds 404 ObjectLockConfigurationNotFoundError
    if result.get("allow") == "unknown" and result.get("http_code") == 404:
        return "disabled"
    return "unknown"


def _retention_from(result, canary):
    if not isinstance(result, dict) or result.get("allow") != "allow":
        return "unknown", "unknown"
    try:
        parsed = canary._parse_retention_xml(result.get("body", b""))
    except canary.LiveGateError:
        return "unknown", "unknown"
    mode = "GOVERNANCE" if parsed.get("mode") == "GOVERNANCE" else "none"
    until = parsed.get("retain_until") or "unknown"
    return mode, until


def _status_from(report: dict) -> str:
    core = ["versioning", "object_lock", "logical_probe_exists", "pitr_probe_exists",
            "unlocked_control_exists", "locked_control_exists"]
    if report.get("locked_control_exists") == "yes":
        core += ["lock_retention_mode", "lock_retain_until_utc"]
    values = [report.get(k, "unknown") for k in core]
    unknowns = sum(1 for v in values if v == "unknown")
    if unknowns == 0:
        return "PASS"
    if unknowns == len(values):
        return "FAILED"
    return "PARTIAL"


# ------------------------- orchestration (read-only; attempt()-only transport) ----------------------
def run_diagnostic(manifest, creds, canary, transport_factory=None, clock=None) -> dict:
    """Perform ONLY the five allowed read-only ops against the exact bucket/prefix/keys and build a
    secret-free report. Never writes, never mutates. `transport_factory`/`clock` are injected offline."""
    if clock is None:
        raise DiagGateError("run_diagnostic requires an injected clock (no wall-clock here)")
    if transport_factory is None:
        transport_factory = canary.SelectelS3Transport
    admin = transport_factory(manifest, creds["retention-admin"], service="s3")
    reader = transport_factory(manifest, creds["restore-reader"], service="s3") \
        if "restore-reader" in creds else None
    bucket = manifest["bucket"]

    def call(transport, op, uri, query="", method="GET"):
        """Perform ONE allowlisted read-only op and attach a secret-free category. Any transport exception
        (timeout/TLS/network/...) is caught and mapped to a fixed category — never re-raised, never logged."""
        if op not in DIAG_READ_ONLY_OPS:  # defence in depth — only literal read ops ever reach here
            raise DiagGateError("operation not in the read-only diagnostic allowlist")
        amz, ds = canary._amz_ds(clock())
        try:
            res = transport.attempt(op, uri, method=method, query=query, amz_date=amz, date_stamp=ds)
        except DiagGateError:
            raise
        except BaseException as exc:  # noqa: BLE001 — classify by type only; message never read
            return {"allow": "unknown", "http_code": None, "body": b"", "category": _exc_category(exc)}
        res = dict(res)
        res["category"] = _classify_result(res)
        return res

    # HeadObject on a missing key is a SUCCESSFUL read whose answer is "absent" -> not an error for read_status
    def _head_status(cat):
        return "ok" if cat in ("ok", "not-found") else cat

    report = {k: "unknown" for k in _OUTPUT_FIELDS}
    rstat = {}

    # (1) bucket-level configuration reads
    v = call(admin, "GetBucketVersioning", f"/{bucket}", query="versioning")
    report["versioning"] = _versioning_from(v)
    rstat["versioning_read_status"] = v["category"]
    ol = call(admin, "GetBucketObjectLockConfiguration", f"/{bucket}", query="object-lock")
    report["object_lock"] = _object_lock_from(ol)
    rstat["object_lock_read_status"] = ol["category"]

    # (2) prefix-scoped enumeration (exact prefix only — proves scope, never lists another prefix).
    # After the 3C2D SigV4 fix ListBucket is the PRIMARY existence signal (retention-admin's policy grants it);
    # HeadObject may be AccessDenied on real Selectel (Head maps to GetObject), so a denied Head must NOT turn
    # a trusted List result into unknown. Only the four frozen keys are ever retained from the listing.
    lst = call(admin, "ListBucket", f"/{bucket}", query=f"prefix={manifest['prefix']}")
    rstat["list_prefix_read_status"] = lst["category"]
    list_status, listed_keys, list_truncated = _list_keys(lst)

    # (3) HeadObject on each of the four exact keys (secondary signal / cross-check)
    heads = {}
    head_exists = {}
    head_cat = {}
    for key in _EXACT_KEYS:
        heads[key] = call(admin, "HeadObject", f"/{bucket}/{key}", method="HEAD")
        head_exists[key] = _exists_from(heads[key])
        head_cat[key] = heads[key]["category"]

    # optional independent cross-check on the two pitr keys via restore-reader
    if reader is not None:
        for key in (KEY_PITR_PROBE, KEY_LOCKED):
            rr = call(reader, "HeadObject", f"/{bucket}/{key}", method="HEAD")
            rx = _exists_from(rr)
            if head_exists[key] == "unknown":
                head_exists[key] = rx
            elif rx not in ("unknown", head_exists[key]):
                head_exists[key] = "unknown"  # two successful Heads disagree -> fail-closed
            if _head_status(rr["category"]) != "ok":
                head_cat[key] = rr["category"]

    def _resolve(key):
        """Return (exists, read_status). ListBucket (when it succeeded and is not truncated) is authoritative;
        a denied/unknown Head does not override it. Conflict between a trusted List and a successful Head, or a
        truncated listing that did not include the key, is fail-closed to unknown (never a false 'no')."""
        he = head_exists[key]
        if list_status == "ok" and not list_truncated:
            le = "yes" if key in listed_keys else "no"
            if he in ("yes", "no") and he != le:
                return "unknown", "unknown"          # List vs successful Head conflict
            return le, "ok"                          # trusted List; denied Head is not fatal
        if list_status == "ok" and list_truncated:
            if key in listed_keys:
                return "yes", "ok"
            return "unknown", "unknown"              # truncated + not seen -> no bounded pagination -> unknown
        return he, _head_status(head_cat[key])       # List unavailable/malformed -> Head only

    exists = {}
    for key, ex_field, st_field in (
            (KEY_LOGICAL_PROBE, "logical_probe_exists", "logical_probe_read_status"),
            (KEY_PITR_PROBE, "pitr_probe_exists", "pitr_probe_read_status"),
            (KEY_UNLOCKED, "unlocked_control_exists", "unlocked_control_read_status"),
            (KEY_LOCKED, "locked_control_exists", "locked_control_read_status")):
        exists[key], rstat[st_field] = _resolve(key)
        report[ex_field] = exists[key]

    # (4) retention only on the lock key, only when it exists
    if exists[KEY_LOCKED] == "yes":
        # a version id, if any, is used ONLY in memory to target the exact version; it is never printed
        version_id = heads[KEY_LOCKED].get("version_id", "") if isinstance(heads[KEY_LOCKED], dict) else ""
        query = ("retention&versionId=" + version_id) if version_id else "retention"
        rr = call(admin, "GetObjectRetention", f"/{bucket}/{KEY_LOCKED}", query=query)
        mode, until = _retention_from(rr, canary)
        report["lock_retention_mode"] = mode
        report["lock_retain_until_utc"] = until
        rstat["lock_retention_read_status"] = rr["category"]
    elif exists[KEY_LOCKED] == "no":
        report["lock_retention_mode"] = "none"
        report["lock_retain_until_utc"] = "none"
        rstat["lock_retention_read_status"] = _NOT_READ
    else:
        rstat["lock_retention_read_status"] = _NOT_READ

    for field, cat in rstat.items():
        report[field] = cat
    report["diagnostic_error_summary"] = _summary_from(rstat)
    report["diagnostic_status"] = _status_from(report)
    return report


# ------------------------- strict secret-free output ------------------------------------------------
def print_report(report, out=None) -> None:
    """Emit ONLY the fixed field allowlist. Any missing field is an error; nothing else is ever printed."""
    out = sys.stdout if out is None else out
    for field in _OUTPUT_FIELDS:
        if field not in report:
            raise DiagGateError("internal: missing output field")
        out.write(f"{field}: {report[field]}\n")


def _all_unknown_failed() -> dict:
    report = {k: "unknown" for k in _OUTPUT_FIELDS}
    report["diagnostic_status"] = "FAILED"
    return report


def _error_category(exc) -> str:
    """Fixed enum label — never the raw exception text (which could carry a URL/body)."""
    return "gate-refused" if isinstance(exc, DiagGateError) else "read-or-transport-error"


# ------------------------- CLI --------------------------------------------------------------------
def diagnose(args, env=None, canary=None, transport_factory=None, clock=None, reader=None) -> int:
    """Read-only diagnostic CLI. Ordinary invocation fails closed BEFORE any network. Only
    --execute-diagnose with the full one-time confirmation reaches the read transport."""
    env = os.environ if env is None else env
    canary = _load_canary_runtime() if canary is None else canary
    the_clock = clock or canary._utcnow
    try:
        manifest = diag_validate(args, env, the_clock)
    except DiagGateError as e:
        sys.stderr.write(f"DIAG GATE REFUSED: {e}\n")
        return 4
    if not getattr(args, "execute_diagnose", False):
        sys.stderr.write("DIAG_DEFERRED: read-only diagnostic requires the explicit --execute-diagnose flag\n")
        return 5
    # only here — after the full gate — are credentials read and the read transport touched
    creds = read_masked_credentials(
        with_restore_reader=getattr(args, "with_restore_reader", False), reader=reader)
    try:
        report = run_diagnostic(manifest, creds, canary, transport_factory=transport_factory, clock=the_clock)
    except Exception as exc:  # never leak: fixed category + all-unknown FAILED report
        sys.stderr.write(f"DIAG ERROR: {_error_category(exc)}\n")
        print_report(_all_unknown_failed())
        return 6
    print_report(report)
    return 0 if report["diagnostic_status"] in ("PASS", "PARTIAL") else 6


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Selectel canary READ-ONLY post-run diagnostic (dormant; no writes, no re-run)")
    ap.add_argument("mode", choices=["diagnose"])
    # non-secret confirmation params only. Credentials are NEVER accepted on argv (masked getpass only).
    ap.add_argument("--run-id", dest="run_id")
    ap.add_argument("--region")
    ap.add_argument("--endpoint")
    ap.add_argument("--bucket")
    ap.add_argument("--ack")
    ap.add_argument("--confirm")
    ap.add_argument("--deadline")
    ap.add_argument("--execute-diagnose", dest="execute_diagnose", action="store_true")
    ap.add_argument("--with-restore-reader", dest="with_restore_reader", action="store_true")
    args = ap.parse_args(argv)
    if args.mode == "diagnose":
        return diagnose(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
