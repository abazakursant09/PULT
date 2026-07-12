"""The contract every marketplace probe adapter implements (F1.2b-b).

This file is COMMON, and it is the reason the spine can stay marketplace-agnostic: it
describes what an adapter is handed and what it must give back, and nothing about any
particular marketplace. No host, no path, no header name, no status code lives here.

An adapter answers exactly one question — "do these credentials work for this scope?" —
and it answers it with a VerificationResult from the shared taxonomy. It never touches the
database: it receives no session, no ORM row and no audit service, so it cannot persist a
verdict even by accident. Recording and state transitions belong to the spine, which knows
the rules that keep a timeout from destroying a working credential.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from ..taxonomy import VerificationResult


@dataclass(frozen=True)
class ProbeContext:
    """Everything an adapter may need — every adapter takes only what it uses.

    The spine hands over the whole set rather than branching on the marketplace to
    assemble a bespoke payload. That is deliberate: the moment the spine decides which
    fields Wildberries wants, it starts knowing what Wildberries is.

    `secret` is plaintext, decrypted at the single boundary in the runner. It must never
    be logged, echoed into an exception, or written anywhere.
    """
    secret: str
    marketplace: str
    scope: str
    # Non-secret connection/credential fields that existing credential shapes require —
    # e.g. Ozon pairs its key with a Client-Id that the seller enters separately.
    ozon_client_id: Optional[str] = None
    credential_meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeRequest:
    method: str
    url: str
    headers: dict[str, str]
    json: Any | None = None


@dataclass(frozen=True)
class ProbeResponse:
    """The minimum an adapter needs to classify. The body never leaves the adapter."""
    status: int
    headers: dict[str, str]
    json: Any | None = None


class ProbeAdapter(Protocol):
    """One marketplace's read-only credential probe."""

    async def verify(self, context: ProbeContext, transport: Any) -> VerificationResult:
        """Probe the marketplace and classify. Read-only; must never write anything.

        May issue more than one request through `transport` (Wildberries needs a second
        call to tell a wrong-category token from a bad one). Transport failures are raised
        by the transport and classified by the spine — they are not marketplace-specific.
        """
        ...
