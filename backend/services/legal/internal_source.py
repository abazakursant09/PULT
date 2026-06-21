"""
Internal LegalSnapshot source (Legal A3) — frame legal-risk inputs from EXISTING
PULT data. No external/MP API, no legal database, no forecast, no AI.

What PULT stores that is legally relevant (today):
  Product            → product_category, product name (title/brand proxy)
  ImportedProductRow → product title (content proxy)
  marketplace        → always known (passed in)

What PULT does NOT store yet (always missing → those requirements stay
not_evaluated, never assumed compliant):
  certificate_data, offer_terms_data, return_policy_data

Honest degradation:
  * db is None              → LegalDataUnavailable("no_db_context")
  * no subject_ref/sku      → LegalDataUnavailable("insufficient_data")
  * built but nothing yet evaluable → LegalSnapshot(status="not_evaluated_ready")

Never writes a finding or a signal. Never asserts compliance. Marketplace-agnostic:
reads generic models, never a marketplace client.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.product import Product
from models.imported_product import ImportedProductRow

from .snapshot import (
    LegalSnapshot, LegalDataUnavailable, REQUIREMENT_CANDIDATES, REQUIRED_INPUTS,
)

# Canonical marketplace → stored-row aliases (imports may store raw labels).
_ALIASES = {
    "wb": ("wb", "wildberries"), "wildberries": ("wb", "wildberries"),
    "ozon": ("ozon",),
    "yandex": ("yandex", "yandex_market", "ym"), "ym": ("yandex", "yandex_market", "ym"),
}

# Inputs PULT cannot store today — structurally always missing (never "compliant").
_NEVER_STORED = ("certificate_data", "offer_terms_data", "return_policy_data")


def _aliases(mp: Optional[str]) -> tuple[str, ...]:
    key = (mp or "").strip().lower()
    return _ALIASES.get(key, (key,))


async def build_snapshot_from_internal(
    db: AsyncSession, *, seller_id: str, marketplace: str, subject_type: Optional[str] = None,
    subject_ref: Optional[str] = None, sku: Optional[str] = None,
    listing_id: Optional[str] = None, now: Optional[datetime] = None,
):
    """LegalSnapshot framed from internal PULT data, or honest LegalDataUnavailable."""
    if db is None:
        return LegalDataUnavailable(marketplace or "unknown", "no_db_context")

    ref = subject_ref or sku
    if not ref:
        return LegalDataUnavailable(marketplace or "unknown", "insufficient_data",
                                    "subject_ref or sku required")

    aliases = _aliases(marketplace)

    # ── read whatever legally-relevant data PULT already has (never fails hard) ─
    prod = (await db.execute(
        select(Product).where(Product.user_id == seller_id, Product.sku == str(ref)).limit(1)
    )).scalars().first()
    imp = (await db.execute(
        select(ImportedProductRow).where(
            ImportedProductRow.user_id == seller_id,
            ImportedProductRow.marketplace.in_(aliases),
            ImportedProductRow.sku == str(ref)).limit(1)
    )).scalars().first()

    category = prod.category if prod else None
    title_or_brand = (prod.name if prod else None) or (imp.title if imp else None)
    product_text = (imp.title if imp else None) or (prod.name if prod else None)

    # ── honest availability of each named input ───────────────────────────────
    availability = {
        "marketplace": bool(marketplace),
        "product_category": category is not None,
        "product_title_or_brand": title_or_brand is not None,
        "product_text": product_text is not None,
        # inputs PULT cannot store yet — always missing (NOT compliant)
        "certificate_data": False,
        "offer_terms_data": False,
        "return_policy_data": False,
    }
    available_inputs = tuple(k for k, v in availability.items() if v)
    missing_inputs = tuple(k for k, v in availability.items() if not v)

    # ── per-requirement: evaluable later, or not_evaluated (with reason) ──────
    not_evaluated_reasons: dict = {}
    evaluable = 0
    for req in REQUIREMENT_CANDIDATES:
        needed = REQUIRED_INPUTS[req]
        absent = [i for i in needed if not availability.get(i)]
        if absent:
            not_evaluated_reasons[req] = f"missing_inputs: {','.join(absent)}"
        else:
            evaluable += 1

    status = "ready" if evaluable > 0 else "not_evaluated_ready"

    return LegalSnapshot(
        seller_id=seller_id, marketplace=marketplace, subject_type=subject_type,
        subject_ref=ref, sku=sku or (str(ref) if subject_type in ("product", "sku") else None),
        listing_id=listing_id, source="internal",
        snapshot_created_at=now or datetime.utcnow(), status=status,
        available_inputs=available_inputs, missing_inputs=missing_inputs,
        field_availability=availability, requirement_candidates=REQUIREMENT_CANDIDATES,
        not_evaluated_reasons=not_evaluated_reasons,
    )
