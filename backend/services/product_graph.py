"""
Product Graph reader (Doctrine §3/§7) — СТРОГО read-only.

Собирает дерево PhysicalProduct → ProductListing[] + Decision[] для одного юзера.
Никаких записей. Один проход листингов + один проход решений (без N+1):
решения раскладываются по physical_product_id в памяти.

Вызывающий владеет сессией (как finance_aggregator) → юнит-тестируемо на
временной sqlite без app/auth.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.decision import Decision


def _atom_to_dict(pp: PhysicalProduct, decisions: list[Decision]) -> dict:
    listings = sorted(pp.listings, key=lambda l: (l.marketplace, l.external_id))
    marketplaces = sorted({l.marketplace for l in listings})
    return {
        "id": pp.id,
        "title": pp.title,
        "barcode": pp.barcode,
        "seller_sku": pp.seller_sku,
        "brand": pp.brand,
        "cogs": pp.cogs,
        "cogs_source": pp.cogs_source,
        "trademark_status": pp.trademark_status,
        "listings": [
            {
                "id": l.id,
                "marketplace": l.marketplace,
                "external_id": l.external_id,
                "title": l.title,
                "match_method": l.match_method,
                "match_confidence": l.match_confidence,
                "confirmed": bool(l.confirmed),
            }
            for l in listings
        ],
        "decisions": [
            {
                "id": d.id,
                "listing_id": d.listing_id,
                "problem": d.problem,
                "severity": d.severity,
                "status": d.status,
                "action": d.action,
                "pnl_impact": d.pnl_impact,
                "pnl_level": d.pnl_level,
            }
            for d in sorted(decisions, key=lambda d: d.id)
        ],
        "listing_count": len(listings),
        "marketplaces": marketplaces,
        "needs_review": any(not l.confirmed for l in listings),
    }


async def _decisions_by_atom(user_id: str, db) -> dict[str, list[Decision]]:
    rows = (await db.execute(
        select(Decision).where(Decision.user_id == user_id)
    )).scalars().all()
    out: dict[str, list[Decision]] = defaultdict(list)
    for d in rows:
        if d.physical_product_id is not None:
            out[d.physical_product_id].append(d)
    return out


async def get_product_graph(user_id: str, db) -> dict:
    """Полное дерево + summary. Детерминированный порядок (title, id)."""
    atoms_rows = (await db.execute(
        select(PhysicalProduct)
        .where(PhysicalProduct.user_id == user_id)
        .options(selectinload(PhysicalProduct.listings))
        .order_by(PhysicalProduct.title, PhysicalProduct.id)
    )).scalars().all()

    dec_map = await _decisions_by_atom(user_id, db)
    atoms = [_atom_to_dict(pp, dec_map.get(pp.id, [])) for pp in atoms_rows]

    n_listings = sum(a["listing_count"] for a in atoms)
    n_unconfirmed = sum(1 for a in atoms for l in a["listings"] if not l["confirmed"])
    n_decisions = sum(len(a["decisions"]) for a in atoms)
    marketplaces = sorted({mp for a in atoms for mp in a["marketplaces"]})

    return {
        "summary": {
            "atoms": len(atoms),
            "listings": n_listings,
            "decisions": n_decisions,
            "unconfirmed_listings": n_unconfirmed,
            "marketplaces": marketplaces,
        },
        "atoms": atoms,
    }


async def get_atom(user_id: str, physical_product_id: str, db) -> Optional[dict]:
    """Один атом со scope по user. None если не найден / чужой."""
    pp = (await db.execute(
        select(PhysicalProduct)
        .where(
            PhysicalProduct.id == physical_product_id,
            PhysicalProduct.user_id == user_id,
        )
        .options(selectinload(PhysicalProduct.listings))
    )).scalar_one_or_none()
    if pp is None:
        return None
    dec_map = await _decisions_by_atom(user_id, db)
    return _atom_to_dict(pp, dec_map.get(pp.id, []))
