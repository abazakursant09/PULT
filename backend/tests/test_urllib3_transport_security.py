"""Offline regressions for urllib3 2.8.0; no SDK init, DNS or real sockets.

GHSA-8988-9cw3-xx77 / GHSA-vxq7-64xx-v4gw / GHSA-gh4c-6fx4-qh6g.
CERT_NONE below is an intentionally conflicting destination test policy, never
an application setting. The proxy must retain its separate strict policy.
"""
from __future__ import annotations

import http.client
import io
from pathlib import Path
import re
import socket
import ssl
import zlib

import certifi
from packaging.version import Version
import pytest
import urllib3
from urllib3 import connection as connection_module
from urllib3.exceptions import ProtocolError
from urllib3.response import DeflateDecoder, HTTPResponse

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("This regression must not use DNS or real sockets")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.mark.parametrize("filename", ["requirements.lock", "requirements-ci.lock"])
def test_both_locks_require_fixed_urllib3(filename):
    pins = re.findall(r"^urllib3==([^\s\\]+)",
                      (BACKEND / filename).read_text(encoding="utf-8"), re.M)
    assert len(pins) == 1, "Exactly one urllib3 pin is required"
    assert Version(pins[0]) >= Version("2.8.0"), "urllib3 predates the three fixes"


@pytest.mark.parametrize("tunneling", [False, True])
def test_https_proxy_keeps_its_own_tls_policy(monkeypatch, tunneling):
    proxy_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    target_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    target_context.check_hostname = False
    target_context.verify_mode = ssl.CERT_NONE
    calls = []
    fake_socket = object()

    def wrap(sock=None, **kwargs):
        calls.append(kwargs)
        return connection_module._WrappedAndVerifiedSocket(fake_socket, True)

    monkeypatch.setattr(connection_module, "_ssl_wrap_socket_and_match_hostname", wrap)
    manager = urllib3.ProxyManager(
        "https://proxy.invalid:8443", proxy_ssl_context=proxy_context,
        proxy_assert_hostname="proxy.invalid", use_forwarding_for_https=not tunneling,
        ssl_context=target_context, cert_reqs=ssl.CERT_NONE,
        server_hostname="target-sni.invalid", assert_hostname="target.invalid",
        cert_file="target-only.pem", key_file="target-only.key",
    )
    connection = manager.connection_from_url("https://target.invalid/")._new_conn()
    monkeypatch.setattr(connection, "_new_conn", lambda: fake_socket)
    if tunneling:
        connection.set_tunnel("target.invalid", 443, scheme="https")
        monkeypatch.setattr(connection, "_tunnel", lambda: None)
    try:
        connection.connect()
        assert len(calls) == (2 if tunneling else 1)
        proxy_call = calls[0]
        assert proxy_call["ssl_context"] is proxy_context
        assert proxy_call["cert_reqs"] == ssl.CERT_REQUIRED
        assert proxy_call["server_hostname"] == "proxy.invalid"
        assert proxy_call["assert_hostname"] == "proxy.invalid"
        assert proxy_call["cert_file"] is None and proxy_call["key_file"] is None
        assert proxy_context.verify_mode == ssl.CERT_REQUIRED
        assert proxy_context.check_hostname
        if tunneling:
            assert calls[1]["ssl_context"] is target_context
            assert calls[1]["server_hostname"] == "target-sni.invalid"
    finally:
        connection.sock = None
        manager.clear()


class _Wire(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.readline_limits = []

    def readline(self, size=-1):
        self.readline_limits.append(size)
        return super().readline(size)


def _chunked_response(chunk_line):
    wire = _Wire(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                 + chunk_line + b"A\r\n0\r\n\r\n")
    fake_socket = type("MemorySocket", (), {"makefile": lambda self, *args: wire})()
    original = http.client.HTTPResponse(fake_socket, method="GET")
    original.begin()
    wire.readline_limits.clear()
    return HTTPResponse(body=original, original_response=original,
                        headers=dict(original.getheaders()), preload_content=False), wire


@pytest.mark.parametrize("reader", ["stream", "read_chunked"])
def test_chunk_size_line_is_bounded(reader):
    # A small, finite fixture: legal size + overlong extension, not a huge body.
    response, wire = _chunked_response(b"1;" + b"x" * 65536 + b"\r\n")
    with response:
        with pytest.raises(ProtocolError, match="chunk size line exceeded"):
            list(getattr(response, reader)(amt=16, decode_content=True))
    assert wire.readline_limits[0] == 65537


@pytest.mark.parametrize("reader", ["stream", "read_chunked"])
def test_normal_chunk_extension_still_decodes(reader):
    response, _ = _chunked_response(b"1;safe=ok\r\n")
    with response:
        assert b"".join(getattr(response, reader)(amt=16, decode_content=True)) == b"A"


@pytest.mark.parametrize("wbits", [zlib.MAX_WBITS, -zlib.MAX_WBITS])
def test_deflate_trailing_bytes_do_not_keep_decoder_spinning(wbits):
    payload = b"synthetic payload " * 64
    compressor = zlib.compressobj(wbits=wbits)
    encoded = compressor.compress(payload) + compressor.flush() + b"trailing bytes"
    decoder = DeflateDecoder()
    parts = [decoder.decompress(encoded, max_length=31)]
    # Bound calls even against the vulnerable version; never run an infinite loop.
    for _ in range(len(payload) // 31 + 2):
        if not decoder.has_unconsumed_tail:
            break
        part = decoder.decompress(b"", max_length=31)
        assert part, "Decoder retained trailing input after EOF without progress"
        parts.append(part)
    assert not decoder.has_unconsumed_tail
    assert b"".join(parts) + decoder.flush() == payload


@pytest.mark.parametrize("proxy", ["", "https://proxy.invalid:8443"])
def test_sentry_http_transport_remains_compatible_offline(monkeypatch, proxy):
    import sentry_sdk
    from sentry_sdk.consts import DEFAULT_OPTIONS
    from sentry_sdk.transport import EndpointType, HttpTransport

    def no_init(*args, **kwargs):
        raise AssertionError("Do not initialize Sentry in this test")

    monkeypatch.setattr(sentry_sdk, "init", no_init)
    for name in ("CLIENT_CERT_FILE", "CLIENT_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    options = dict(DEFAULT_OPTIONS, dsn="https://synthetic@example.invalid/1",
                   http_proxy="", https_proxy=proxy, ca_certs=certifi.where())
    transport = HttpTransport(options)
    calls = []
    try:
        pool = transport._pool
        assert isinstance(pool, urllib3.ProxyManager if proxy else urllib3.PoolManager)
        assert pool.connection_pool_kw["cert_reqs"] == "CERT_REQUIRED"
        assert pool.connection_pool_kw["ca_certs"] == certifi.where()
        response = HTTPResponse(status=200, body=b"{}")

        def request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return response

        monkeypatch.setattr(pool, "request", request)
        assert transport._request("POST", EndpointType.ENVELOPE, b"synthetic", {}) is response
        assert calls == [("POST", "https://example.invalid/api/1/envelope/",
                          {"body": b"synthetic", "headers": {}})]
    finally:
        transport.kill()
        transport._pool.clear()
