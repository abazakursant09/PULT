"""The seller's first import, and what they are honestly told afterwards.

Three defects are pinned here, all of them things a brand-new seller hit on day one:

  B3. A finance report was uploaded, the seller was told "PULT анализирует", and the very next
      screen said "Нет данных для анализа" — because producers only ever ran on the scheduler's
      due window (24h for most contours) and `has_data` asks about TODAY's money, not about
      whether a diagnosis exists. Analysis now runs for that seller right after their import, and
      the first screen distinguishes the real states instead of collapsing them into "no data".

  B4. A file with the right name but the wrong columns produced zero rows, no error, an enabled
      confirm button, and a green "Импортировано 0 строк" success screen. Nothing was written and
      the seller believed their report had landed.

  B5. The empty screen promised CSV **or Excel**; the backend rejects everything but .csv.

The rule the tests defend throughout: never claim work is happening when it is not, and never
report success for an import that wrote nothing.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  registers tables
from models.advisory_run import AdvisoryRun
from models.import_record import ImportRecord
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.imported_return import ImportedReturnRow

from tasks.csv_parser import parse_csv
from services.first_run_state import (
    compute_first_run_state, ANALYZING, FAILED, INSUFFICIENT, NO_DATA, READY,
)

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


GOOD_FINANCE = (
    "Дата,Артикул,Выручка,Комиссия,Логистика,Прибыль\n"
    "05.01.2025,SKU-1,1000,100,50,850\n"
    "06.01.2025,SKU-2,2000,200,80,1720\n"
).encode("utf-8")

# Right shape, no `sku` column anywhere — the exact file that used to import "successfully".
NO_SKU_FINANCE = (
    "Дата,Выручка,Комиссия,Логистика,Прибыль\n"
    "05.01.2025,1000,100,50,850\n"
).encode("utf-8")


# ── B4: an import that would write nothing is never confirmable ──────────────

def test_a_good_file_still_parses():
    """Guard rail for everything below: the happy path must be untouched."""
    r = parse_csv(GOOD_FINANCE, "wb", "finance")
    assert r.valid_rows == 2
    assert r.errors == []


def test_a_missing_required_column_is_an_error_not_a_warning():
    """It used to be a warning, which left the import confirmable and wrote nothing."""
    r = parse_csv(NO_SKU_FINANCE, "wb", "finance")
    assert r.errors, "missing sku must block the import"
    assert r.valid_rows == 0


def test_the_error_names_the_missing_column():
    """"Проверьте сопоставление колонок" does not tell the seller what to fix."""
    r = parse_csv(NO_SKU_FINANCE, "wb", "finance")
    assert any("sku" in e for e in r.errors)


def test_rows_that_all_fail_to_parse_are_an_error():
    """Required column present, but nothing survives — still nothing to import."""
    empty_skus = (
        "Дата,Артикул,Выручка,Прибыль\n"
        ",,,\n"
        ",,,\n"
    ).encode("utf-8")
    r = parse_csv(empty_skus, "wb", "finance")
    assert r.valid_rows == 0
    assert r.errors


def test_zero_valid_rows_can_never_report_success():
    """The whole point of B4: no parse result may say 'clean' while importing nothing."""
    for payload in (NO_SKU_FINANCE,):
        r = parse_csv(payload, "wb", "finance")
        assert not (r.errors == [] and r.valid_rows == 0), \
            "a clean result with zero rows is exactly the false success screen"


def test_the_confirm_endpoint_enforces_it_too():
    """A disabled button is a suggestion; the server is where the rule lives.

    Asserted against the source so the guard cannot be deleted from the endpoint while the
    frontend check stays and silently becomes the only one.
    """
    import inspect
    import routers.csv_import as ci
    src = inspect.getsource(ci.confirm_import)
    assert "valid_rows == 0" in src
    assert "422" in src


# ── B3: analysis actually runs, and the screen tells the truth ───────────────

async def _seed_finance(db, user_id, *, days=2, when=None):
    rec = ImportRecord(
        id=str(uuid.uuid4()), user_id=user_id, marketplace="wb", import_type="finance",
        filename="f.csv", file_hash=str(uuid.uuid4()), status="confirmed",
        imported_count=days, confirmed_at=when or datetime.utcnow(),
    )
    db.add(rec)
    for i in range(days):
        db.add(ImportedFinanceRow(
            id=str(uuid.uuid4()), import_id=rec.id, user_id=user_id, marketplace="wb",
            date=f"2025-01-{i + 1:02d}", sku=f"SKU-{i}", revenue=100.0, net_profit=50.0,
        ))
    await db.commit()
    return rec


def _finished_run(user_id, key, status="ok", at=None):
    ts = at or datetime.utcnow()
    return AdvisoryRun(id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), user_id=user_id,
                       producer_key=key, started_at=ts, finished_at=ts, status=status,
                       triggered_by="import")


def test_no_import_at_all_is_no_data():
    async def go():
        db = await _db()
        st = await compute_first_run_state(db, "u1", has_diagnosis=False)
        await db.close()
        return st
    assert _run(go()).state == NO_DATA


def test_a_fresh_import_is_analyzing_not_no_data():
    """THE defect: the seller uploads, lands on the dashboard, and must not be told there is
    no data. Nothing has finished yet, so the honest answer is that the analysis is owed."""
    async def go():
        db = await _db()
        await _seed_finance(db, "u2")
        st = await compute_first_run_state(db, "u2", has_diagnosis=False)
        await db.close()
        return st
    st = _run(go())
    assert st.state == ANALYZING
    assert st.state != NO_DATA


def test_analyzing_is_also_true_while_a_run_is_in_flight():
    async def go():
        db = await _db()
        await _seed_finance(db, "u3")
        db.add(AdvisoryRun(id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), user_id="u3",
                           producer_key="growth", started_at=datetime.utcnow(),
                           status="running", triggered_by="import"))
        await db.commit()
        st = await compute_first_run_state(db, "u3", has_diagnosis=False)
        await db.close()
        return st
    assert _run(go()).state == ANALYZING


def test_analyzing_is_never_claimed_once_the_run_has_finished():
    """"Разбор готовится" must not be shown when nothing is actually running — otherwise it is
    the same lie in a friendlier font."""
    async def go():
        db = await _db()
        await _seed_finance(db, "u4", when=datetime.utcnow() - timedelta(hours=2))
        db.add(_finished_run("u4", "growth"))
        await db.commit()
        st = await compute_first_run_state(db, "u4", has_diagnosis=False)
        await db.close()
        return st
    assert _run(go()).state != ANALYZING


def test_a_diagnosis_outranks_everything():
    async def go():
        db = await _db()
        await _seed_finance(db, "u5")
        st = await compute_first_run_state(db, "u5", has_diagnosis=True)
        await db.close()
        return st
    assert _run(go()).state == READY


def test_historical_data_is_not_treated_as_empty():
    """Rows dated last January are still data. The old screen keyed off today/yesterday money
    and therefore called a historical export 'нет данных'."""
    async def go():
        db = await _db()
        rec = ImportRecord(
            id=str(uuid.uuid4()), user_id="u6", marketplace="wb", import_type="finance",
            filename="old.csv", file_hash=str(uuid.uuid4()), status="confirmed",
            imported_count=1, confirmed_at=datetime.utcnow() - timedelta(days=200),
        )
        db.add(rec)
        db.add(ImportedFinanceRow(id=str(uuid.uuid4()), import_id=rec.id, user_id="u6",
                                  marketplace="wb", date="2024-01-05", sku="OLD",
                                  revenue=10.0, net_profit=5.0))
        db.add(_finished_run("u6", "growth", at=datetime.utcnow() - timedelta(days=199)))
        await db.commit()
        st = await compute_first_run_state(db, "u6", has_diagnosis=False)
        await db.close()
        return st
    st = _run(go())
    assert st.state != NO_DATA
    assert st.finance_days == 1


def test_insufficient_names_the_real_gap():
    """Not a generic apology: the seller is told which data is missing."""
    async def go():
        db = await _db()
        await _seed_finance(db, "u7", days=2, when=datetime.utcnow() - timedelta(hours=1))
        db.add(_finished_run("u7", "growth"))
        await db.commit()
        st = await compute_first_run_state(db, "u7", has_diagnosis=False)
        await db.close()
        return st
    st = _run(go())
    assert st.state == INSUFFICIENT
    assert st.missing, "an insufficient state with no explanation is the old screen again"
    blob = " ".join(st.missing)
    assert "6" in blob                      # the real revenue/money-leak gate
    assert "товар" in blob                  # no products import → stock contours cannot run
    assert "возврат" in blob                # no returns import


def test_the_gap_reflects_what_was_actually_uploaded():
    """Having products must remove the 'загрузите отчёт по товарам' line — the explanation has
    to track reality, or sellers learn to ignore it."""
    async def go():
        db = await _db()
        rec = await _seed_finance(db, "u8", days=2,
                                  when=datetime.utcnow() - timedelta(hours=1))
        db.add(ImportedProductRow(id=str(uuid.uuid4()), import_id=rec.id, user_id="u8",
                                  marketplace="wb", sku="SKU-1", price=10.0, stock=5))
        db.add(ImportedReturnRow(id=str(uuid.uuid4()), import_id=rec.id, user_id="u8",
                                 marketplace="wb", sku="SKU-1", returns_qty=1))
        db.add(_finished_run("u8", "growth"))
        await db.commit()
        st = await compute_first_run_state(db, "u8", has_diagnosis=False)
        await db.close()
        return st
    st = _run(go())
    assert st.has_products is True and st.has_returns is True
    assert st.product_snapshots == 1
    blob = " ".join(st.missing)
    assert "загрузите отчёт по товарам" not in blob.lower()
    assert "возврат" not in blob.lower()
    assert "выгрузк" in blob.lower()        # …but the snapshot gap IS named


def test_a_wholly_failed_analysis_does_not_blame_the_seller():
    async def go():
        db = await _db()
        await _seed_finance(db, "u9", when=datetime.utcnow() - timedelta(hours=1))
        db.add(_finished_run("u9", "growth", status="error"))
        await db.commit()
        st = await compute_first_run_state(db, "u9", has_diagnosis=False)
        await db.close()
        return st
    assert _run(go()).state == FAILED


# ── B3: the trigger itself ──────────────────────────────────────────────────

def test_confirm_queues_analysis_for_the_importing_seller():
    """Wired to BackgroundTasks so the upload response is not held open by the analysis."""
    import inspect
    import routers.csv_import as ci
    src = inspect.getsource(ci.confirm_import)
    assert "background_tasks.add_task(run_producers_for_user" in src
    assert "if counts.imported > 0:" in src           # nothing landed → nothing to analyse


def test_analysis_is_not_queued_when_nothing_was_imported():
    """A run over zero new rows would record work that did not happen and put the seller on
    "разбор готовится" about nothing."""
    import inspect
    import routers.csv_import as ci
    src = inspect.getsource(ci.confirm_import)
    add_at = src.index("background_tasks.add_task")
    guard_at = src.index("if counts.imported > 0:")
    assert guard_at < add_at


def test_the_trigger_runs_only_the_named_seller():
    """One seller's import must never recompute another's diagnosis."""
    import inspect
    from services.advisory_runtime import after_import
    src = inspect.getsource(after_import.run_producers_for_user)
    assert "user_id=user_id" in src
    # the scheduler's all-users enumeration must not appear here
    assert "_active_user_ids" not in src
    assert "run_due_producers" not in src


def test_the_trigger_never_raises_into_the_request():
    """A background task that raises dies silently and the seller waits forever."""
    import services.advisory_runtime.after_import as ai

    class _Boom:
        key = "growth"
        enabled = True

    async def go():
        # No DB, no registry patching needed: the function must swallow whatever happens.
        out = await ai.run_producers_for_user("nobody-at-all")
        return out
    out = _run(go())
    assert isinstance(out, dict) and "ran" in out and "errors" in out


def test_it_uses_the_existing_runtime_and_marks_the_trigger():
    """No second scheduler, no copied cadence logic — run_one is the shared entrypoint, and
    `import` is the triggered_by value RuntimeContext has documented from the start."""
    import inspect
    from services.advisory_runtime import after_import
    src = inspect.getsource(after_import)
    assert "run_one" in src
    assert after_import.TRIGGER == "import"


def test_the_scheduler_tick_is_untouched():
    """The periodic path must keep working exactly as before."""
    import inspect
    from services.advisory_runtime.runtime import AdvisoryRuntime
    src = inspect.getsource(AdvisoryRuntime.run_due_producers)
    assert "slot_budget" in src and "_is_due" in src


def test_confirming_twice_cannot_double_import():
    """Idempotency of the confirm itself — re-posting must be refused, not duplicated."""
    import inspect
    import routers.csv_import as ci
    src = inspect.getsource(ci.confirm_import)
    assert 'rec.status == "confirmed"' in src
    assert "уже подтверждён" in src


# ── B5: the interface promises only what the backend accepts ────────────────

def test_only_csv_is_accepted_by_the_backend():
    import inspect
    import routers.csv_import as ci
    src = inspect.getsource(ci)
    assert ".csv" in src
    assert "Поддерживаются только CSV файлы" in src


@pytest.mark.parametrize("path", [
    "../frontend/app/dashboard/page.tsx",
])
def test_the_dashboard_does_not_promise_excel(path):
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, path), encoding="utf-8") as fh:
        src = fh.read()
    assert "Excel" not in src
    assert "CSV" in src
