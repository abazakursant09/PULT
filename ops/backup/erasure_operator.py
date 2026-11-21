"""Dormant, one-shot Linux operator channel for a read-only DRAFT review.

The host pins the peer UID and supplies the reviewer; request fields are never
authority or connection configuration. No registry, journal, purge or restore
method is reachable here. An observation is not a reusable authorization token,
a live fence, proof against a malicious database administrator, or a deployment.
"""
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
import socket
import struct
import time
from uuid import UUID, uuid4

from ops.backup import erasure_custodian as ipc
from ops.backup.erasure_journal import JournalError, canonical, _unique_pairs

PROTOCOL = "pult-erasure-operator-review-v1"
POLICY = "normal-close-30d-v1"
STATUS = "COMMITTED_DRAFT_REVIEW_ONLY"
MAX_FRAME = 1024
SESSION_SECONDS = 10
FLAGS = ("identity_enrollment_admitted", "journal_dispatch_admitted", "purge_or_restore_admitted")


class ReviewError(Exception):
    """Fixed, secret-free reason; never carries the request or DB exception."""


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value or UUID(value).version != 4:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ReviewError("canonical_review_uuid_required") from None
    return value


def _epoch(value):
    try:
        if type(value) is not str or len(value) != 27:
            raise ValueError
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError, OverflowError):
        raise ReviewError("canonical_review_epoch_required") from None


@dataclass(frozen=True)
class DraftObservation:
    nonce: str = field(repr=False)
    account_id: str = field(repr=False)
    draft_id: str = field(repr=False)
    closure_epoch: str = field(repr=False)
    policy: str = field(repr=False)
    reviewed_at: str = field(repr=False)
    status: str = field(default=STATUS, init=False)
    identity_enrollment_admitted: bool = field(default=False, init=False)
    journal_dispatch_admitted: bool = field(default=False, init=False)
    purge_or_restore_admitted: bool = field(default=False, init=False)

    def __post_init__(self):
        for value in (self.nonce, self.account_id, self.draft_id):
            _uuid(value)
        if self.account_id == self.draft_id or self.nonce in (self.account_id, self.draft_id):
            raise ReviewError("review_identity_alias_refused")
        start, observed = _epoch(self.closure_epoch), _epoch(self.reviewed_at)
        if self.policy != POLICY or observed < start:
            raise ReviewError("review_policy_or_clock_refused")


def _receive(connection, deadline):
    # Reuse the existing canonical/duplicate-key rules with a smaller hard bound
    # checked BEFORE allocating or reading the declared payload.
    def take(size):
        result = bytearray()
        while len(result) < size:
            ipc._timeout(connection, deadline)
            chunk = connection.recv(size - len(result))
            if not chunk:
                raise ReviewError("operator_session_interrupted")
            result.extend(chunk)
        return bytes(result)
    size, = struct.unpack("!I", take(4))
    if not 0 < size <= MAX_FRAME:
        raise ReviewError("operator_frame_bound")
    body = take(size)
    try:
        value = json.loads(body, object_pairs_hook=_unique_pairs)
        if type(value) is not dict or canonical(value) != body:
            raise ValueError
        return value
    except (JournalError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ReviewError("operator_frame_invalid") from None


def _send(connection, value, deadline):
    if len(canonical(value)) > MAX_FRAME:
        raise ReviewError("operator_frame_bound")
    ipc._send(connection, value, deadline)


def _check(value, keys):
    if type(value) is not dict or set(value) != set(keys) | {"protocol"} or value["protocol"] != PROTOCOL:
        raise ReviewError("operator_schema_refused")


class OperatorReviewServer:
    """One listener, one accepted connection, one configured read-only review.

    A dedicated host process must own the private socket directory. It supplies
    a trusted async reviewer, never one selected by a socket field. No unlink,
    auto-retry, daemon, installation or enrollment is provided.
    """
    def __init__(self, socket_path, *, operator_uid, reviewer):
        try:
            self.uid, self.operator_uid = ipc._principal(), ipc._uid(operator_uid)
            self.path = ipc._path(socket_path)
            if self.uid == self.operator_uid or not callable(reviewer):
                raise ReviewError("dedicated_operator_and_reviewer_required")
            if ipc._socket_directory(self.path, self.uid) != os.getegid():
                raise ReviewError("dedicated_operator_socket_group_required")
        except (ipc.IsolationError, OSError, UnicodeError):
            raise ReviewError("operator_host_refused") from None
        self.reviewer = reviewer

    async def _session(self, connection):
        deadline = time.monotonic() + SESSION_SECONDS
        try:
            ipc._peer(connection, self.operator_uid)  # Before reading or disclosing anything.
            nonce = str(uuid4())
            _send(connection, dict(protocol=PROTOCOL, nonce=nonce), deadline)
            request = _receive(connection, deadline)
            _check(request, {"op", "nonce", "account_id", "draft_id"})
            if request["op"] != "review" or request["nonce"] != nonce:
                raise ReviewError("operator_session_binding_refused")
            account, draft = _uuid(request["account_id"]), _uuid(request["draft_id"])
            if len({nonce, account, draft}) != 3:
                raise ReviewError("review_identity_alias_refused")
            left = deadline - time.monotonic()
            if left <= 0:
                raise ReviewError("operator_session_expired")
            observed = await asyncio.wait_for(self.reviewer(account, draft, nonce=nonce), left)
            if (type(observed) is not DraftObservation
                    or (observed.nonce, observed.account_id, observed.draft_id) != (nonce, account, draft)
                    or observed.status != STATUS or any(getattr(observed, key) is not False for key in FLAGS)):
                raise ReviewError("operator_observation_refused")
            _send(connection, dict(protocol=PROTOCOL, **asdict(observed)), deadline)
        except Exception:
            # No response (including a misleading PASS) after any ambiguous failure.
            raise ReviewError("operator_review_refused") from None

    async def serve_once(self):
        previous = os.umask(0o077)
        try:
            ipc._socket_directory(self.path, self.uid)
            if os.path.lexists(self.path):
                raise ReviewError("operator_socket_exists_requires_review")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(self.path))
                os.chmod(self.path, 0o660)
                ipc._socket_file(self.path, self.uid, os.getegid())
                listener.listen(1)
                listener.settimeout(SESSION_SECONDS)
                connection, _ = listener.accept()
                with connection:
                    await self._session(connection)
        except (ipc.IsolationError, OSError, UnicodeError):
            raise ReviewError("operator_listener_refused") from None
        finally:
            os.umask(previous)
        # Keep the socket for explicit review; never remove or replace a path.


class OperatorReviewClient:
    """No secrets or IDs are sent until kernel authentication of the server."""
    def __init__(self, socket_path, *, reviewer_uid):
        try:
            self.uid, self.reviewer_uid = ipc._principal(), ipc._uid(reviewer_uid)
            self.path = ipc._path(socket_path)
            if self.uid == self.reviewer_uid:
                raise ReviewError("separate_reviewer_required")
        except (ipc.IsolationError, OSError, UnicodeError):
            raise ReviewError("operator_client_refused") from None

    def review(self, account_id, draft_id):
        _uuid(account_id)
        _uuid(draft_id)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                gid = ipc._socket_directory(self.path, self.reviewer_uid)
                ipc._socket_file(self.path, self.reviewer_uid, gid)
                deadline = time.monotonic() + SESSION_SECONDS
                ipc._timeout(connection, deadline)
                connection.connect(str(self.path))
                ipc._peer(connection, self.reviewer_uid)  # Before IDs leave this process.
                challenge = _receive(connection, deadline)
                _check(challenge, {"nonce"})
                nonce = _uuid(challenge["nonce"])
                if len({nonce, account_id, draft_id}) != 3:
                    raise ReviewError("review_identity_alias_refused")
                _send(connection, dict(protocol=PROTOCOL, op="review", nonce=nonce,
                                       account_id=account_id, draft_id=draft_id), deadline)
                value = _receive(connection, deadline)
                _check(value, {"nonce", "account_id", "draft_id", "closure_epoch", "policy", "reviewed_at", "status", *FLAGS})
                if ((value["nonce"], value["account_id"], value["draft_id"]) != (nonce, account_id, draft_id)
                        or value["status"] != STATUS or any(value[key] is not False for key in FLAGS)):
                    raise ReviewError("operator_response_refused")
                return DraftObservation(**{key: value[key] for key in (
                    "nonce", "account_id", "draft_id", "closure_epoch", "policy", "reviewed_at")})
        except Exception:
            raise ReviewError("operator_review_refused") from None
