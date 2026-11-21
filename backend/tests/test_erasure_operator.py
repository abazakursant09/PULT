"""Protocol negatives; real kernel UID checks have a separate mandatory gate."""
import asyncio
from dataclasses import FrozenInstanceError, asdict
import io
from pathlib import Path
import struct
import sys
import time
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backup import erasure_operator as gate
from ops.backup.erasure_journal import canonical

ACCOUNT = "3faf54de-0b0d-43b9-8cfa-ecc306aa6d35"
DRAFT = "bb909f03-e92e-4345-af81-20f7fbdc1757"
NONCE = "008052a3-12d2-4b0a-9628-c0215901cb70"
EPOCH = "2026-10-01T12:00:00.000000Z"
NOW = "2026-11-21T12:00:00.000000Z"


def observation(**overrides):
    fields = dict(nonce=NONCE, account_id=ACCOUNT, draft_id=DRAFT,
                  closure_epoch=EPOCH, policy=gate.POLICY, reviewed_at=NOW)
    fields.update(overrides)
    return gate.DraftObservation(**fields)


class Wire:
    def __init__(self, body=b""):
        self.input, self.sent, self.reads = io.BytesIO(body), bytearray(), 0

    def recv(self, size):
        self.reads += 1
        return self.input.read(min(size, 3))  # Actual fragmentation, including length prefix.

    def sendall(self, body):
        self.sent.extend(body)

    def settimeout(self, value):
        assert 0 < value <= gate.SESSION_SECONDS + 1


def framed(value):
    body = canonical(value)
    return struct.pack("!I", len(body)) + body


def request(**overrides):
    value = dict(protocol=gate.PROTOCOL, op="review", nonce=NONCE, account_id=ACCOUNT, draft_id=DRAFT)
    value.update(overrides)
    return value


def test_observation_is_frozen_nonadmitting_and_repr_does_not_disclose_ids():
    value = observation()
    assert value.status == gate.STATUS
    assert all(getattr(value, key) is False for key in gate.FLAGS)
    assert not any(secret in repr(value) for secret in (NONCE, ACCOUNT, DRAFT, EPOCH))
    with pytest.raises(FrozenInstanceError):
        value.journal_dispatch_admitted = True
    with pytest.raises(TypeError):
        observation(identity_enrollment_admitted=True)


@pytest.mark.parametrize("field,bad", [
    ("account_id", ACCOUNT.upper()), ("account_id", True), ("account_id", "../secret"),
    ("draft_id", ACCOUNT), ("nonce", DRAFT), ("nonce", str(uuid4()).replace("-", "")),
    ("closure_epoch", "2026-10-01T12:00:00Z"), ("closure_epoch", "2026-10-01T12:00:00.000000+00:00"),
    ("reviewed_at", "2026-09-01T12:00:00.000000Z"), ("policy", "unknown"),
])
def test_observation_rejects_invalid_canonical_identity_policy_or_clock(field, bad):
    with pytest.raises(gate.ReviewError):
        observation(**{field: bad})


def test_fragmented_canonical_frame_round_trip():
    wire = Wire(framed(request()))
    assert gate._receive(wire, time.monotonic() + 1) == request()
    assert wire.reads > 4
    gate._send(wire, request(), time.monotonic() + 1)
    assert bytes(wire.sent) == framed(request())


@pytest.mark.parametrize("body", [b"", b"{}", b'{"a":1,"a":1}', b'{ "a":1}', b"[]", b"null", b"\xff"])
def test_noncanonical_duplicate_or_interrupted_frame_refused(body):
    value = struct.pack("!I", len(body)) + body
    if body == b"{}":  # Empty dict is canonical but fails the protocol schema.
        with pytest.raises(gate.ReviewError):
            gate._check(gate._receive(Wire(value), time.monotonic() + 1), {"nonce"})
    else:
        with pytest.raises(gate.ReviewError):
            gate._receive(Wire(value), time.monotonic() + 1)


@pytest.mark.parametrize("size", [0, gate.MAX_FRAME + 1, 2**32 - 1])
def test_bound_checked_before_payload_read(size):
    wire = Wire(struct.pack("!I", size) + b"secret payload must not be read")
    with pytest.raises(gate.ReviewError, match="^operator_frame_bound$"):
        gate._receive(wire, time.monotonic() + 1)
    assert wire.input.tell() == 4


def run_session(monkeypatch, value, reviewer=None, *, refuse_peer=False):
    calls = []
    def peer(connection, uid):
        assert not connection.sent and connection.reads == 0
        calls.append("peer")
        if refuse_peer:
            raise gate.ipc.IsolationError("secret peer details")
    async def review(account, draft, *, nonce):
        calls.append((account, draft, nonce))
        return observation()
    monkeypatch.setattr(gate.ipc, "_peer", peer)
    monkeypatch.setattr(gate, "uuid4", lambda: NONCE)
    server = object.__new__(gate.OperatorReviewServer)  # Unit boundary only, not UID proof.
    server.operator_uid, server.reviewer = 46002, reviewer or review
    wire = Wire(framed(value))
    try:
        asyncio.run(server._session(wire))
    except gate.ReviewError as error:
        return wire, calls, error
    return wire, calls, None


def test_accepted_session_observes_once_and_never_admits_actions(monkeypatch):
    wire, calls, error = run_session(monkeypatch, request())
    assert error is None and calls == ["peer", (ACCOUNT, DRAFT, NONCE)]
    answers = Wire(bytes(wire.sent))
    assert gate._receive(answers, time.monotonic() + 1) == dict(protocol=gate.PROTOCOL, nonce=NONCE)
    assert gate._receive(answers, time.monotonic() + 1) == dict(protocol=gate.PROTOCOL, **asdict(observation()))
    assert answers.input.read() == b""


def test_peer_denial_happens_before_any_read_disclosure_or_reviewer_call(monkeypatch):
    wire, calls, error = run_session(monkeypatch, request(), refuse_peer=True)
    assert str(error) == "operator_review_refused"
    assert wire.sent == b"" and wire.reads == 0 and calls == ["peer"]


@pytest.mark.parametrize("overrides", [
    {"uid": 46002}, {"approved": True}, {"database_url": "secret"}, {"closure_epoch": EPOCH},
    {"policy": gate.POLICY}, {"op": "enroll"}, {"op": "purge"}, {"op": "restore"},
    {"op": "dispatch"}, {"nonce": str(uuid4())}, {"account_id": DRAFT}, {"protocol": "old"},
])
def test_forged_authority_extra_fields_wrong_op_or_replay_never_reach_reviewer(monkeypatch, overrides):
    wire, calls, error = run_session(monkeypatch, request(**overrides))
    assert str(error) == "operator_review_refused" and calls == ["peer"]
    assert bytes(wire.sent) == framed(dict(protocol=gate.PROTOCOL, nonce=NONCE))


@pytest.mark.parametrize("mode", ["wrong_nonce", "wrong_draft", "dict", "exception", "timeout"])
def test_reviewer_failure_mismatch_or_timeout_never_sends_success(monkeypatch, mode):
    async def review(*args, **kwargs):
        if mode == "exception":
            raise RuntimeError("smtp://credential@secret/address")
        if mode == "timeout":
            await asyncio.sleep(.05)
        if mode == "dict":
            return asdict(observation())
        return observation(**({"nonce": str(uuid4())} if mode == "wrong_nonce" else
                              {"draft_id": str(uuid4())} if mode == "wrong_draft" else {}))
    if mode == "timeout":
        monkeypatch.setattr(gate, "SESSION_SECONDS", .01)
    wire, calls, error = run_session(monkeypatch, request(), reviewer=review)
    assert str(error) == "operator_review_refused" and calls == ["peer"]
    assert bytes(wire.sent) == framed(dict(protocol=gate.PROTOCOL, nonce=NONCE))


def test_expired_frame_refused_without_reading():
    wire = Wire(framed(request()))
    with pytest.raises(gate.ipc.IsolationError, match="session expired"):
        gate._receive(wire, time.monotonic() - 1)
    assert wire.reads == 0


@pytest.mark.parametrize("mode", ["valid", "wrong_nonce", "wrong_draft", "admitted", "extra", "status", "clock"])
def test_client_authenticates_before_ids_and_refuses_mismatched_or_admitting_response(monkeypatch, mode):
    value = dict(protocol=gate.PROTOCOL, **asdict(observation()))
    if mode == "wrong_nonce":
        value["nonce"] = str(uuid4())
    elif mode == "wrong_draft":
        value["draft_id"] = str(uuid4())
    elif mode == "admitted":
        value["identity_enrollment_admitted"] = True
    elif mode == "extra":
        value["approved"] = True
    elif mode == "status":
        value["status"] = "ADMITTED"
    elif mode == "clock":
        value["reviewed_at"] = "yesterday"
    class Connection(Wire):
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def connect(self, path):
            assert path == "/synthetic/review.sock"
    wire = Connection(framed(dict(protocol=gate.PROTOCOL, nonce=NONCE)) + framed(value))
    authenticated = []
    def peer(connection, uid):
        assert connection is wire and uid == 46001
        assert wire.sent == b"" and wire.reads == 0
        authenticated.append(True)
    monkeypatch.setattr(gate.ipc, "_socket_directory", lambda path, uid: 46002)
    monkeypatch.setattr(gate.ipc, "_socket_file", lambda path, uid, gid: None)
    monkeypatch.setattr(gate.ipc, "_peer", peer)
    # The protocol-only fake never opens a socket. Some Windows Python builds
    # lack this Linux family constant; real authentication is proved on Linux.
    monkeypatch.setattr(gate.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(gate.socket, "socket", lambda *args: wire)
    client = object.__new__(gate.OperatorReviewClient)
    client.path, client.reviewer_uid = "/synthetic/review.sock", 46001
    if mode == "valid":
        assert client.review(ACCOUNT, DRAFT) == observation()
    else:
        with pytest.raises(gate.ReviewError, match="^operator_review_refused$"):
            client.review(ACCOUNT, DRAFT)
    assert authenticated == [True]
    assert bytes(wire.sent) == framed(request())
