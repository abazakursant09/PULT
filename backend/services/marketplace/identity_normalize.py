"""Identity-layer marketplace normalizer (PULT-LAUNCH-1.2/1.3).

Maps every KNOWN marketplace alias to the CANONICAL identity value used by
MarketplaceAccount / MarketplaceConnection / MarketplaceStore — the FULL name
(wildberries / ozon / yandex).

This is deliberately NOT ``services.marketplace.metric_catalog.normalize_marketplace``,
which returns the SHORT metric codes (e.g. 'wb'). Using that here would write
``account.marketplace='wb'``, which violates the store↔account composite FK and the
canonical identity set. An unknown value returns ``None`` — the caller MUST treat it
as needs_review and NEVER guess a marketplace.
"""
from __future__ import annotations

from typing import Optional

CANONICAL = ("wildberries", "ozon", "yandex")

# Only the aliases proven to occur in real data (PULT-LAUNCH-1.2 §2). Anything not
# listed is intentionally left unresolved so tightening halts instead of guessing.
_ALIASES = {
    "wb": "wildberries",
    "wildberries": "wildberries",
    "ozon": "ozon",
    "ym": "yandex",
    "yandex": "yandex",
    "yandex_market": "yandex",
}


def normalize_identity_marketplace(value: Optional[str]) -> Optional[str]:
    """Return the canonical full marketplace name, or None for an unknown value.

    None means "do not guess": the row stays needs_review and no schema tightening
    (CHECK / NOT NULL) may proceed for it.
    """
    if value is None:
        return None
    return _ALIASES.get(value.strip().lower())
