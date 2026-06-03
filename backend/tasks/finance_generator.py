# DEPRECATED (Step 2 — Ledger Unification). This produces RANDOM FinancialSnapshot
# rows and is NO LONGER part of the money path. routers/finance.py no longer calls
# it; money now derives from imported_finance_rows (the ledger). Retained only for
# rollback / sample-data generation. Do NOT wire back into any user-facing summary.
import random
from datetime import datetime
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.financial_snapshot import FinancialSnapshot


def _months_back(n: int) -> str:
    """Return 'YYYY-MM' string for n months before the current month."""
    now = datetime.utcnow()
    month = now.month - n
    year  = now.year
    while month <= 0:
        month += 12
        year  -= 1
    return f"{year:04d}-{month:02d}"


async def generate_finance_snapshots(
    product_id: str,
    db: AsyncSession,
    product_price: float | None = None,
) -> list[FinancialSnapshot]:
    # Clear previous snapshots for this product
    await db.execute(
        delete(FinancialSnapshot).where(FinancialSnapshot.product_id == product_id)
    )

    snapshots: list[FinancialSnapshot] = []

    for months_ago in range(2, -1, -1):   # oldest → newest
        period = _months_back(months_ago)

        # Base revenue from product price × estimated units, or pure random
        if product_price and product_price > 0:
            units   = random.randint(40, 180)
            revenue = round(product_price * units * random.uniform(0.85, 1.15), 2)
        else:
            revenue = round(random.uniform(80_000, 600_000), 2)

        marketplace_fee = round(revenue * random.uniform(0.02, 0.05), 2)
        ad_spend        = round(revenue * random.uniform(0.03, 0.08), 2)
        cogs            = round(revenue * random.uniform(0.40, 0.60), 2)
        net_profit      = round(revenue - marketplace_fee - ad_spend - cogs, 2)
        margin_percent  = round(net_profit / revenue * 100, 2) if revenue else 0.0

        snapshots.append(FinancialSnapshot(
            product_id      = product_id,
            period          = period,
            revenue         = revenue,
            marketplace_fee = marketplace_fee,
            ad_spend        = ad_spend,
            cogs            = cogs,
            net_profit      = net_profit,
            margin_percent  = margin_percent,
        ))

    db.add_all(snapshots)
    await db.commit()
    for s in snapshots:
        await db.refresh(s)

    return snapshots
