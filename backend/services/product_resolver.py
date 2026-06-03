"""
Product Spine resolver — maps an imported row to a canonical Product.id.

Resolution key = (user_id, normalized_marketplace, normalized_sku).

Pure / deterministic: normalization + in-memory index + lookup. NO database
access here (callers own the session) so it is unit-testable and reusable by
both the backfill script and the live import pipeline. Product CREATION is the
caller's decision — this module only resolves against a provided index.
"""
from __future__ import annotations

from typing import Iterable, Optional

# Canonical marketplace slugs. Free-string values from imports / products are
# squashed to these so "Wildberries" / "ВБ" / "wb" all resolve to one product.
_MP_CANON: dict[str, str] = {
    "wb": "wb", "wildberries": "wb", "вб": "wb", "вайлдберриз": "wb",
    "ozon": "ozon", "озон": "ozon", "oz": "ozon",
    "yandex": "yandex", "yandexmarket": "yandex", "ям": "yandex", "яндекс": "yandex",
    "megamarket": "megamarket", "мегамаркет": "megamarket", "mm": "megamarket",
}


def normalize_marketplace(value: Optional[str]) -> str:
    s = (value or "").strip().lower()
    return _MP_CANON.get(s, s)


def normalize_sku(value: Optional[str]) -> Optional[str]:
    """Empty / whitespace sku → None (UNRESOLVABLE — never matched or created)."""
    s = (value or "").strip().upper()
    return s or None


def resolution_key(user_id: str, marketplace: Optional[str], sku: Optional[str]) -> Optional[tuple[str, str, str]]:
    """Full key or None when the sku is empty (cannot be placed on the spine)."""
    sku_n = normalize_sku(sku)
    if sku_n is None:
        return None
    return (str(user_id), normalize_marketplace(marketplace), sku_n)


def build_product_index(products: Iterable) -> dict[tuple[str, str, str], str]:
    """
    Build {(user_id, mp_norm, sku_norm): product_id} from Product rows.
    First row wins on collision (caller orders by created_at → oldest wins;
    duplicates are silently de-duped, matching the deterministic-pick rule).
    Products with an empty sku are skipped (cannot be a resolution target).
    """
    index: dict[tuple[str, str, str], str] = {}
    for p in products:
        key = resolution_key(p.user_id, p.marketplace, p.sku)
        if key is None:
            continue
        index.setdefault(key, p.id)
    return index


def resolve(index: dict[tuple[str, str, str], str], user_id: str,
            marketplace: Optional[str], sku: Optional[str]) -> Optional[str]:
    """Return product_id for the row, or None if empty-sku / no match."""
    key = resolution_key(user_id, marketplace, sku)
    if key is None:
        return None
    return index.get(key)
