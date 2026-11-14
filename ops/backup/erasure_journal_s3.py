"""Single-object Selectel adapter; explicit credentials, no default chain or CLI.

Uses the existing reviewed SigV4 signer, not canary execution/orchestration. Only
GET and conditional PUT are exposed. Real IAM/network provisioning is NOT here.
"""
from datetime import datetime, timezone
import hashlib
import re

from ops.canary.canary import (
    LIVE_REGION_ENDPOINTS, SelectelS3Transport, _credential_format_ok,
)
from ops.backup.erasure_journal import (
    MAX_BYTES, Conflict, JournalError, Missing, Stored, WriteUnknown,
)


class S3JournalStore:
    def __init__(self, *, endpoint, region, bucket, key, reader, writer, _test_transport=None):
        import httpx
        # Offline fixtures only. Never accept a live client/transport that can
        # weaken TLS, add proxies, redirects, authentication or hidden retries.
        if _test_transport is not None and type(_test_transport) is not httpx.MockTransport:
            raise JournalError("only a socket-free mock transport may be injected")
        if (LIVE_REGION_ENDPOINTS.get(region) != endpoint
                or not isinstance(bucket, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket)
                or not isinstance(key, str)
                or not re.fullmatch(r"pult/erasure-test/[A-Za-z0-9_-]{1,64}/journal\.json", key)):
            raise JournalError("unapproved journal location")
        for credential in (reader, writer):
            if (type(credential) is not dict or set(credential) != {"access_key", "secret_key"}
                    or not _credential_format_ok(credential["access_key"], credential["secret_key"])):
                raise JournalError("invalid explicit credentials")
            try:
                credential["secret_key"].encode("utf-8")
            except UnicodeError:
                raise JournalError("invalid explicit credentials") from None
        if reader["access_key"] == writer["access_key"]:
            raise JournalError("separate reader and writer keys required")
        manifest = {"endpoint": endpoint, "region": region}
        self._reader = SelectelS3Transport(manifest, reader)
        self._writer = SelectelS3Transport(manifest, writer)
        self._url = endpoint + "/" + bucket + "/" + key
        self._uri = "/" + bucket + "/" + key
        self._test_transport = _test_transport
        self._client = None

    def __repr__(self):
        return "<S3JournalStore private bounded journal>"

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    def _request(self, method, body=b"", headers=None):
        # Keep scope bounded even if this private method is accidentally misused.
        if method not in ("GET", "PUT") or len(body) > MAX_BYTES:
            raise JournalError("unsupported journal request")
        import httpx
        if self._client is None:
            transport = self._test_transport if self._test_transport is not None else httpx.HTTPTransport(
                verify=True, trust_env=False, retries=0)
            self._client = httpx.Client(verify=True, follow_redirects=False, trust_env=False,
                                       timeout=httpx.Timeout(15, connect=5), transport=transport, auth=None)
        signer = self._reader if method == "GET" else self._writer
        now = datetime.now(timezone.utc)
        try:
            signed = signer._sign(method, "GetObject" if method == "GET" else "PutObject",
                                  self._uri, "", hashlib.sha256(body).hexdigest(),
                                  now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d"), headers)
        except UnicodeError:
            raise JournalError("invalid signing input") from None
        # No retry, no redirects, bounded decoded bytes; no raw error body is consumed/logged.
        try:
            with self._client.stream(method, self._url, headers=signed, content=body,
                                     follow_redirects=False, auth=None) as response:
                code = response.status_code
                if method == "PUT":
                    if code == 412:
                        raise Conflict("conditional write conflict")
                    if code != 200:
                        raise WriteUnknown("write outcome unknown")
                    return None
                if code == 404:
                    raise Missing("journal missing; initialization is never automatic")
                if code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    raise JournalError("journal read failed")
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise JournalError("journal response exceeds bound")
                    data.extend(chunk)
                return Stored(bytes(data), response.headers.get("etag", ""))
        except (httpx.HTTPError, OSError, UnicodeError):
            if method == "PUT":
                raise WriteUnknown("write outcome unknown") from None
            raise JournalError("journal read failed") from None

    def read(self):
        return self._request("GET")

    def write(self, body, *, etag):
        if type(body) is not bytes or not 0 < len(body) <= MAX_BYTES:
            raise JournalError("invalid write size")
        if etag is not None:
            Stored(b"", etag)  # exact strong-validator grammar; no wildcard/unquoted bypass
        headers = {"content-type": "application/json", "if-none-match": "*"} if etag is None else {
            "content-type": "application/json", "if-match": etag}
        return self._request("PUT", body, headers)
