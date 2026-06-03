"""
Step 2 characterization — imported_finance_rows is the ONE money truth.

Asserts:
  * finance summary == whole-ledger total (strip cannot drift from breakdown)
  * per-product = ledger sums; unassigned (product_id NULL) is surfaced
  * avg_margin is the WEIGHTED aggregate (Σnet/Σrev)
  * random FinancialSnapshot rows are IGNORED by the money path
  * monthly rollup breakdown sums to the real ledger net (residual cogs identity)
  * routers/finance.py never references FinancialSnapshot (no second source)

Self-contained: own temp sqlite engine, no app/auth/fixtures needed.
"""
import asyncio
import os
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

import models  # noqa: F401 — register all mappers
from database import Base
from models.product import Product
from models.imported_finance import ImportedFinanceRow
from models.financial_snapshot import FinancialSnapshot
from services.finance_aggregator import summary_by_product, product_monthly_rollup


def _fin(**kw):
    base = dict(import_id="imp", marketplace="wb", commission=0.0,
                logistics=0.0, ad_spend=0.0, quantity=0)
    base.update(kw)
    return ImportedFinanceRow(**base)


async def _main():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        uid = "u1"
        async with Session() as db:
            db.add(Product(id="p1", user_id=uid, name="A", marketplace="wb", sku="A"))
            db.add(Product(id="p2", user_id=uid, name="B", marketplace="wb", sku="B"))
            # resolved ledger
            db.add(_fin(id="f1", user_id=uid, date="2026-01-15", sku="A",
                        revenue=1000, commission=100, ad_spend=80, net_profit=300, product_id="p1"))
            db.add(_fin(id="f2", user_id=uid, date="2026-02-10", sku="A",
                        revenue=2000, commission=200, ad_spend=160, net_profit=500, product_id="p1"))
            db.add(_fin(id="f3", user_id=uid, date="2026-01-20", sku="B",
                        revenue=400, net_profit=-100, product_id="p2"))
            # unassigned (no catalog link)
            db.add(_fin(id="f4", user_id=uid, date="2026-01-22", sku="Z",
                        revenue=200, net_profit=70, product_id=None))
            # RANDOM snapshot — must be ignored by the money path
            db.add(FinancialSnapshot(id="s1", product_id="p1", period="2026-01",
                                     revenue=999999, marketplace_fee=1, ad_spend=1,
                                     cogs=1, net_profit=888888, margin_percent=50))
            await db.commit()

            items, totals = await summary_by_product(uid, db)

            ledger_net = 300 + 500 - 100 + 70   # 770
            ledger_rev = 1000 + 2000 + 400 + 200  # 3600
            assert totals["net_profit"] == ledger_net, totals
            assert totals["revenue"] == ledger_rev, totals
            # strip (Σ items) == whole-ledger total — no drift
            assert round(sum(i["total_net_profit"] for i in items), 2) == ledger_net
            assert round(sum(i["total_revenue"] for i in items), 2) == ledger_rev
            # random snapshot ignored
            assert all(i["total_net_profit"] != 888888 for i in items)
            assert totals["net_profit"] != 888888
            # unassigned surfaced
            assert any(i["product_id"] == "__unassigned__" for i in items)
            # weighted margin p1 = (300+500)/(1000+2000)*100
            p1 = next(i for i in items if i["product_id"] == "p1")
            assert p1["avg_margin_percent"] == round(800 / 3000 * 100, 2)
            # monthly rollup residual identity + periods
            roll = await product_monthly_rollup("p1", uid, db)
            assert {r["period"] for r in roll} == {"2026-01", "2026-02"}
            for r in roll:
                lhs = round(r["revenue"] - r["cogs"] - r["marketplace_fee"] - r["ad_spend"], 2)
                assert lhs == round(r["net_profit"], 2), r
    finally:
        await engine.dispose()
        os.unlink(path)


def test_ledger_is_single_money_truth():
    asyncio.run(_main())


def test_finance_router_has_no_financial_snapshot_dependency():
    src = (Path(__file__).resolve().parent.parent / "routers" / "finance.py").read_text(encoding="utf-8")
    # FinancialSnapshotOut (DTO) is fine; the random SOURCE (model + generator) must be gone.
    assert "models.financial_snapshot" not in src, "finance router must not read the FinancialSnapshot model"
    assert "finance_generator" not in src, "finance router must not call the random generator"
