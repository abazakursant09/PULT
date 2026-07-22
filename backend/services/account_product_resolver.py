"""Account-scoped product resolver (PULT-LAUNCH-1.4.3).

Resolves an imported row to a Product WITHIN one MarketplaceAccount, returning an explicit
found / missing / ambiguous verdict.

This is deliberately a NEW path, not a change to services.product_resolver — that one is keyed
on (user, short-marketplace, sku) and picks the first Product on a collision, and it still has
many consumers (backfill, decision feed, learning, listing matcher, …). Here two Products that
share a SKU inside one account are AMBIGUOUS and are NEVER silently picked; different accounts
are never compared, so the same SKU in two accounts is two different Products.

Matching priority: external_product_id (only when the row actually carries one — today's CSVs do
not) → seller SKU within the account. A product NAME is never an identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

FOUND = "found"
MISSING = "missing"
AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class Resolution:
    status: str                 # FOUND | MISSING | AMBIGUOUS
    product_id: Optional[str]   # set only when FOUND


def _norm_sku(value: Optional[str]) -> Optional[str]:
    s = (value or "").strip().upper()
    return s or None


class AccountProductIndex:
    """In-memory index of ONE account's products, with SKU-ambiguity tracking.

    Build once per import from the account's products, then resolve() each row and add() any
    Product created mid-import so later rows in the same file resolve to it.
    """

    def __init__(self, products: Iterable, account_id: str):
        self.account_id = account_id
        self._by_external: dict[str, str] = {}
        self._by_sku: dict[str, list[str]] = {}
        for p in products:
            if p.marketplace_account_id != account_id:
                continue                      # never compare across accounts
            self._register(p)

    def _register(self, p) -> None:
        ext = getattr(p, "external_product_id", None)
        if ext:
            self._by_external.setdefault(ext, p.id)
        sk = _norm_sku(p.sku)
        if sk:
            self._by_sku.setdefault(sk, []).append(p.id)

    def add(self, product) -> None:
        """Register a Product created during this import."""
        self._register(product)

    def candidates(self, *, sku: Optional[str] = None) -> list[str]:
        """Product ids in this account matching the seller SKU (0, 1, or many). Used to show the
        seller which products a conflict row could be — recomputed within the account, never stored."""
        sk = _norm_sku(sku)
        if sk is None:
            return []
        return list(self._by_sku.get(sk, []))

    def resolve(self, *, external_product_id: Optional[str] = None,
                sku: Optional[str] = None) -> Resolution:
        if external_product_id:
            pid = self._by_external.get(external_product_id)
            if pid is not None:
                return Resolution(FOUND, pid)
        sk = _norm_sku(sku)
        if sk is None:
            return Resolution(MISSING, None)      # empty sku cannot be placed on the spine
        pids = self._by_sku.get(sk, [])
        if len(pids) == 1:
            return Resolution(FOUND, pids[0])
        if len(pids) > 1:
            return Resolution(AMBIGUOUS, None)    # never pick the first
        return Resolution(MISSING, None)
