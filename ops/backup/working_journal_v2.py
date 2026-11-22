"""Separate working-v2 storage contract; socket-free, no launch/admission path.

Reuses the accepted immutable event codec and SigV4 signer. This module cannot
provision a bucket, open a live transport, bootstrap an anchor, obtain a default
credential or authorize enrollment/purge/restore. Namespace syntax is not proof
of private IAM, independent custody, freshness or trusted generation provenance.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re

import httpx

from ops.backup.custodian_v2.codec.event_codec import (
    Binding, CodecError, Event, Head, MAX_RECORD_BYTES, decode_record,
    encode_candidate, genesis, object_key,
)
from ops.backup.erasure_journal_s3 import _validate_credential
from ops.canary.canary import SelectelS3Transport


class NamespaceError(Exception):
    """Fixed diagnostic, without caller values or provider responses."""


class StoreError(Exception):
    """Storage contract error, never an admission receipt."""


class Missing(StoreError):
    pass


class Conflict(StoreError):
    pass


class WriteUnknown(StoreError):
    pass


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class WorkingNamespace:
    endpoint: str
    region: str
    bucket: str
    scope: str
    generation: str

    def __post_init__(self):
        if (type(self.endpoint) is not str or type(self.region) is not str
                or self.endpoint != "https://s3.ru-3.storage.selcloud.ru"
                or self.region != "ru-3"
                or type(self.bucket) is not str
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket)
                or not self.bucket.startswith("pult-custodian-working-")
                or type(self.scope) is not str
                or not re.fullmatch(r"pult-work-[A-Za-z0-9_-]{1,54}", self.scope)):
            raise NamespaceError("explicit separate working namespace required")
        try:
            Binding(self.scope, self.generation)
        except CodecError:
            raise NamespaceError("canonical working generation required") from None

    def __repr__(self):
        return "<WorkingNamespace storage contract; not admitted>"

    @property
    def binding(self):
        return Binding(self.scope, self.generation)

    @property
    def location(self):
        prefix = object_key(self.binding, 1).rsplit("/", 1)[0] + "/"
        return self.endpoint + "/" + self.bucket + "/" + prefix

    def key(self, sequence):
        try:
            return object_key(self.binding, sequence)
        except CodecError:
            raise NamespaceError("invalid working event sequence") from None


class _WorkingEventCapability:
    def __init__(self, *, namespace, credential, transport):
        # No default/live branch, custom clients, transport subclasses or modes.
        if type(namespace) is not WorkingNamespace:
            raise StoreError("exact working namespace required")
        if type(transport) is not httpx.MockTransport:
            raise StoreError("socket-free MockTransport required")
        try:
            _validate_credential(credential)
        except Exception:
            raise StoreError("explicit working credential required") from None
        self._namespace = namespace
        self._signer = SelectelS3Transport(
            {"endpoint": namespace.endpoint, "region": namespace.region}, dict(credential))
        self._client = httpx.Client(
            transport=transport, verify=True, trust_env=False, follow_redirects=False,
            auth=None, timeout=httpx.Timeout(15, connect=5))
        self._closed = False

    @property
    def namespace(self):
        return self._namespace

    @property
    def location(self):
        return self._namespace.location

    def __repr__(self):
        return "<WorkingEventCapability socket-free; not admitted>"

    def close(self):
        self._closed = True
        try:
            self._client.close()
        except Exception:
            raise StoreError("working event close failed") from None

    def _stream(self, sequence, body=b""):
        if self._closed:
            raise StoreError("working event capability closed")
        if (self._method not in ("GET", "PUT") or type(body) is not bytes
                or len(body) > MAX_RECORD_BYTES or (self._method == "GET" and body)):
            raise StoreError("unsupported working event capability")
        uri = "/" + self._namespace.bucket + "/" + self._namespace.key(sequence)
        extra = {"accept-encoding": "identity"}
        if self._method == "PUT":
            extra.update({"content-type": "application/json", "if-none-match": "*"})
        now = datetime.now(timezone.utc)
        try:
            signed = self._signer._sign(
                self._method, "GetObject" if self._method == "GET" else "PutObject",
                uri, "", hashlib.sha256(body).hexdigest(),
                now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d"), extra)
        except Exception:
            raise StoreError("working event signing failed") from None
        return self._client.stream(
            self._method, self._namespace.endpoint + uri, content=body,
            headers=signed, follow_redirects=False, auth=None)


class WorkingEventWriter(_WorkingEventCapability):
    """PUT-only artificial contract. HTTP200 is not a durable acknowledgement."""
    _method = "PUT"

    def create(self, previous, event):
        if type(previous) is not Head or type(event) is not Event:
            raise StoreError("typed working candidate required")
        try:
            body, target = encode_candidate(self._namespace.binding, previous, event)
        except CodecError:
            raise StoreError("invalid working event candidate") from None
        try:
            with self._stream(target.sequence, body) as response:
                code = response.status_code
        except Exception:
            raise WriteUnknown("working event write outcome unknown") from None
        if code == 412:
            raise Conflict("working event key already exists")
        if code != 200:
            raise WriteUnknown("working event write outcome unknown")
        return "PUT_HTTP200_UNCONFIRMED"


class WorkingEventReader(_WorkingEventCapability):
    """GET-only artificial contract; verifies exact next bounded event bytes."""
    _method = "GET"

    def read_next(self, previous):
        if type(previous) is not Head:
            raise StoreError("typed working previous head required")
        if previous.sequence == 0 and previous != genesis(self._namespace.binding):
            raise StoreError("wrong working event genesis")
        self._namespace.key(previous.sequence + 1)
        try:
            with self._stream(previous.sequence + 1) as response:
                code = response.status_code
                valid = code == 200 and response.headers.get("content-encoding", "identity") == "identity"
                body = bytearray()
                if valid:
                    for chunk in response.iter_bytes(chunk_size=MAX_RECORD_BYTES + 1):
                        if len(body) + len(chunk) > MAX_RECORD_BYTES:
                            raise StoreError("bounded working event required")
                        body.extend(chunk)
        except Exception:
            raise StoreError("working event read failed") from None
        if code == 404:
            raise Missing("working event object unavailable")
        if not valid:
            raise StoreError("working event read failed")
        try:
            return decode_record(bytes(body), self._namespace.binding, previous)
        except CodecError:
            raise StoreError("working event chain verification failed") from None
