"""Exercise real httpx serialization and existing signer against a socket-free stub."""
import hashlib
from pathlib import Path
import socket
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backup.erasure_journal import (
    MAX_BYTES, Conflict, Event, Journal, JournalError, Missing, WriteUnknown, empty, genesis,
)
from ops.backup.erasure_journal_s3 import S3JournalReader, S3JournalStore

READER = {"access_key": "synthetic-reader", "secret_key": "synthetic-reader-secret"}
WRITER = {"access_key": "synthetic-writer", "secret_key": "synthetic-writer-secret"}
CONFIG = dict(endpoint="https://s3.ru-3.storage.selcloud.ru", region="ru-3",
              bucket="synthetic-bucket", key="pult/erasure-test/synthetic/journal.json",
              reader=READER, writer=WRITER)
BODY = empty("synthetic").encode()


@pytest.fixture(autouse=True)
def no_real_sockets(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("offline tests must never open a socket")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def store_for(handler, **changes):
    return S3JournalStore(**(CONFIG | changes),
                          _test_transport=httpx.MockTransport(handler))


def test_signed_get_and_conditional_put_use_separate_keys_no_query():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=BODY, headers={"ETag": '"opaque-etag"'})
    store = store_for(handler)
    assert store.read().body == BODY
    store.write(BODY, etag='"opaque-etag"')
    store.write(BODY, etag=None)
    assert len(requests) == 3
    for request, principal in zip(requests, ("synthetic-reader", "synthetic-writer", "synthetic-writer")):
        assert request.url == CONFIG["endpoint"] + "/synthetic-bucket/" + CONFIG["key"]
        assert "Credential=" + principal + "/" in request.headers["authorization"]
        assert request.headers["x-amz-content-sha256"] == hashlib.sha256(request.content).hexdigest()
        assert "?" not in str(request.url)
    assert requests[1].headers["if-match"] == '"opaque-etag"'
    assert "if-match" in requests[1].headers["authorization"].split("SignedHeaders=")[1]
    assert requests[2].headers["if-none-match"] == "*"
    assert "if-none-match" in requests[2].headers["authorization"].split("SignedHeaders=")[1]
    assert "if-match" not in requests[2].headers and "if-none-match" not in requests[1].headers
    assert "secret" not in repr(store) and "synthetic-reader" not in repr(store)


@pytest.mark.parametrize("code", [201, 204, 301, 302, 307, 308, 400, 403, 409, 429, 500, 503])
def test_ambiguous_or_failed_write_never_retries_or_follows_redirect(code):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(code, headers={"Location": "https://untrusted.example/"},
                              content=b"private provider diagnostic synthetic-secret")
    store = store_for(handler)
    with pytest.raises(WriteUnknown) as error:
        store.write(BODY, etag='"old"')
    assert len(requests) == 1
    assert "synthetic-secret" not in str(error.value)


def test_412_is_conflict_not_automatic_retry():
    calls = []
    store = store_for(lambda request: (calls.append(request), httpx.Response(412))[1])
    with pytest.raises(Conflict):
        store.write(BODY, etag='"stale"')
    assert len(calls) == 1


@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout, httpx.WriteError,
                                     httpx.RemoteProtocolError])
def test_network_tls_or_protocol_error_sanitized_and_no_retry(method, failure):
    calls = []
    def handler(request):
        calls.append(request)
        raise failure("synthetic credential must not escape")
    store = store_for(handler)
    with pytest.raises(WriteUnknown if method == "PUT" else JournalError) as error:
        store.read() if method == "GET" else store.write(BODY, etag='"old"')
    assert len(calls) == 1 and "credential" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("code", [201, 206, 301, 302, 307, 403, 429, 500, 503])
def test_read_requires_exact_200_and_never_retries(code):
    calls = []
    store = store_for(lambda request: (calls.append(request), httpx.Response(code))[1])
    with pytest.raises(JournalError):
        store.read()
    assert len(calls) == 1


def test_404_is_missing_never_create():
    calls = []
    store = store_for(lambda request: (calls.append(request), httpx.Response(404))[1])
    with pytest.raises(Missing):
        store.read()
    assert [request.method for request in calls] == ["GET"]


class Chunks(httpx.SyncByteStream):
    def __init__(self):
        self.count = 0
        self.closed = False

    def __iter__(self):
        for _ in range(100):
            self.count += 1
            yield b"x" * 8192

    def close(self):
        self.closed = True


def test_chunked_read_bound_closes_early_without_consuming_whole_body():
    chunks = Chunks()
    store = store_for(lambda request: httpx.Response(200, stream=chunks, headers={"etag": '"e"'}))
    with pytest.raises(JournalError, match="bound"):
        store.read()
    assert chunks.count == MAX_BYTES // 8192 + 1 and chunks.closed


def test_compressed_response_is_rejected_before_decompression_or_read():
    chunks = Chunks()
    store = store_for(lambda request: httpx.Response(200, stream=chunks,
        headers={"etag": '"e"', "content-encoding": "gzip"}))
    with pytest.raises(JournalError):
        store.read()
    assert chunks.count == 0 and chunks.closed


@pytest.mark.parametrize("etag", [None, "", "*", "bare", 'W/"weak"'])
def test_missing_or_weak_get_validator_rejected(etag):
    store = store_for(lambda request: httpx.Response(200, content=BODY,
                      headers={} if etag is None else {"etag": etag}))
    with pytest.raises(JournalError):
        store.read()


@pytest.mark.parametrize("changes", [
    {"endpoint": "http://s3.ru-3.storage.selcloud.ru"}, {"endpoint": "https://untrusted.example"},
    {"region": "ru-7"}, {"bucket": "bad/bucket"}, {"bucket": "a"},
    {"key": "pult/pg/real-backup"}, {"key": "pult/erasure-test/../journal.json"},
    {"key": "pult/erasure-test/a/journal.json?versionId=1"},
    {"reader": WRITER}, {"writer": {"access_key": "x\r\ny", "secret_key": "s"}},
    {"reader": {"access_key": "synthetic", "secret_key": ""}},
])
def test_location_and_explicit_separate_credentials_validated_before_network(changes):
    with pytest.raises(JournalError):
        S3JournalStore(**(CONFIG | changes))


def test_default_client_enforces_tls_no_proxy_no_redirect_and_close(monkeypatch):
    settings = []
    class Client:
        def __init__(self, **kwargs):
            settings.append(kwargs)
        def stream(self, *args, **kwargs):
            raise httpx.ConnectError("offline")
        def close(self):
            settings[0]["transport"].close()
            settings.append("closed")
    monkeypatch.setattr(httpx, "Client", Client)
    store = S3JournalStore(**CONFIG)
    assert settings == []  # lazy constructor does not connect/read credential providers
    with pytest.raises(JournalError):
        store.read()
    assert settings[0]["verify"] is True
    assert settings[0]["follow_redirects"] is False and settings[0]["trust_env"] is False
    assert settings[0]["auth"] is None
    assert settings[0]["timeout"].connect == 5 and settings[0]["timeout"].read == 15
    store.close()
    assert settings[-1] == "closed"


@pytest.mark.parametrize("body,etag", [(b"", None), (b"x" * (MAX_BYTES + 1), None),
                                       (BODY, "*"), (BODY, 'W/"x"')],
                         ids=["empty", "oversized", "wildcard", "weak"])
def test_invalid_write_never_dispatches(body, etag):
    store = store_for(lambda request: pytest.fail("must fail before HTTP"))
    with pytest.raises(JournalError):
        store.write(body, etag=etag)


@pytest.mark.parametrize("lose_response", [False, True])
def test_whole_protocol_through_real_adapter_with_persisted_stub_object(lose_response):
    """Real signing/HTTP serialization + core; stub persistence is NOT cloud proof."""
    remote, requests = {}, []
    def handler(request):
        requests.append(request.method)
        if request.method == "GET":
            if not remote:
                return httpx.Response(404)
            return httpx.Response(200, content=remote["body"], headers={"etag": remote["etag"]})
        assert request.method == "PUT"
        if "if-none-match" in request.headers:
            if remote:
                return httpx.Response(412)
        elif remote.get("etag") != request.headers["if-match"]:
            return httpx.Response(412)
        remote.update(body=request.content, etag='"' + hashlib.sha256(request.content).hexdigest() + '"')
        if lose_response and len(requests) > 2:
            raise httpx.ReadTimeout("synthetic lost acknowledgement after persistence")
        return httpx.Response(200)
    store = store_for(handler)
    journal = Journal(store, "synthetic")
    assert journal.initialize(genesis("synthetic")).status == "STORED_UNANCHORED"
    event = Event("73e1c70a-3840-40ec-b5a2-6380343d9cbf",
                  "770d659f-c20f-46dc-b3bc-3f6000204bc6", "intent")
    result = journal.append(event, genesis("synthetic"))
    assert result.status == ("UNKNOWN" if lose_response else "STORED_UNANCHORED")
    assert result.observed.sequence == 1
    assert requests == ["PUT", "GET", "GET", "PUT", "GET"]
    # A fresh in-process coordinator observes the existing intent without any PUT.
    repeat = Journal(store, "synthetic").append(event, genesis("synthetic"))
    assert repeat.status == "ALREADY_STORED_UNANCHORED"
    assert requests == ["PUT", "GET", "GET", "PUT", "GET", "GET"]


@pytest.mark.parametrize("filename", ["journal/json", "journal?json", "journal#json"])
def test_exact_key_filename_rejects_wildcard_dot_matches(filename):
    with pytest.raises(JournalError):
        S3JournalStore(**(CONFIG | {"key": "pult/erasure-test/synthetic/" + filename}))


def test_injected_client_auth_cannot_replace_sigv4():
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("no dispatch")),
                      auth=("synthetic", "synthetic")) as client:
        with pytest.raises(TypeError):
            S3JournalStore(**CONFIG, http_client=client)


def test_non_utf8_secret_rejected_before_client_construction():
    credential = {"access_key": "synthetic", "secret_key": "synthetic-\ud800-secret"}
    with pytest.raises(JournalError):
        S3JournalStore(**(CONFIG | {"reader": credential}))


def test_signing_exception_never_exposes_credential_in_exception(monkeypatch):
    store = store_for(lambda request: pytest.fail("must fail before dispatch"))
    def bad_sign(*args, **kwargs):
        raise UnicodeError("synthetic secret must not escape")
    monkeypatch.setattr(store._reader, "_sign", bad_sign)
    with pytest.raises(JournalError) as caught:
        store.read()
    assert "synthetic secret" not in str(caught.value) and caught.value.__suppress_context__


def test_untrusted_injected_client_is_not_supported():
    with httpx.Client(transport=httpx.HTTPTransport(verify=False, retries=2)) as client:
        with pytest.raises((TypeError, JournalError)):
            S3JournalStore(**CONFIG, http_client=client)


@pytest.mark.parametrize("factory", [lambda: httpx.HTTPTransport(verify=False, retries=2),
                                     lambda: object(),
                                     lambda: type("CustomMock", (httpx.MockTransport,), {})(lambda request: None)])
def test_only_exact_offline_mock_transport_is_accepted(factory):
    transport = factory()
    try:
        with pytest.raises(JournalError):
            S3JournalStore(**CONFIG, _test_transport=transport)
    finally:
        if hasattr(transport, "close"):
            transport.close()


def test_real_transport_explicitly_enforces_tls_no_env_and_zero_retry(monkeypatch):
    settings = []
    class Transport:
        def __init__(self, **kwargs):
            settings.append(kwargs)
    class Client:
        def __init__(self, **kwargs):
            self.transport = kwargs["transport"]
        def stream(self, *args, **kwargs):
            assert kwargs["auth"] is None and kwargs["follow_redirects"] is False
            raise httpx.ConnectError("synthetic")
    monkeypatch.setattr(httpx, "HTTPTransport", Transport)
    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(JournalError):
        S3JournalStore(**CONFIG).read()
    assert settings == [{"verify": True, "trust_env": False, "retries": 0}]


def test_reader_only_has_no_writer_credential_or_write_surface():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=BODY, headers={"etag": '"reader-only"'})
    config = {key: value for key, value in CONFIG.items() if key != "writer"}
    reader = S3JournalReader(**config, _test_transport=httpx.MockTransport(handler))
    assert reader._writer is None and not hasattr(reader, "write")
    assert reader.location == CONFIG["endpoint"] + "/" + CONFIG["bucket"] + "/" + CONFIG["key"]
    assert reader.read().body == BODY
    assert len(calls) == 1 and calls[0].method == "GET"
    assert "Credential=synthetic-reader/" in calls[0].headers["authorization"]
    with pytest.raises(JournalError):
        reader._request("PUT", BODY, {"if-none-match": "*"})
    assert len(calls) == 1
    reader.close()


def test_reader_rejects_writer_argument_and_arbitrary_client():
    with pytest.raises(TypeError):
        S3JournalReader(**CONFIG)
    config = {key: value for key, value in CONFIG.items() if key != "writer"}
    with pytest.raises(TypeError):
        S3JournalReader(**config, http_client=object())
    with pytest.raises(JournalError):
        S3JournalReader(**config, _test_transport=object())
