"""
Advisory Runtime contracts (Phase-0 foundation) — RuntimeContext + ProducerResult.

The two stable contracts the Runtime hands to / receives from a producer. Deliberately
minimal so the Runtime stays decoupled from every contour:

  * RuntimeContext carries ONLY runtime dependencies. It must NOT carry config,
    thresholds, limits, marketplace, sku, listing_id, snapshots, or any
    contour-specific field — the producer fetches everything it needs itself.

  * ProducerResult is ok + an OPAQUE stats blob. The Runtime never reads the
    semantics of `stats`; it stores it verbatim in the AdvisoryRun ledger.

No runner, no scheduler, no execution lives here — foundation only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from logging import Logger
from typing import Mapping

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RuntimeContext:
    """Runtime dependencies handed to a producer. Runtime-owned only."""
    db: AsyncSession
    user_id: str
    now: datetime
    run_id: str
    logger: Logger
    triggered_by: str            # scheduled | import | manual


@dataclass(frozen=True)
class ProducerResult:
    """A producer's return value. `stats` is OPAQUE to the Runtime."""
    ok: bool
    stats: Mapping[str, object] = field(default_factory=dict)
