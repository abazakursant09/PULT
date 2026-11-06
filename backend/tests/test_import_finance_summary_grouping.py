"""Finance summary: PostgreSQL-safe grouping and unchanged SKU/owner totals.

SQLite exercises actual query results; compiling the captured Selects with the
asyncpg dialect catches parameter identities that SQLite's GROUP BY accepts.
The native PostgreSQL route composition remains a separate executed audit.
"""
import asyncio
import re
from types import SimpleNamespace

from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.imported_finance import ImportedFinanceRow
from models.import_record import ImportRecord
from routers.csv_import import finance_summary


def _summary(uid="owner-a"):
    async def execute():
        statements = []

        class CaptureSession(AsyncSession):
            async def execute(self, statement, *args, **kwargs):
                statements.append(statement)
                return await super().execute(statement, *args, **kwargs)

        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=CaptureSession, expire_on_commit=False)
            async with factory() as db:
                db.add(ImportRecord(id="summary-import", user_id="owner-a", marketplace="wb",
                                    import_type="finance", filename="synthetic.csv", file_hash="0" * 64,
                                    status="confirmed"))
                for i, (owner, market, day, sku, title, revenue, profit) in enumerate([
                    ("owner-a", "wb", "2026-01-15", "SAME", "Alpha", 100, 80),
                    ("owner-a", "wb", "2026-02-15", "SAME", "Zulu", 200, 160),
                    ("owner-a", "ozon", "2026-02-28", "SAME", None, 50, 20),
                    ("owner-a", "wb", None, "ALT", None, 30, 10),
                    ("owner-b", "wb", "2026-03-15", "SAME", "ZZZZ-FOREIGN", 9999, 8888),
                ]):
                    db.add(ImportedFinanceRow(id=f"summary-row-{i}", import_id="summary-import",
                        user_id=owner, marketplace=market, date=day, sku=sku, title=title,
                        revenue=revenue, net_profit=profit, commission=0, logistics=0, ad_spend=0, quantity=1))
                await db.commit()
                result = await finance_summary(db=db, user=SimpleNamespace(id=uid))
                return result, statements
        finally:
            await engine.dispose()

    return asyncio.run(execute())


def test_postgresql_month_grouping_reuses_identical_parameter_positions():
    _, statements = _summary()
    period = next(stmt for stmt in statements if "period" in stmt.selected_columns.keys())
    compiled = str(period.compile(dialect=asyncpg.dialect()))
    expressions = re.findall(r"substr\([^)]*\)", compiled)
    assert len(expressions) == 3
    assert expressions[0] == expressions[1] == expressions[2], compiled


def test_periods_and_totals_remain_owner_scoped_with_missing_date():
    result, _ = _summary()
    assert result.has_data is True and result.row_count == 4
    assert (result.total_revenue, result.total_profit) == (380, 270)
    assert [(p["period"], p["revenue"], p["profit"]) for p in result.by_period] == [
        ("2026-01", 100, 80), ("2026-02", 250, 180)]


def test_same_sku_title_changes_do_not_split_or_change_money_totals():
    result, _ = _summary()
    rows = [r for r in result.by_product if r["sku"] == "SAME" and r["marketplace"] == "wb"]
    assert len(rows) == 1
    assert rows[0]["revenue"] == 300 and rows[0]["profit"] == 240
    assert rows[0]["sales"] == 2 and rows[0]["title"] == "Zulu"
    assert "ZZZZ-FOREIGN" not in str(result.model_dump())


def test_product_title_is_aggregated_without_extending_the_sku_group_key():
    _, statements = _summary()
    products = next(stmt for stmt in statements if "sales" in stmt.selected_columns.keys())
    compiled = str(products.compile(dialect=asyncpg.dialect()))
    assert "max(imported_finance_rows.title) AS title" in compiled
    assert len(products._group_by_clauses) == 2


def test_all_null_titles_still_fall_back_to_sku():
    result, _ = _summary()
    null_named = [r for r in result.by_product if r["marketplace"] == "ozon" or r["sku"] == "ALT"]
    assert len(null_named) == 2
    assert all(r["title"] == r["sku"] for r in null_named)


def test_empty_owner_stays_empty_without_grouped_queries():
    result, statements = _summary("owner-empty")
    assert result.has_data is False and result.row_count == 0
    assert result.by_product == result.by_period == []
    assert len(statements) == 1
