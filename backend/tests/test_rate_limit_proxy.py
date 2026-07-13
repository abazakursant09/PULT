"""The rate limiters and the registration IP cap keyed off X-Forwarded-For[0] — the leftmost,
client-supplied value. An attacker rotated the header to get a fresh bucket per request and
defeated login throttling / the registration cap. client_ip() now trusts only the hops our own
proxies appended (counting from the right), and ignores X-Forwarded-For entirely when no trusted
proxy is declared. These tests fail the day a spoofed header can rotate the identity again.
"""
from types import SimpleNamespace

from fastapi import Request

import rate_limit
from config import settings
from rate_limit import client_ip


def _req(xff: str | None, peer: str = "10.0.0.9") -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "headers": headers,
        "client": (peer, 12345),
    }
    return Request(scope)


def test_spoofed_xff_is_ignored_without_a_trusted_proxy(monkeypatch):
    # Default deployment: TRUSTED_PROXY_COUNT = 0. The header is untrusted, full stop.
    monkeypatch.setattr(settings, "trusted_proxy_count", 0)
    a = client_ip(_req("1.2.3.4", peer="10.0.0.9"))
    b = client_ip(_req("9.9.9.9", peer="10.0.0.9"))
    c = client_ip(_req(None, peer="10.0.0.9"))
    # Every request resolves to the real peer, no matter what the attacker put in the header.
    assert a == b == c == "10.0.0.9"


def test_spoofed_xff_cannot_rotate_the_identity_behind_a_proxy(monkeypatch):
    # One trusted proxy (Caddy/nginx) appends the true client to whatever the client sent.
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    # Attacker sends a forged left entry; the proxy appended the real client (203.0.113.7).
    forged1 = client_ip(_req("1.1.1.1, 203.0.113.7"))
    forged2 = client_ip(_req("2.2.2.2, 203.0.113.7"))
    forged3 = client_ip(_req("evil, more, 203.0.113.7"))
    # All resolve to the same real client — the spoofed prefix is ignored.
    assert forged1 == forged2 == forged3 == "203.0.113.7"


def test_trusted_proxy_forwarding_still_yields_the_real_client(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    assert client_ip(_req("198.51.100.42", peer="172.18.0.2")) == "198.51.100.42"


def test_two_trusted_proxies_skip_both_appended_hops(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_count", 2)
    # Two proxies: the edge proxy appended the real client, the app proxy appended the edge.
    # As the app sees it: <spoofable client-supplied>, <real client>, <edge proxy>.
    # The real client sits N=2 from the right; the leftmost is still attacker-controlled.
    assert client_ip(_req("evil-spoof, 203.0.113.7, 10.0.0.1")) == "203.0.113.7"


def test_short_header_falls_back_to_peer(monkeypatch):
    # Header shorter than the declared depth → it did not pass the expected chain → untrusted.
    monkeypatch.setattr(settings, "trusted_proxy_count", 2)
    assert client_ip(_req("1.2.3.4", peer="172.18.0.2")) == "172.18.0.2"


def test_limiter_bucket_does_not_rotate_on_spoofed_header(monkeypatch):
    # End to end: the same peer hammering with a rotating spoofed XFF must share ONE bucket
    # and hit the 429, proving the identity no longer rotates.
    import asyncio
    monkeypatch.setattr(settings, "trusted_proxy_count", 0)
    fresh = rate_limit._SlidingWindow()
    monkeypatch.setattr(rate_limit, "_limiter", fresh)

    async def _drive():
        blocked = False
        for i in range(8):
            r = _req(f"9.9.9.{i}", peer="10.0.0.9")   # different spoofed XFF each time
            try:
                await rate_limit.limit_auth(r)
            except Exception as e:
                blocked = getattr(e, "status_code", None) == 429
                break
        return blocked

    assert asyncio.new_event_loop().run_until_complete(_drive()) is True
