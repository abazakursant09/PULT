"""An uploaded CSV must not outlive the import it was uploaded for.

The file exists to carry a seller from preview to confirm and nothing else: it is re-parsed at
confirm and deleted, no endpoint serves it back, re-import does not reuse it, history and audit
read the database. Confirm already deleted what it consumed and swept that seller's leftovers —
but only for a seller who confirms. Upload a file, look at the preview, close the tab, and the
file stayed on disk with nothing in the system that would ever remove it.

These tests pin the sweep that closes that, and pin the limits on it: it must delete stale files
belonging to ANY seller, must not touch a file whose import session is still open, and must not be
usable to delete anything outside uploads/imports.
"""
import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest

from routers import csv_import
from routers.csv_import import _ORPHAN_TTL_SECONDS
from tasks import uploads_cleanup
from tasks.uploads_cleanup import run_uploads_cleanup

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


def _sweep() -> int:
    """Force a sweep and return the number of FILES removed.

    `force=True` because these tests are about what the sweep deletes, not about when it decides
    to run — the interval has its own tests below.
    """
    files, _dirs = _run(run_uploads_cleanup(force=True))
    return files


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """A clean upload root per test (overrides the session-wide one from conftest)."""
    root = tmp_path / "imports"
    root.mkdir(parents=True)
    monkeypatch.setattr(csv_import, "_UPLOAD_DIR", root)
    # The "when did we last sweep" stamp is module state; leaving it set would make the next
    # test's result depend on this one's.
    monkeypatch.setattr(uploads_cleanup, "_last_sweep_at", None)
    return root


def _file(root: Path, user: str, *, age_s: float, name: str | None = None) -> Path:
    d = root / user
    d.mkdir(parents=True, exist_ok=True)
    p = d / (name or f"{uuid.uuid4()}.csv")
    p.write_bytes(b"a,b\n1,2\n")
    stamp = time.time() - age_s
    os.utime(p, (stamp, stamp))
    return p


STALE = _ORPHAN_TTL_SECONDS + 60
FRESH = _ORPHAN_TTL_SECONDS - 60


# ── 1-4. The window, applied to everyone ────────────────────────────────────

def test_a_stale_file_is_removed(uploads):
    f = _file(uploads, "seller-1", age_s=STALE)
    assert _sweep() == 1
    assert not f.exists()


def test_another_sellers_stale_file_is_removed_too(uploads):
    """THE defect: cleanup ran only for the seller who happened to confirm something."""
    a = _file(uploads, "seller-1", age_s=STALE)
    b = _file(uploads, "seller-2", age_s=STALE)
    assert _sweep() == 2
    assert not a.exists() and not b.exists()


def test_a_fresh_file_survives(uploads):
    """Deleting inside the window would break preview → confirm for a seller mid-import."""
    f = _file(uploads, "seller-1", age_s=FRESH)
    assert _sweep() == 0
    assert f.exists()


def test_a_fresh_file_of_another_seller_survives(uploads):
    fresh = _file(uploads, "seller-2", age_s=FRESH)
    stale = _file(uploads, "seller-1", age_s=STALE)
    assert _sweep() == 1
    assert fresh.exists() and not stale.exists()


# ── 5-6. It must not overreach ──────────────────────────────────────────────

def test_only_csv_files_are_touched(uploads):
    """The sweep owns uploaded CSVs, not whatever else may share the directory."""
    other = uploads / "seller-1" / "notes.txt"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("x")
    stamp = time.time() - STALE
    os.utime(other, (stamp, stamp))

    _sweep()
    assert other.exists()


def test_cleanup_stays_inside_the_upload_root(uploads, tmp_path):
    """A symlinked seller directory must not walk the sweep out of uploads/imports."""
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "important.csv"
    victim.write_text("not ours")
    stamp = time.time() - STALE
    os.utime(victim, (stamp, stamp))

    try:
        (uploads / "seller-link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")

    _sweep()
    assert victim.exists(), "cleanup followed a symlink out of the upload root"


def test_a_symlinked_file_is_not_followed(uploads, tmp_path):
    """Nor may a symlinked FILE inside a real seller directory cost us its target."""
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "important.csv"
    victim.write_text("not ours")

    d = uploads / "seller-1"
    d.mkdir(parents=True)
    try:
        link = d / "link.csv"
        link.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    stamp = time.time() - STALE
    os.utime(link, (stamp, stamp), follow_symlinks=False)

    _sweep()
    assert victim.exists(), "cleanup deleted a symlink target"


# ── 7-8. It must be robust ──────────────────────────────────────────────────

def test_one_unremovable_file_does_not_stop_the_sweep(uploads, monkeypatch):
    """A confirm may delete a file between our check and our unlink, and a file may be locked.
    Either way the rest of the sweep must still happen."""
    _file(uploads, "seller-1", age_s=STALE, name="aaa.csv")
    _file(uploads, "seller-2", age_s=STALE, name="bbb.csv")
    _file(uploads, "seller-3", age_s=STALE, name="ccc.csv")

    real_unlink = Path.unlink

    def flaky(self, *a, **kw):
        if self.name == "bbb.csv":
            raise OSError("locked by another process")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", flaky)
    removed = _sweep()

    assert removed == 2                      # the other two still went
    assert not (uploads / "seller-1" / "aaa.csv").exists()
    assert not (uploads / "seller-3" / "ccc.csv").exists()


def test_a_vanished_file_is_not_an_error(uploads, monkeypatch):
    _file(uploads, "seller-1", age_s=STALE, name="gone.csv")
    real_unlink = Path.unlink

    def racing(self, *a, **kw):
        real_unlink(self, *a, **kw)
        raise FileNotFoundError(str(self))    # as if a confirm got there first

    monkeypatch.setattr(Path, "unlink", racing)
    _sweep()                                  # must not raise


# ── 9. Housekeeping ─────────────────────────────────────────────────────────

def test_an_emptied_directory_is_removed(uploads):
    _file(uploads, "seller-1", age_s=STALE)
    _sweep()
    assert not (uploads / "seller-1").exists()


def test_a_directory_that_still_has_files_is_kept(uploads):
    _file(uploads, "seller-1", age_s=STALE)
    _file(uploads, "seller-1", age_s=FRESH)
    _sweep()
    assert (uploads / "seller-1").is_dir()


def test_a_missing_upload_root_is_not_an_error(uploads):
    for child in uploads.iterdir():
        child.rmdir()
    uploads.rmdir()
    assert _sweep() == 0


# ── 10. Account deletion ────────────────────────────────────────────────────

def test_account_deletion_removes_only_that_sellers_directory(uploads):
    from routers.referrals import _remove_upload_dir

    mine = _file(uploads, "seller-1", age_s=FRESH)
    theirs = _file(uploads, "seller-2", age_s=FRESH)

    _remove_upload_dir("seller-1")

    assert not mine.exists() and not (uploads / "seller-1").exists()
    assert theirs.exists(), "another seller's uploads were deleted"


def test_deleting_an_account_with_no_uploads_is_not_an_error(uploads):
    from routers.referrals import _remove_upload_dir
    _remove_upload_dir("never-uploaded-anything")     # must not raise


def test_account_deletion_does_not_follow_a_symlinked_directory(uploads, tmp_path):
    from routers.referrals import _remove_upload_dir

    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "important.csv"
    victim.write_text("not ours")

    try:
        (uploads / "seller-link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")

    _remove_upload_dir("seller-link")
    assert victim.exists(), "account deletion followed a symlink out of the upload root"


# ── 11. The flow the file exists for still works, and ends with it gone ─────

def _client(db, uid):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from database import get_db
    from dependencies import get_current_user
    from rate_limit import limit_import

    async def _override_db():
        yield db

    app = FastAPI()
    app.include_router(csv_import.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[limit_import] = lambda: None
    return TestClient(app)


async def _new_db():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from database import Base
    import models  # noqa: F401  registers tables

    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


_CSV = (
    "дата,артикул,название,выручка,комиссия,логистика,реклама,чистая прибыль,количество\n"
    "2026-07-01,ART-1,Товар,1000,100,50,30,820,3\n"
).encode("utf-8")


async def _seed_store(db, uid):
    """A wildberries cabinet + active primary store (PULT-LAUNCH-1.4.2 upload needs one)."""
    import uuid as _uuid
    from models.marketplace_account import MarketplaceAccount
    from models.marketplace_store import MarketplaceStore
    from models.workspace import Workspace
    ws = str(_uuid.uuid4()); acc = str(_uuid.uuid4()); sid = str(_uuid.uuid4())
    db.add(Workspace(id=ws, owner_user_id=uid))
    db.add(MarketplaceAccount(id=acc, workspace_id=ws, marketplace="wildberries",
                              identity_status="unverified", label="K"))
    db.add(MarketplaceStore(id=sid, marketplace_account_id=acc, marketplace="wildberries",
                            store_key="primary", label="S", source="manual", status="active"))
    await db.commit()
    return sid


def test_preview_then_confirm_still_works_and_leaves_no_file(uploads):
    """The whole reason the file exists. It must survive long enough to be confirmed — and must
    not survive the confirm itself."""
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    c = _client(db, uid)
    sid = _run(_seed_store(db, uid))

    up = c.post("/api/import/upload",
                files={"file": ("finance.csv", _CSV, "text/csv")},
                data={"marketplace_store_id": sid, "import_type": "finance"})
    assert up.status_code == 200, up.text

    written = list((uploads / uid).glob("*.csv"))
    assert len(written) == 1, "upload did not store a file to confirm from"

    # A sweep running while the seller is still deciding must not take it away.
    assert _sweep() == 0
    assert written[0].exists()

    cf = c.post(f"/api/import/{up.json()['import_id']}/confirm", json={"mode": "new"})
    assert cf.status_code == 200, cf.text
    assert cf.json()["imported_count"] == 1

    # Consumed and gone — no sweep required.
    assert not written[0].exists()
    assert not list((uploads / uid).glob("*.csv"))


def test_an_abandoned_preview_is_eventually_swept(uploads):
    """Upload, never confirm, walk away. Nothing else in the system would ever remove this."""
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    c = _client(db, uid)
    sid = _run(_seed_store(db, uid))

    up = c.post("/api/import/upload",
                files={"file": ("finance.csv", _CSV, "text/csv")},
                data={"marketplace_store_id": sid, "import_type": "finance"})
    assert up.status_code == 200, up.text
    orphan = next((uploads / uid).glob("*.csv"))

    stamp = time.time() - STALE
    os.utime(orphan, (stamp, stamp))

    assert _sweep() == 1
    assert not orphan.exists()
    assert not (uploads / uid).exists()          # and the empty directory goes too


# ── 12. Logs must not carry identifiers ─────────────────────────────────────

def test_cleanup_logs_no_filename_user_id_or_path(uploads, caplog, monkeypatch):
    """A filename here is a seller's upload and a directory name is their user id. Neither belongs
    in a log line, and neither does the path that gives both away."""
    import logging

    secret_user = "user-a1b2c3-do-not-log"
    secret_file = "secret-report-9f8e7d.csv"
    _file(uploads, secret_user, age_s=STALE, name=secret_file)

    # Also drive the two warning paths: an unexpected symlink and an unremovable file.
    real_unlink = Path.unlink

    def flaky(self, *a, **kw):
        if self.name == "locked-8h7g6f.csv":
            raise OSError("locked")
        return real_unlink(self, *a, **kw)

    _file(uploads, "user-locked-z9y8x7", age_s=STALE, name="locked-8h7g6f.csv")
    monkeypatch.setattr(Path, "unlink", flaky)

    lg = logging.getLogger("tasks.uploads_cleanup")
    prev_disabled, prev_level = lg.disabled, lg.level
    lg.disabled = False
    lg.setLevel(logging.DEBUG)
    try:
        with caplog.at_level(logging.DEBUG, logger="tasks.uploads_cleanup"):
            _sweep()
    finally:
        lg.disabled, lg.level = prev_disabled, prev_level

    blob = " ".join(r.getMessage() for r in caplog.records)
    assert secret_file not in blob, "a seller's filename reached the log"
    assert secret_user not in blob, "a user id reached the log"
    assert "user-locked-z9y8x7" not in blob
    assert "locked-8h7g6f.csv" not in blob
    assert str(uploads) not in blob, "the full upload path reached the log"


# ── 13. It runs on its own interval, not on every tick ──────────────────────

def test_two_ticks_in_a_row_sweep_only_once(uploads, monkeypatch):
    """The scheduler ticks every minute. Scanning every seller's directory that often is waste,
    so the sweep decides for itself when its time has come."""
    calls = []
    real = uploads_cleanup._sweep_now
    monkeypatch.setattr(uploads_cleanup, "_sweep_now",
                        lambda: (calls.append(1), real())[1])

    _file(uploads, "seller-1", age_s=STALE)
    _run(run_uploads_cleanup())          # first tick after start: sweeps immediately
    _run(run_uploads_cleanup())          # second tick, moments later: must not sweep again

    assert len(calls) == 1


def test_the_sweep_runs_again_once_the_interval_has_passed(uploads, monkeypatch):
    calls = []
    real = uploads_cleanup._sweep_now
    monkeypatch.setattr(uploads_cleanup, "_sweep_now",
                        lambda: (calls.append(1), real())[1])

    _run(run_uploads_cleanup())
    assert len(calls) == 1

    # Move the last-sweep stamp into the past rather than waiting a real quarter of an hour.
    monkeypatch.setattr(uploads_cleanup, "_last_sweep_at",
                        time.monotonic() - uploads_cleanup._SWEEP_INTERVAL_SECONDS - 1)
    _run(run_uploads_cleanup())
    assert len(calls) == 2


def test_the_first_tick_after_a_restart_sweeps_immediately(uploads):
    """`None` means "never ran" — a restart must not buy an orphan another 15 minutes."""
    assert uploads_cleanup._last_sweep_at is None
    _file(uploads, "seller-1", age_s=STALE)
    files, _ = _run(run_uploads_cleanup())
    assert files == 1


def test_the_walk_does_not_run_on_the_event_loop(uploads, monkeypatch):
    """Pure blocking I/O on the loop stalls every API call for the length of the scan, and this
    backend serves requests from a single worker."""
    used = []
    real_to_thread = asyncio.to_thread

    async def spy(fn, *a, **kw):
        used.append(fn.__name__)
        return await real_to_thread(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    _run(run_uploads_cleanup(force=True))

    assert used == ["_sweep_now"], "the filesystem walk was not handed to a worker thread"


# ── 14. confirm racing the sweep answers 400, not 500 ───────────────────────

def test_confirm_answers_400_when_the_file_vanishes_before_the_read(uploads, monkeypatch):
    """The existence check is not a guarantee: the sweep can delete the file between that check
    and the read. The seller must be told to upload it again, not shown a 500."""
    db = _run(_new_db())
    uid = str(uuid.uuid4())
    c = _client(db, uid)
    sid = _run(_seed_store(db, uid))

    up = c.post("/api/import/upload",
                files={"file": ("finance.csv", _CSV, "text/csv")},
                data={"marketplace_store_id": sid, "import_type": "finance"})
    assert up.status_code == 200, up.text
    stored = next((uploads / uid).glob("*.csv"))

    # os.path.exists still says yes; the file is gone by the time open() runs.
    real_open = open

    def vanishing(path, *a, **kw):
        if str(path) == str(stored):
            stored.unlink(missing_ok=True)
            raise FileNotFoundError(str(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", vanishing)
    cf = c.post(f"/api/import/{up.json()['import_id']}/confirm", json={"mode": "new"})

    assert cf.status_code == 400
    assert cf.json()["detail"] == "Временный файл недоступен. Загрузите файл заново."
    # No traceback and no internal path leak to the caller.
    body = cf.text
    assert "Traceback" not in body and "FileNotFoundError" not in body
    assert str(uploads) not in body


# ── 15. The retention window is one number, not two ─────────────────────────

def test_confirm_and_the_sweep_share_one_retention_window():
    """Two copies of this number would drift, and the shorter one would delete a file a seller
    was still allowed to confirm."""
    import inspect
    src = inspect.getsource(csv_import)
    assert "_ORPHAN_TTL_SECONDS = 3600" in src
    # the confirm path must use the constant, not a second literal
    confirm_src = src[src.index("async def confirm_import"):]
    assert "> _ORPHAN_TTL_SECONDS" in confirm_src
    assert "> 3600" not in confirm_src
