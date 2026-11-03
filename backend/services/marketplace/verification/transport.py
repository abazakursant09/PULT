"""HTTP for probes only. One request, no retries, nothing logged (F1.2b-b).

Deliberately NOT `base_client`. That client retries 429 and 5xx twice with a backoff — the
right policy for a write the seller asked for, and the wrong one for a credential check:
Wildberries allows three `/ping` calls per 30 seconds per host, so retrying a 429 is how
you turn a rate limit into a block, and three attempts at a 15-second timeout is a
~47-second wait on a button the seller is watching. A probe asks once and reports honestly
what happened; a rate limit is an answer ("we do not know"), not something to fight.

Nothing here is logged. Not the URL, not the headers — which carry the token — and not the
body, which carries the seller's data. The response body is handed to the adapter in
memory and never leaves it: only a classified outcome is persisted.
"""
from __future__ import annotations

import httpx
from types import SimpleNamespace

from services.marketplace.ym_review_read_hold import (
    YMReviewReadDeferred, admit_probe_request, observe_yandex_response,
)

from .adapters.base import ProbeRequest, ProbeResponse

# Short on purpose. A probe is interactive: the seller is waiting on it, and a marketplace
# that has not answered in this long is telling us something we can record as "unavailable"
# rather than something worth waiting out.
PROBE_TIMEOUT_SECONDS = 8.0


class ProbeTransportError(Exception):
    """Transport failed. Marketplace-independent, so the spine classifies it, not an adapter."""

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"

    def __init__(self, kind: str) -> None:
        # No URL, no headers, no underlying exception text: httpx puts the request URL into
        # its message, and a stray token in a query string would end up in a log or Sentry.
        super().__init__(kind)
        self.kind = kind


class ProbeTransport:
    """One request per call. No retries. No logging."""

    def __init__(self, timeout: float = PROBE_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    async def request(self, req: ProbeRequest) -> ProbeResponse:
        await _admit_probe(self, req)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    req.method, req.url, headers=req.headers, json=req.json,
                )
        except httpx.TimeoutException:
            raise ProbeTransportError(ProbeTransportError.TIMEOUT) from None
        except httpx.HTTPError:
            raise ProbeTransportError(ProbeTransportError.UNAVAILABLE) from None

        await _observe_probe(self, resp)  # original httpx.Headers, before any flattening

        body = None
        if resp.content:
            try:
                body = resp.json()
            except ValueError:
                body = None      # unparseable — the adapter decides what that means

        return ProbeResponse(
            status=resp.status_code,
            headers={k.lower(): v for k, v in resp.headers.items()},
            json=body,
        )


async def _admit_probe(client, req):
    await admit_probe_request(client, req)


async def _observe_probe(client, response):
    try:
        await observe_yandex_response(client, response)
    except YMReviewReadDeferred as exc:
        exc.probe_http_status = response.status_code  # only a real response may supply this
        raise


class BoundProbeTransport:
    """Keep injected probe transports on the same explicitly selected boundary."""

    def __init__(self, transport):
        self.transport = transport

    async def request(self, req):
        await _admit_probe(self.transport, req)
        response = await self.transport.request(req)
        # Preserve the injected response's original header container; never flatten it again.
        await _observe_probe(self.transport, SimpleNamespace(
            status_code=response.status, headers=response.headers,
        ))
        return response
