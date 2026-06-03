"""
Product Spine backfill — Phase B (fill product_id) + Phase C (coverage report).

Idempotent. Per user:
  1. Build index from existing Products (oldest wins).
  2. PRODUCT rows: resolve; non-empty-sku misses → auto-create Product, link.
  3. FINANCE rows: resolve only (NO auto-create); empty-sku / catalog-miss → NULL.

Run:  python -m scripts.backfill_product_spine          (apply)
      python -m scripts.backfill_product_spine --dry-run (report only, no writes)

Safe to re-run: only fills rows where product_id IS NULL.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from collections import defaultdict

from sqlalchemy import select

from database import AsyncSessionLocal
from models.product import Product
from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow
from services.product_resolver import build_product_index, resolve, resolution_key


async def _run(dry_run: bool) -> None:
    stats: dict[str, int] = defaultdict(int)

    async with AsyncSessionLocal() as db:
        users = (await db.execute(select(ImportedProductRow.user_id).distinct())).scalars().all()
        users += (await db.execute(select(ImportedFinanceRow.user_id).distinct())).scalars().all()
        users = sorted(set(users))

        for uid in users:
            products = (await db.execute(
                select(Product).where(Product.user_id == uid).order_by(Product.created_at)
            )).scalars().all()
            index = build_product_index(products)

            # ── PRODUCT rows (auto-create allowed) ──
            prod_rows = (await db.execute(
                select(ImportedProductRow).where(
                    ImportedProductRow.user_id == uid,
                    ImportedProductRow.product_id.is_(None),
                )
            )).scalars().all()
            for r in prod_rows:
                pid = resolve(index, uid, r.marketplace, r.sku)
                if pid is None:
                    key = resolution_key(uid, r.marketplace, r.sku)
                    if key is None:
                        stats["product_unresolved_empty_sku"] += 1
                        continue
                    pid = str(uuid.uuid4())
                    if not dry_run:
                        db.add(Product(
                            id=pid, user_id=uid, name=(r.title or r.sku),
                            marketplace=r.marketplace, sku=r.sku, price=r.price,
                        ))
                    index[key] = pid
                    stats["product_created"] += 1
                else:
                    stats["product_matched"] += 1
                if not dry_run:
                    r.product_id = pid

            # ── FINANCE rows (resolve only, no create) ──
            fin_rows = (await db.execute(
                select(ImportedFinanceRow).where(
                    ImportedFinanceRow.user_id == uid,
                    ImportedFinanceRow.product_id.is_(None),
                )
            )).scalars().all()
            for r in fin_rows:
                pid = resolve(index, uid, r.marketplace, r.sku)
                if pid is None:
                    if resolution_key(uid, r.marketplace, r.sku) is None:
                        stats["finance_unresolved_empty_sku"] += 1
                    else:
                        stats["finance_unresolved_no_catalog"] += 1
                    continue
                stats["finance_matched"] += 1
                if not dry_run:
                    r.product_id = pid

        if not dry_run:
            await db.commit()

        # ── Phase C coverage report ──
        tot_p = (await db.execute(select(ImportedProductRow))).scalars().all()
        tot_f = (await db.execute(select(ImportedFinanceRow))).scalars().all()
        p_cov = sum(1 for r in tot_p if r.product_id) / len(tot_p) * 100 if tot_p else 100.0
        f_cov = sum(1 for r in tot_f if r.product_id) / len(tot_f) * 100 if tot_f else 100.0

    print("=== Product Spine backfill" + (" [DRY RUN]" if dry_run else "") + " ===")
    for k in sorted(stats):
        print(f"  {k}: {stats[k]}")
    print(f"  coverage product_rows: {p_cov:.1f}%  ({len(tot_p)} rows)")
    print(f"  coverage finance_rows: {f_cov:.1f}%  ({len(tot_f)} rows)")
    print("  NOTE: finance_unresolved_no_catalog rows are EXPECTED to stay NULL")
    print("        (no auto-create from finance). NOT-NULL (Phase D) only if you")
    print("        accept finance rows can be NULL, or after catalog import covers them.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes")
    args = ap.parse_args()
    asyncio.run(_run(args.dry_run))
