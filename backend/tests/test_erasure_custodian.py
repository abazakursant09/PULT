"""Offline wire/validation guards, not proof of distinct-UID process isolation.

Framing uses only an in-memory connection. Real Linux principal, filesystem and
peer-credential enforcement belong to the separate process integration tests.
"""
from pathlib import Path
import socket
import struct
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backup import erasure_custodian as custodian
from ops.backup.erasure_journal import MAX_EVENTS, Head, JournalError, canonical


SCOPE = "synthetic"
GENERATION = "1e534b17-05e7-4529-8ffa-c32162c67ad5"
OTHER_GENERATION = "82047569-5c91-4b10-87c4-3a8b121e1891"
LOCATION = "synthetic://test/journal"


class FakeConnection:
    """Short reads, EOF and captured writes without a socket or file descriptor."""

    def __init__(self, incoming=b"", *, chunk_size=8192):
        self.remaining = incoming
        self.chunk_size = chunk_size
        self.requests = []
        self.timeouts = []
        self.sent = []

    def settimeout(self, seconds):
        self.timeouts.append(seconds)

    def recv(self, size):
        assert 0 < size <= 8192
        self.requests.append(size)
        size = min(size, self.chunk_size)
        result, self.remaining = self.remaining[:size], self.remaining[size:]
        return result

    def sendall(self, body):
        assert type(body) is bytes
        self.sent.append(body)


@pytest.fixture(autouse=True)
def no_socket_io(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("offline custodian unit test attempted socket IO")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "bind", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def _deadline():
    return time.monotonic() + 60


def _packet(body):
    return struct.pack("!I", len(body)) + body


def _binding():
    return custodian._Binding(scope=SCOPE, generation=GENERATION, location=LOCATION)


def _sized_value(size):
    value = {"payload": "x" * (size - len(canonical({"payload": ""})))}
    assert len(canonical(value)) == size
    return value


def test_send_emits_one_length_prefixed_canonical_frame():
    connection = FakeConnection()
    value = {"z": "\u00e9", "a": {"status": "PREPARED"}}
    custodian._send(connection, value, _deadline())
    assert connection.sent == [_packet(canonical(value))]
    assert len(connection.timeouts) == 1
    assert 0 < connection.timeouts[0] <= 60
    assert connection.requests == []


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 8192])
def test_receive_reassembles_short_header_and_body_reads(chunk_size):
    value = {"op": "claim", "nested": {"value": "\u00e9"}}
    connection = FakeConnection(_packet(canonical(value)), chunk_size=chunk_size)
    assert custodian._receive(connection, _deadline()) == value
    assert connection.remaining == b"" and connection.sent == []
    assert len(connection.timeouts) == len(connection.requests)
    assert all(0 < seconds <= 60 for seconds in connection.timeouts)
    assert connection.timeouts == sorted(connection.timeouts, reverse=True)


def test_receive_consumes_one_frame_without_swallowing_the_next():
    first, second = {"op": "claim"}, {"op": "confirm"}
    next_packet = _packet(canonical(second))
    connection = FakeConnection(_packet(canonical(first)) + next_packet)
    assert custodian._receive(connection, _deadline()) == first
    assert connection.remaining == next_packet
    assert custodian._receive(connection, _deadline()) == second
    assert connection.remaining == b""


def test_exact_frame_bound_is_accepted_in_both_directions():
    value = _sized_value(custodian.MAX_FRAME)
    connection = FakeConnection()
    custodian._send(connection, value, _deadline())
    connection.remaining = connection.sent[0]
    assert struct.unpack("!I", connection.remaining[:4]) == (custodian.MAX_FRAME,)
    assert custodian._receive(connection, _deadline()) == value
    assert max(connection.requests) == 8192
    assert connection.remaining == b""


def test_send_over_bound_fails_before_timeout_or_write():
    connection = FakeConnection()
    with pytest.raises(custodian.IsolationError, match="frame exceeds bound"):
        custodian._send(connection, _sized_value(custodian.MAX_FRAME + 1), _deadline())
    assert connection.sent == [] and connection.timeouts == []


@pytest.mark.parametrize("size", [0, custodian.MAX_FRAME + 1, 2**32 - 1])
def test_invalid_length_is_rejected_before_body_is_read(size):
    connection = FakeConnection(struct.pack("!I", size) + b"unread body")
    with pytest.raises(custodian.IsolationError, match="frame exceeds bound"):
        custodian._receive(connection, _deadline())
    assert connection.requests == [4]
    assert connection.remaining == b"unread body"


@pytest.mark.parametrize("cutoff", range(len(_packet(canonical({"op": "claim"})))))
def test_every_truncated_header_or_body_fails_closed(cutoff):
    connection = FakeConnection(_packet(canonical({"op": "claim"}))[:cutoff], chunk_size=2)
    with pytest.raises(custodian.IsolationError, match="session interrupted"):
        custodian._receive(connection, _deadline())
    assert connection.sent == []


@pytest.mark.parametrize("body", [
    pytest.param(b"{", id="malformed-json"),
    pytest.param(b'{"value":}\n', id="missing-value"),
    pytest.param(b'{}\ntrailing', id="trailing-garbage"),
    pytest.param(b'{}\n{}\n', id="second-json-in-one-frame"),
    pytest.param(b'{}\n\0', id="trailing-nul"),
    pytest.param(b'{}', id="missing-newline"),
    pytest.param(b' {}\n', id="leading-space"),
    pytest.param(b'{}\n\n', id="extra-newline"),
    pytest.param(b'{}\r\n', id="crlf"),
    pytest.param(b'{"value": 1}\n', id="noncanonical-spacing"),
    pytest.param(b'{"z":0,"a":1}\n', id="noncanonical-key-order"),
    pytest.param(b'{"value":1e0}\n', id="noncanonical-number"),
    pytest.param(b'{"value":"first","value":"fixture-private-marker"}\n', id="duplicate-key"),
    pytest.param(b'{"nested":{"value":1,"value":2}}\n', id="nested-duplicate-key"),
    pytest.param(b'{"value":NaN}\n', id="nan"),
    pytest.param(b'{"value":Infinity}\n', id="infinity"),
    pytest.param(b'{"value":-Infinity}\n', id="negative-infinity"),
    pytest.param(b'{"value":"\xff"}\n', id="invalid-utf8"),
    pytest.param(b'{"value":"\xc3\xa9"}\n', id="unescaped-unicode"),
    pytest.param(b'[]\n', id="array-not-object"),
    pytest.param(b'null\n', id="null-not-object"),
    pytest.param(b'true\n', id="boolean-not-object"),
    pytest.param(b'1\n', id="number-not-object"),
    pytest.param(b'"text"\n', id="string-not-object"),
    pytest.param(b'{"nested":' + b'[' * 1100 + b'0' + b']' * 1099 + b'}\n', id="deeply-nested-malformed"),
])
def test_malformed_or_noncanonical_frame_never_returns_a_message(body):
    with pytest.raises(JournalError) as error:
        custodian._receive(FakeConnection(_packet(body)), _deadline())
    assert "fixture-private-marker" not in str(error.value)


@pytest.mark.parametrize("operation", [custodian._receive, custodian._send])
def test_expired_deadline_prevents_read_or_write(operation):
    connection = FakeConnection(_packet(canonical({"op": "claim"})))
    with pytest.raises(custodian.IsolationError, match="session expired"):
        if operation is custodian._send:
            operation(connection, {"op": "claim"}, time.monotonic() - 1)
        else:
            operation(connection, time.monotonic() - 1)
    assert connection.requests == connection.sent == connection.timeouts == []


def test_binding_wrap_preserves_all_configured_fields_and_checks_exact_schema():
    binding = _binding()
    message = binding.wrap(op="current")
    assert message == dict(protocol=custodian.PROTOCOL, scope=SCOPE,
                           generation=GENERATION, location=LOCATION, op="current")
    binding.check(message, {"op"})
    message["scope"] = "other"
    assert binding.wrap(op="current")["scope"] == SCOPE
    response = binding.wrap(status="OBSERVED_ONLY", head={"sequence": 0, "digest": "a" * 64})
    binding.check(response, {"status", "head"})


@pytest.mark.parametrize("field", ["protocol", "scope", "generation", "location", "op"])
def test_binding_rejects_each_missing_field(field):
    binding = _binding()
    message = binding.wrap(op="current")
    del message[field]
    with pytest.raises(custodian.IsolationError, match="binding or schema mismatch"):
        binding.check(message, {"op"})


@pytest.mark.parametrize("field,value", [
    ("protocol", "pult-erasure-custodian-unix-v2"),
    ("scope", "other"),
    ("generation", OTHER_GENERATION),
    ("location", "synthetic://other/journal"),
])
def test_binding_rejects_cross_protocol_scope_generation_or_location(field, value):
    binding = _binding()
    message = binding.wrap(op="claim")
    message[field] = value
    with pytest.raises(custodian.IsolationError, match="binding or schema mismatch"):
        binding.check(message, {"op"})


@pytest.mark.parametrize("field", ["protocol", "scope", "generation", "location"])
@pytest.mark.parametrize("value", [None, False, 1, [], {}])
def test_binding_rejects_wrong_types_for_bound_fields(field, value):
    binding = _binding()
    message = binding.wrap(op="claim")
    message[field] = value
    with pytest.raises(custodian.IsolationError, match="binding or schema mismatch"):
        binding.check(message, {"op"})


def test_binding_rejects_extra_fields_and_wrong_operation_schema():
    binding = _binding()
    for message, keys in ((binding.wrap(op="claim", unexpected=True), {"op"}),
                          (binding.wrap(op="claim"), {"status", "head"})):
        with pytest.raises(custodian.IsolationError, match="binding or schema mismatch"):
            binding.check(message, keys)


@pytest.mark.parametrize("generation", [None, 1, "", GENERATION.upper(), GENERATION.replace("-", ""),
                                       "00000000-0000-0000-0000-000000000000"])
def test_binding_requires_a_canonical_uuid4_generation(generation):
    with pytest.raises(JournalError):
        custodian._Binding(scope=SCOPE, generation=generation, location=LOCATION)


@pytest.mark.parametrize("sequence", [0, 1, MAX_EVENTS])
def test_head_wire_round_trip_is_exact(sequence):
    head = Head(sequence, "a" * 64)
    wire = custodian._head(head)
    assert wire == {"sequence": sequence, "digest": "a" * 64}
    assert custodian._parse_head(wire) == head


@pytest.mark.parametrize("value", [
    None, [], "head", {}, {"sequence": 0}, {"digest": "a" * 64},
    {"sequence": 0, "digest": "a" * 64, "extra": True},
    *({"sequence": sequence, "digest": "a" * 64}
      for sequence in (True, False, 1.0, "1", -1, MAX_EVENTS + 1)),
    *({"sequence": 0, "digest": digest} for digest in (None, 1, b"a" * 64, "a" * 63, "A" * 64, "g" * 64)),
])
def test_head_parser_rejects_schema_type_and_bound_bypasses(value):
    with pytest.raises(JournalError):
        custodian._parse_head(value)


class IntSubclass(int):
    pass


@pytest.mark.parametrize("value", [1, 1001, 2**31 - 1])
def test_uid_accepts_only_positive_bounded_plain_integers(value):
    assert custodian._uid(value) == value


@pytest.mark.parametrize("value", [None, False, True, 0, -1, 2**31, 2**64, 1.0,
                                   float("nan"), "1001", b"1001", [], {}, IntSubclass(1001)])
def test_uid_rejects_root_bool_subclasses_and_out_of_range_values(value):
    with pytest.raises(custodian.IsolationError, match="invalid dedicated principal"):
        custodian._uid(value)


@pytest.mark.skipif(sys.platform == "linux" and hasattr(socket, "SO_PEERCRED"),
                    reason="actual unsupported host only; real Linux UID tests are separate")
@pytest.mark.parametrize("entrypoint", ["principal", "server", "writer"])
def test_actual_unsupported_host_fails_closed_without_mocking_identity(entrypoint, tmp_path):
    # Do not replace platform/getuid/SO_PEERCRED or claim this is Linux isolation.
    with pytest.raises(custodian.IsolationError, match="Linux peer credentials required"):
        if entrypoint == "principal":
            custodian._principal()
        elif entrypoint == "server":
            custodian.UnixCustodianServer(object(), tmp_path / "custodian.sock", writer_uid=1001)
        else:
            custodian.UnixAnchoredJournal(tmp_path / "custodian.sock", custodian_uid=1002,
                                         scope=SCOPE, generation=GENERATION, location=LOCATION,
                                         writer=object())
