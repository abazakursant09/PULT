"""Socket-denied working-v2 contracts, not IAM/live-S3/protocol-state proof.

MockTransport handlers are trusted test code, NOT a sandbox. Bucket separation
is routing here: equal scope/generation in another bucket is not a new crypto
binding. All credentials/objects below are artificial and never working inputs.
"""
from dataclasses import FrozenInstanceError
import hashlib
import inspect
import json
from pathlib import Path, PurePosixPath
import socket
import sys
import traceback

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backup import erasure_custodian_host as old_host
from ops.backup import erasure_journal_s3 as old_s3
from ops.backup import working_journal_v2 as gate
from ops.backup.custodian_v2.codec import event_codec as codec
from ops.backup.erasure_journal import JournalError

GENERATION = "1e534b17-05e7-4529-8ffa-c32162c67ad5"
OTHER_GENERATION = "61896c0f-e851-4d49-a1be-b11ab3dc30f8"
CONFIG = dict(endpoint="https://s3.ru-3.storage.selcloud.ru", region="ru-3",
              bucket="pult-custodian-working-unit", scope="pult-work-unit", generation=GENERATION)
READER = dict(access_key="FAKE_READER_KEY", secret_key="FAKE_READER_SECRET_NOT_LIVE")
WRITER = dict(access_key="FAKE_WRITER_KEY", secret_key="FAKE_WRITER_SECRET_NOT_LIVE")
SECRET = "FAKE_PRIVATE_DIAGNOSTIC_DO_NOT_ECHO"
EVENT = codec.Event("c7461350-9188-4bf1-bc6c-c4a5b9455bec",
                    "ff636b10-6ccb-4b1b-9394-df59af74b793", "intent")


class StrAlias(str):
    pass


class DictAlias(dict):
    pass


class NamespaceAlias(gate.WorkingNamespace):
    pass


class TransportAlias(httpx.MockTransport):
    pass


class HeadAlias(codec.Head):
    pass


class EventAlias(codec.Event):
    pass


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("portable storage contracts must not access sockets")
    for method in ("connect", "connect_ex", "bind", "listen", "sendto", "sendmsg"):
        if hasattr(socket.socket, method):
            monkeypatch.setattr(socket.socket, method, blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def namespace():
    return gate.WorkingNamespace(**CONFIG)


@pytest.fixture
def factory(namespace):
    capabilities = []
    def make(kind, handler, **changes):
        dispatches = []
        def tracked(request):
            dispatches.append(request)  # Record before trusted handler can raise.
            return handler(request)
        options = dict(namespace=namespace, credential=READER if kind is gate.WorkingEventReader else WRITER,
                       transport=httpx.MockTransport(tracked))
        options.update(changes)
        capability = kind(**options)
        capabilities.append(capability)
        make.calls[capability] = dispatches
        return capability
    make.calls = {}
    yield make
    for capability in capabilities:
        if not capability._closed:
            capability.close()


def fail_before_io(*args, **kwargs):
    raise AssertionError("validation must precede credentials/client/dispatch")


def record(namespace):
    base = codec.genesis(namespace.binding)
    body, head = codec.encode_candidate(namespace.binding, base, EVENT)
    return base, body, head


def sanitized(error, expected_type, message):
    assert type(error.value) is expected_type
    assert str(error.value) == message
    assert error.value.__suppress_context__
    assert SECRET not in "".join(traceback.format_exception(error.value))


class ResponseStream(httpx.SyncByteStream):
    def __init__(self, chunks=(), *, failure=None, close_failure=None):
        self.chunks = chunks
        self.failure, self.close_failure = failure, close_failure
        self.iterations = self.closes = 0
        self.chunks_yielded = 0
        self.reached_tail = False

    def __iter__(self):
        self.iterations += 1
        for chunk in self.chunks:
            self.chunks_yielded += 1
            yield chunk
        self.reached_tail = True
        if self.failure is not None:
            raise self.failure

    def close(self):
        self.closes += 1
        if self.close_failure is not None:
            raise self.close_failure


@pytest.mark.parametrize("bucket", [
    "pult-custodian-working-a", "pult-custodian-working-" + "a" * (63 - len("pult-custodian-working-")),
])
@pytest.mark.parametrize("scope", ["pult-work-a", "pult-work-A_z-9", "pult-work-" + "a" * 54])
def test_namespace_exact_valid_syntax_boundaries(bucket, scope):
    value = gate.WorkingNamespace(**(CONFIG | dict(bucket=bucket, scope=scope)))
    assert value.bucket == bucket and value.scope == scope


BAD_NAMESPACE = (
    [(field, bad) for field in CONFIG for bad in (None, True, False, 1)]
    + [(field, StrAlias(value)) for field, value in CONFIG.items()]
    + [("endpoint", value) for value in ("http://s3.ru-3.storage.selcloud.ru", CONFIG["endpoint"] + "/",
        CONFIG["endpoint"] + "?x=1", "https://s3.ru-7.storage.selcloud.ru")]
    + [("region", value) for value in ("ru-7", "RU-3", "ru-3\n")]
    + [("bucket", value) for value in ("synthetic-bucket", "pult-custodian-working-",
        "pult-custodian-working-A", "pult-custodian-working-a/b", "pult-custodian-working-a.",
        "pult-custodian-working-" + "a" * (64 - len("pult-custodian-working-")), "pult-custodian-working-a\n")]
    + [("scope", value) for value in ("synthetic", "pult-work-", "pult-work-../x", "pult-work-a/b",
        "pult-work-a.b", "pult-work-a b", "pult-work-" + "a" * 55, "pult-work-a\n")]
    + [("generation", value) for value in (GENERATION.upper(), GENERATION.replace("-", ""),
        "00000000-0000-0000-0000-000000000000", "6ba7b810-9dad-11d1-80b4-00c04fd430c8", GENERATION + "\n")]
)


@pytest.mark.parametrize("field,bad", BAD_NAMESPACE)
def test_namespace_rejects_before_any_credential_or_client(monkeypatch, field, bad):
    monkeypatch.setattr(gate, "_validate_credential", fail_before_io)
    monkeypatch.setattr(gate.httpx, "Client", fail_before_io)
    with pytest.raises(gate.NamespaceError):
        gate.WorkingNamespace(**(CONFIG | {field: bad}))


@pytest.mark.parametrize("field", ["key", "prefix", "mode", "client", "transport", "live", "reader", "writer"])
def test_namespace_has_no_extra_configuration_or_positional_mode(field):
    with pytest.raises(TypeError):
        gate.WorkingNamespace(**CONFIG, **{field: "forbidden"})
    with pytest.raises(TypeError):
        gate.WorkingNamespace(*CONFIG.values())


def test_namespace_is_frozen_and_repr_does_not_disclose_binding(namespace):
    for field in CONFIG:
        with pytest.raises(FrozenInstanceError):
            setattr(namespace, field, "changed")
    assert all(value not in repr(namespace) for value in CONFIG.values())


def test_immutable_key_and_location_are_derived(namespace):
    key = f"pult/erasure-v2/{CONFIG['scope']}/{GENERATION}/events/{1:020d}.json"
    assert namespace.key(1) == key
    assert namespace.location == CONFIG["endpoint"] + "/" + CONFIG["bucket"] + "/" + key.rsplit("/", 1)[0] + "/"
    assert namespace.key(codec.MAX_SEQUENCE).endswith(f"/{codec.MAX_SEQUENCE:020d}.json")


@pytest.mark.parametrize("sequence", [True, False, 0, -1, codec.MAX_SEQUENCE + 1, "1", None, 1.0])
def test_key_sequence_is_positive_plain_bounded_int(namespace, sequence):
    with pytest.raises(gate.NamespaceError):
        namespace.key(sequence)


@pytest.mark.parametrize("change", [dict(scope="pult-work-other"), dict(generation=OTHER_GENERATION)])
def test_scope_and_generation_change_immutable_binding(namespace, change):
    other = gate.WorkingNamespace(**(CONFIG | change))
    assert namespace.binding != other.binding
    assert namespace.key(1) != other.key(1) and namespace.location != other.location
    assert codec.genesis(namespace.binding) != codec.genesis(other.binding)


def test_bucket_separation_is_routing_not_additional_crypto_provenance(namespace):
    other = gate.WorkingNamespace(**(CONFIG | dict(bucket="pult-custodian-working-other")))
    assert namespace.location != other.location
    assert namespace.key(1) == other.key(1) and namespace.binding == other.binding
    assert record(namespace)[1] == record(other)[1]


@pytest.mark.parametrize("kind", [gate.WorkingEventReader, gate.WorkingEventWriter])
def test_exact_namespace_and_mock_transport_rejected_before_credentials_client(monkeypatch, namespace, kind):
    monkeypatch.setattr(gate, "_validate_credential", fail_before_io)
    monkeypatch.setattr(gate.httpx, "Client", fail_before_io)
    for value in (None, CONFIG, namespace.binding, NamespaceAlias(**CONFIG)):
        with pytest.raises(gate.StoreError, match="^exact working namespace required$"):
            kind(namespace=value, credential=object(), transport=httpx.MockTransport(fail_before_io))
    for transport in (None, object(), object.__new__(httpx.HTTPTransport), TransportAlias(fail_before_io)):
        with pytest.raises(gate.StoreError, match="^socket-free MockTransport required$"):
            kind(namespace=namespace, credential=object(), transport=transport)


@pytest.mark.parametrize("kind", [gate.WorkingEventReader, gate.WorkingEventWriter])
@pytest.mark.parametrize("field", ["client", "key", "prefix", "mode", "live", "endpoint", "region", "bucket",
    "binding", "_test_transport", "verify", "proxy", "auth", "retries", "reader", "writer"])
def test_no_caller_transport_modes_or_implicit_defaults(namespace, kind, field):
    assert set(inspect.signature(kind).parameters) == {"namespace", "credential", "transport"}
    with pytest.raises(TypeError):
        kind(namespace=namespace, credential=READER, transport=httpx.MockTransport(fail_before_io), **{field: None})
    with pytest.raises(TypeError):
        kind(namespace=namespace, credential=READER)


@pytest.mark.parametrize("bad", [None, True, {}, DictAlias(READER),
    {"access_key": "a", "secret_key": ""}, READER | {"token": "forbidden"},
    {"access_key": "x\r\ny", "secret_key": "fake"}, {"access_key": "fake", "secret_key": "\ud800"}])
def test_invalid_credentials_refused_before_client(monkeypatch, namespace, bad):
    monkeypatch.setattr(gate.httpx, "Client", fail_before_io)
    with pytest.raises(gate.StoreError, match="^explicit working credential required$"):
        gate.WorkingEventReader(namespace=namespace, credential=bad, transport=httpx.MockTransport(fail_before_io))


def test_get_and_put_use_separate_explicit_principals_and_immutable_sigv4(factory, namespace, monkeypatch):
    base, body, head = record(namespace)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=body if request.method == "GET" else b"untrusted provider body")
    reader = factory(gate.WorkingEventReader, handler)
    writer = factory(gate.WorkingEventWriter, handler)
    signatures = []
    def spy_signer(capability):
        original = capability._signer._sign
        def tracked_sign(method, action, uri, query, payload_hash, amz_date, short_date, extra):
            signatures.append(dict(method=method, action=action, uri=uri, query=query,
                                   payload_hash=payload_hash, extra=dict(extra)))
            return original(method, action, uri, query, payload_hash, amz_date, short_date, extra)
        monkeypatch.setattr(capability._signer, "_sign", tracked_sign)
    spy_signer(reader)
    spy_signer(writer)
    assert reader.read_next(base) == (EVENT, head)
    result = writer.create(base, EVENT)
    assert type(result) is str and result == "PUT_HTTP200_UNCONFIRMED"
    assert len(requests) == 2
    assert len(signatures) == 2
    expected_uri = "/" + namespace.bucket + "/" + namespace.key(1)
    for request, credential, signed in zip(requests, (READER, WRITER), signatures):
        assert str(request.url) == namespace.endpoint + "/" + namespace.bucket + "/" + namespace.key(1)
        assert signed["method"] == request.method
        assert signed["action"] == {"GET": "GetObject", "PUT": "PutObject"}[request.method]
        assert signed["uri"] == request.url.path == expected_uri
        assert request.url.raw_path == signed["uri"].encode("ascii")
        assert signed["query"] == "" and request.url.query == b""
        assert signed["payload_hash"] == hashlib.sha256(request.content).hexdigest()
        expected_extra = {"accept-encoding": "identity"}
        if request.method == "PUT":
            expected_extra.update({"content-type": "application/json", "if-none-match": "*"})
        assert signed["extra"] == expected_extra
        assert not request.url.query and "?" not in str(request.url)
        assert "Credential=" + credential["access_key"] + "/" in request.headers["authorization"]
        assert request.headers["x-amz-content-sha256"] == hashlib.sha256(request.content).hexdigest()
        assert request.headers["accept-encoding"] == "identity"
        assert "if-match" not in request.headers
    assert requests[0].method == "GET" and requests[0].content == b""
    assert "if-none-match" not in requests[0].headers
    assert requests[1].method == "PUT" and requests[1].content == body
    assert requests[1].headers["if-none-match"] == "*"
    assert "if-none-match" in requests[1].headers["authorization"].split("SignedHeaders=", 1)[1].split(",", 1)[0].split(";")


def test_client_settings_copy_credentials_and_no_public_capability_escalation(factory, namespace):
    calls = []
    credential = dict(READER)
    base, body, _ = record(namespace)
    reader = factory(gate.WorkingEventReader, lambda request: (calls.append(request), httpx.Response(200, content=body))[1],
                     credential=credential)
    credential["access_key"] = "CHANGED_NOT_USED"
    credential["secret_key"] = "CHANGED_NOT_USED"
    reader.read_next(base)
    assert "Credential=" + READER["access_key"] + "/" in calls[0].headers["authorization"]
    assert reader.namespace is namespace and reader.location == namespace.location
    assert reader._client.follow_redirects is False and reader._client.trust_env is False
    assert reader._client.timeout == httpx.Timeout(15, connect=5)
    assert reader._client._auth is None
    assert not any(hasattr(reader, name) for name in ("create", "write", "delete", "list"))
    writer = factory(gate.WorkingEventWriter, fail_before_io)
    assert not any(hasattr(writer, name) for name in ("read", "read_next", "delete", "list"))
    assert all(secret not in repr(reader) + repr(writer) for secret in READER.values())
    for capability in (reader, writer):
        with pytest.raises(AttributeError):
            capability.namespace = namespace
        with pytest.raises(AttributeError):
            capability.location = "other"


@pytest.mark.parametrize("status", [201, 204, 301, 302, 307, 308, 400, 403, 404, 409, 429, 500, 503])
def test_non200_write_is_unknown_no_retry_or_redirect(factory, namespace, status):
    calls = []
    stream = ResponseStream(failure=AssertionError("writer must not consume provider body"))
    writer = factory(gate.WorkingEventWriter, lambda request: (calls.append(request),
        httpx.Response(status, headers={"location": "https://forbidden.invalid/"}, stream=stream))[1])
    with pytest.raises(gate.WriteUnknown) as error:
        writer.create(codec.genesis(namespace.binding), EVENT)
    assert str(error.value) == "working event write outcome unknown"
    assert len(calls) == 1 and stream.closes == 1 and stream.iterations == 0


def test_only_received_412_after_stream_close_is_conflict(factory, namespace):
    calls = []
    stream = ResponseStream(failure=AssertionError("must not read conflict body"))
    writer = factory(gate.WorkingEventWriter, lambda request: (calls.append(request), httpx.Response(412, stream=stream))[1])
    with pytest.raises(gate.Conflict, match="^working event key already exists$"):
        writer.create(codec.genesis(namespace.binding), EVENT)
    assert len(calls) == 1 and stream.closes == 1 and not stream.iterations


@pytest.mark.parametrize("status", [201, 204, 206, 301, 302, 307, 403, 412, 429, 500, 503])
def test_get_bad_status_never_retries_reads_body_or_redirects(factory, namespace, status):
    calls = []
    stream = ResponseStream(failure=AssertionError("must not read failed body"))
    reader = factory(gate.WorkingEventReader, lambda request: (calls.append(request),
        httpx.Response(status, headers={"location": "https://forbidden.invalid/"}, stream=stream))[1])
    with pytest.raises(gate.StoreError) as error:
        reader.read_next(codec.genesis(namespace.binding))
    assert type(error.value) is gate.StoreError
    assert len(calls) == 1 and stream.closes == 1 and not stream.iterations


def test_get_404_is_missing_and_never_initializes(factory, namespace):
    calls = []
    reader = factory(gate.WorkingEventReader, lambda request: (calls.append(request), httpx.Response(404))[1])
    with pytest.raises(gate.Missing, match="^working event object unavailable$"):
        reader.read_next(codec.genesis(namespace.binding))
    assert [request.method for request in calls] == ["GET"]


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "IDENTITY"])
def test_compressed_get_is_rejected_without_body_consumption(factory, namespace, encoding):
    stream = ResponseStream(failure=AssertionError("must not decompress/read"))
    reader = factory(gate.WorkingEventReader, lambda request:
        httpx.Response(200, headers={"content-encoding": encoding}, stream=stream))
    with pytest.raises(gate.StoreError, match="^working event read failed$"):
        reader.read_next(codec.genesis(namespace.binding))
    assert stream.closes == 1 and not stream.iterations


def _assert_oversize_stopped(stream):
    assert stream.iterations == stream.closes == 1
    assert stream.chunks_yielded == 1
    assert stream.reached_tail is False


def test_oversize_stream_stops_early_and_closes(factory, namespace):
    stream = ResponseStream([b"x" * (codec.MAX_RECORD_BYTES + 1), b"SECOND_CHUNK_SENTINEL"])
    reader = factory(gate.WorkingEventReader, lambda request: httpx.Response(200, stream=stream))
    with pytest.raises(gate.StoreError, match="^working event read failed$"):
        reader.read_next(codec.genesis(namespace.binding))
    _assert_oversize_stopped(stream)


@pytest.mark.parametrize("overread", ["second_chunk", "tail"])
def test_oversize_oracle_rejects_overconsuming_test_mutants(overread):
    # Oracle-only mutation proof: no production patch, HTTP request or socket.
    chunks = [b"x" * (codec.MAX_RECORD_BYTES + 1)]
    if overread == "second_chunk":
        chunks.append(b"SECOND_CHUNK_SENTINEL")
    stream = ResponseStream(chunks)
    iterator = iter(stream)
    next(iterator)
    if overread == "second_chunk":
        next(iterator)
        assert stream.chunks_yielded == 2 and stream.reached_tail is False
    else:
        list(iterator)
        assert stream.chunks_yielded == 1 and stream.reached_tail is True
    stream.close()
    with pytest.raises(AssertionError):
        _assert_oversize_stopped(stream)


@pytest.mark.parametrize("mode", ["empty", "json", "noncanonical", "duplicate", "scope", "generation", "sequence",
    "sequence_bool", "previous", "digest", "extra", "event_extra", "phase", "subject", "purge", "utf8"])
def test_reader_refuses_malformed_wrong_binding_chain_event_and_body(factory, namespace, mode):
    base, body, _ = record(namespace)
    value = json.loads(body)
    if mode == "empty":
        body = b""
    elif mode == "json":
        body = b"{}\n"
    elif mode == "noncanonical":
        body = body.rstrip(b"\n")
    elif mode == "duplicate":
        body = b'{"format":"duplicate",' + body[1:]
    elif mode == "utf8":
        body = b"\xff"
    else:
        if mode == "scope":
            value["scope"] = "pult-work-other"
        elif mode == "generation":
            value["generation"] = OTHER_GENERATION
        elif mode == "sequence":
            value["sequence"] = 2
        elif mode == "sequence_bool":
            value["sequence"] = True
        elif mode == "previous":
            value["previous"] = "0" * 64
        elif mode == "digest":
            value["digest"] = "0" * 64
        elif mode == "extra":
            value["approved"] = True
        elif mode == "event_extra":
            value["event"]["approved"] = True
        elif mode == "phase":
            value["event"]["phase"] = "purge"
        elif mode == "subject":
            value["event"]["subject_id"] = "not-a-uuid"
        elif mode == "purge":
            value["event"]["purge_id"] = "not-a-uuid"
        if mode != "digest":
            unsigned = {key: item for key, item in value.items() if key != "digest"}
            value["digest"] = hashlib.sha256(codec.canonical(unsigned)).hexdigest()
        body = codec.canonical(value)
    calls = []
    reader = factory(gate.WorkingEventReader, lambda request: (calls.append(request), httpx.Response(200, content=body))[1])
    with pytest.raises(gate.StoreError, match="^working event chain verification failed$"):
        reader.read_next(base)
    assert len(calls) == 1


def test_exact_next_record_from_nonzero_trusted_head(factory, namespace):
    base, first, head = record(namespace)
    complete = codec.Event(EVENT.purge_id, EVENT.subject_id, "complete")
    second, target = codec.encode_candidate(namespace.binding, head, complete)
    bodies = iter((first, second))
    calls = []
    reader = factory(gate.WorkingEventReader, lambda request:
        (calls.append(request), httpx.Response(200, content=next(bodies)))[1])
    assert reader.read_next(base) == (EVENT, head)
    assert reader.read_next(head) == (complete, target)
    assert [request.url.path.rsplit("/", 1)[1] for request in calls] == [f"{i:020d}.json" for i in (1, 2)]


@pytest.mark.parametrize("kind", [gate.WorkingEventReader, gate.WorkingEventWriter])
def test_wrong_genesis_exhaustion_and_untyped_values_never_dispatch(factory, namespace, kind):
    capability = factory(kind, fail_before_io)
    action = capability.read_next if kind is gate.WorkingEventReader else lambda head: capability.create(head, EVENT)
    for value in (None, True, {}, HeadAlias(0, codec.genesis(namespace.binding).digest), codec.Head(0, "0" * 64)):
        with pytest.raises(gate.StoreError):
            action(value)
        assert factory.calls[capability] == []
    with pytest.raises(gate.NamespaceError if kind is gate.WorkingEventReader else gate.StoreError):
        action(codec.Head(codec.MAX_SEQUENCE, "a" * 64))
    assert factory.calls[capability] == []
    if kind is gate.WorkingEventWriter:
        for value in (None, True, {}, EventAlias(EVENT.purge_id, EVENT.subject_id, EVENT.phase)):
            with pytest.raises(gate.StoreError):
                capability.create(codec.genesis(namespace.binding), value)
            assert factory.calls[capability] == []


@pytest.mark.parametrize("kind", [gate.WorkingEventReader, gate.WorkingEventWriter])
@pytest.mark.parametrize("failure", [RuntimeError, httpx.ReadTimeout, httpx.ConnectError, gate.Conflict, gate.WriteUnknown])
def test_handler_exceptions_cannot_forge_conflict_or_escape_details(factory, namespace, kind, failure):
    calls = []
    def handler(request):
        calls.append(request)
        raise failure(SECRET)
    capability = factory(kind, handler)
    expected = gate.StoreError if kind is gate.WorkingEventReader else gate.WriteUnknown
    with pytest.raises(expected) as error:
        capability.read_next(codec.genesis(namespace.binding)) if kind is gate.WorkingEventReader else capability.create(codec.genesis(namespace.binding), EVENT)
    sanitized(error, expected, "working event read failed" if kind is gate.WorkingEventReader else "working event write outcome unknown")
    assert len(calls) == 1


@pytest.mark.parametrize("kind", [gate.WorkingEventReader, gate.WorkingEventWriter])
@pytest.mark.parametrize("status", [200, 412, 404])
def test_stream_close_exception_cannot_select_conflict_missing_or_success(factory, namespace, kind, status):
    base, body, _ = record(namespace)
    stream = ResponseStream([body], close_failure=gate.Conflict(SECRET))
    calls = []
    capability = factory(kind, lambda request: (calls.append(request), httpx.Response(status, stream=stream))[1])
    expected = gate.StoreError if kind is gate.WorkingEventReader else gate.WriteUnknown
    with pytest.raises(expected) as error:
        capability.read_next(base) if kind is gate.WorkingEventReader else capability.create(base, EVENT)
    sanitized(error, expected, "working event read failed" if kind is gate.WorkingEventReader else "working event write outcome unknown")
    assert len(calls) == 1 and stream.closes == 1


def test_reader_stream_iteration_exception_is_sanitized_and_closed(factory, namespace):
    stream = ResponseStream(failure=gate.Conflict(SECRET))
    reader = factory(gate.WorkingEventReader, lambda request: httpx.Response(200, stream=stream))
    with pytest.raises(gate.StoreError) as error:
        reader.read_next(codec.genesis(namespace.binding))
    sanitized(error, gate.StoreError, "working event read failed")
    assert stream.iterations == stream.closes == 1


@pytest.mark.parametrize("kind", [gate.WorkingEventReader, gate.WorkingEventWriter])
def test_signing_exception_dispatches_nothing_and_is_sanitized(factory, namespace, monkeypatch, kind):
    capability = factory(kind, fail_before_io)
    def bad_sign(*args, **kwargs):
        raise gate.Conflict(SECRET)
    monkeypatch.setattr(capability._signer, "_sign", bad_sign)
    expected = gate.StoreError if kind is gate.WorkingEventReader else gate.WriteUnknown
    with pytest.raises(expected) as error:
        capability.read_next(codec.genesis(namespace.binding)) if kind is gate.WorkingEventReader else capability.create(codec.genesis(namespace.binding), EVENT)
    assert factory.calls[capability] == []
    sanitized(error, expected, "working event read failed" if kind is gate.WorkingEventReader else "working event write outcome unknown")


@pytest.mark.parametrize("kind", [gate.WorkingEventReader, gate.WorkingEventWriter])
def test_capability_close_failure_marks_closed_and_prevents_any_further_operation(factory, namespace, monkeypatch, kind):
    capability = factory(kind, fail_before_io)
    original_close = capability._client.close
    def bad_close():
        raise RuntimeError(SECRET)
    monkeypatch.setattr(capability._client, "close", bad_close)
    with pytest.raises(gate.StoreError) as error:
        capability.close()
    assert factory.calls[capability] == []
    sanitized(error, gate.StoreError, "working event close failed")
    assert capability._closed is True
    with pytest.raises(gate.StoreError if kind is gate.WorkingEventReader else gate.WriteUnknown):
        capability.read_next(codec.genesis(namespace.binding)) if kind is gate.WorkingEventReader else capability.create(codec.genesis(namespace.binding), EVENT)
    assert factory.calls[capability] == []
    original_close()  # Close the owned mocked client without lifting socket denial.
    assert factory.calls[capability] == []


def test_legacy_v1_adapter_still_rejects_working_object_key_before_credentials(monkeypatch, namespace):
    monkeypatch.setattr(old_s3, "_validate_credential", fail_before_io)
    monkeypatch.setattr(old_s3.httpx if hasattr(old_s3, "httpx") else httpx, "Client", fail_before_io)
    with pytest.raises(JournalError, match="^unapproved journal location$"):
        old_s3.S3JournalReader(endpoint=namespace.endpoint, region=namespace.region, bucket=namespace.bucket,
            key=namespace.key(1), reader=READER, _test_transport=httpx.MockTransport(fail_before_io))


def test_legacy_host_parser_still_rejects_working_key_without_io(monkeypatch, namespace):
    monkeypatch.setattr(old_host, "Path", PurePosixPath)
    value = dict(format=old_host.FORMAT, scope=namespace.scope, generation=GENERATION,
        custodian_uid=46001, writer_uid=46002, socket_gid=46002,
        state_file="/synthetic/state/anchor.sqlite", socket_file="/synthetic/run/custodian.sock",
        s3=dict(endpoint=namespace.endpoint, region=namespace.region, bucket=namespace.bucket, key=namespace.key(1)))
    with pytest.raises(old_host.HostError, match="^invalid explicit journal location$"):
        old_host.parse_config(json.dumps(value).encode("ascii"))
