"""What the seller's very first screen is allowed to claim, derived only from evidence.

The dashboard used to ask two yes/no questions — are there cards, is there data for today — and
collapse everything else into "Нет данных для анализа". A seller who had just uploaded a historical
report matched neither: no producer had run yet, and their rows were not dated today. They were
told, one screen after "PULT анализирует", that there was nothing to analyse.

This module answers a sharper question with five distinct states, every one of them backed by a row
that exists rather than by a guess:

    no_data      — nothing has been imported at all
    analyzing    — data landed and the analysis for it has not finished YET
    ready        — a diagnosis exists
    insufficient — the analysis ran to completion and could not conclude anything
    failed       — every producer in the latest run errored

`analyzing` is deliberately evidence-bound: it means an AdvisoryRun row is in flight, or the newest
confirmed import is newer than the newest finished run. It is never claimed just because an import
happened, and no completion time is promised — the runtime guarantees none.

`insufficient` must SAY WHAT IS MISSING. The gaps below mirror the real gates the producers apply,
so the seller is told the actual reason, not a generic apology.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.advisory_run import AdvisoryRun
from models.import_record import ImportRecord
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.imported_return import ImportedReturnRow

NO_DATA = "no_data"
ANALYZING = "analyzing"
READY = "ready"
INSUFFICIENT = "insufficient"
FAILED = "failed"

# Thresholds the diagnosis producers actually enforce. Kept here so the seller-facing explanation
# cannot drift from the gate it describes; each is named next to the contour that owns it.
_REVENUE_DAYS = 6          # revenue trajectory + money leak
_STOCK_DAYS = 3            # supply runway + overstock
_PRODUCT_SNAPSHOTS = 3     # price erosion / rating / review velocity compare dated snapshots


@dataclass(frozen=True)
class FirstRunState:
    state: str
    finance_days: int = 0
    product_snapshots: int = 0
    has_products: bool = False
    has_returns: bool = False
    missing: list[str] = field(default_factory=list)


async def _count_distinct_finance_days(db: AsyncSession, user_id: str) -> int:
    return int((await db.execute(
        select(func.count(distinct(ImportedFinanceRow.date)))
        .where(ImportedFinanceRow.user_id == user_id,
               ImportedFinanceRow.date.isnot(None))
    )).scalar() or 0)


async def _count_product_snapshots(db: AsyncSession, user_id: str) -> int:
    """One confirmed products import = one snapshot.

    ImportedProductRow carries no date of its own, so the series is ordered by import — which is
    exactly what the price-erosion / rating / review-velocity sources do. Counting distinct
    import_ids therefore counts the same snapshots those producers will see.
    """
    return int((await db.execute(
        select(func.count(distinct(ImportedProductRow.import_id)))
        .where(ImportedProductRow.user_id == user_id)
    )).scalar() or 0)


async def _has_rows(db: AsyncSession, model, user_id: str) -> bool:
    return (await db.execute(
        select(model.id).where(model.user_id == user_id).limit(1)
    )).scalar() is not None


def _describe_gaps(finance_days: int, product_snapshots: int,
                   has_products: bool, has_returns: bool) -> list[str]:
    """Name every gap between what the seller has uploaded and what a contour needs.

    Phrased as what to DO, because "недостаточно данных" on its own leaves the seller with no next
    step. No promise is made that filling a gap guarantees a diagnosis — only that without it the
    contour cannot run at all.
    """
    gaps: list[str] = []
    if finance_days < _REVENUE_DAYS:
        gaps.append(
            f"Для разбора выручки и потерь нужны данные минимум за {_REVENUE_DAYS} разных дней — "
            f"сейчас в отчёте {finance_days}. Загрузите выгрузку за более длинный период."
        )
    if not has_products:
        gaps.append(
            "Чтобы считать запасы, затоваривание и цены, загрузите отчёт по товарам "
            "(остатки и цены)."
        )
    elif product_snapshots < _PRODUCT_SNAPSHOTS:
        gaps.append(
            f"Изменение цен и рейтинга видно при сравнении нескольких выгрузок по товарам: "
            f"загружено {product_snapshots} из {_PRODUCT_SNAPSHOTS}. "
            f"Загружайте отчёт по товарам регулярно."
        )
    elif finance_days < _STOCK_DAYS:
        gaps.append(
            f"Для расчёта запаса нужны продажи минимум за {_STOCK_DAYS} дня — "
            f"сейчас {finance_days}."
        )
    if not has_returns:
        gaps.append("Чтобы видеть проблемы с возвратами, загрузите отчёт по возвратам.")
    return gaps


async def compute_first_run_state(db: AsyncSession, user_id: str, *,
                                  has_diagnosis: bool) -> FirstRunState:
    """Resolve the seller's real state. `has_diagnosis` is the caller's card check.

    A diagnosis outranks everything: if one exists the seller is shown it, whatever else is or is
    not still running.
    """
    if has_diagnosis:
        return FirstRunState(state=READY)

    finance_days = await _count_distinct_finance_days(db, user_id)
    has_finance = finance_days > 0 or await _has_rows(db, ImportedFinanceRow, user_id)
    if not has_finance:
        # Nothing imported — and note this is the ONLY state that may tell the seller to upload,
        # which is why the old screen was wrong for everyone else.
        return FirstRunState(state=NO_DATA)

    in_flight = (await db.execute(
        select(AdvisoryRun.id).where(AdvisoryRun.user_id == user_id,
                                     AdvisoryRun.status == "running").limit(1)
    )).scalar() is not None

    last_finished = (await db.execute(
        select(func.max(AdvisoryRun.finished_at)).where(AdvisoryRun.user_id == user_id)
    )).scalar()

    last_import = (await db.execute(
        select(func.max(ImportRecord.confirmed_at)).where(
            ImportRecord.user_id == user_id, ImportRecord.status == "confirmed")
    )).scalar()

    # Data newer than the newest completed analysis means the analysis for THIS import is still
    # owed. Together with the in-flight check this covers the gap between the import committing
    # and the background run recording its first row.
    unanalysed = last_import is not None and (last_finished is None or last_import > last_finished)

    if in_flight or unanalysed:
        return FirstRunState(state=ANALYZING, finance_days=finance_days)

    product_snapshots = await _count_product_snapshots(db, user_id)
    has_products = product_snapshots > 0
    has_returns = await _has_rows(db, ImportedReturnRow, user_id)

    if last_finished is not None:
        errored = (await db.execute(
            select(func.count(AdvisoryRun.id)).where(AdvisoryRun.user_id == user_id,
                                                     AdvisoryRun.status == "error")
        )).scalar() or 0
        succeeded = (await db.execute(
            select(func.count(AdvisoryRun.id)).where(AdvisoryRun.user_id == user_id,
                                                     AdvisoryRun.status == "ok")
        )).scalar() or 0
        if errored and not succeeded:
            # Every producer failed. Saying "недостаточно данных" here would blame the seller for
            # our own breakage.
            return FirstRunState(state=FAILED, finance_days=finance_days,
                                 product_snapshots=product_snapshots,
                                 has_products=has_products, has_returns=has_returns)

    return FirstRunState(
        state=INSUFFICIENT,
        finance_days=finance_days,
        product_snapshots=product_snapshots,
        has_products=has_products,
        has_returns=has_returns,
        missing=_describe_gaps(finance_days, product_snapshots, has_products, has_returns),
    )
