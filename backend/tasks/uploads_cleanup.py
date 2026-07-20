"""Sweep uploaded CSVs that were never confirmed.

An uploaded file exists for one reason: to carry a seller from preview to confirm, where it is
re-parsed and then deleted (routers/csv_import.py). Nothing reads it afterwards — the seller
cannot download it, re-import does not reuse it, and history and audit both read the database.
So the file is waste the moment its import session ends, and waste holding a seller's financial
export is worth deleting rather than keeping.

Confirm already deletes the file it consumed, and sweeps that seller's stale leftovers. But that
path only runs for a seller who confirms something. A seller who uploads, looks at the preview and
walks away triggers nothing at all, and their file stays forever: there is no other cleanup.

This is that missing sweep, over every seller, on the scheduler's tick.
"""
import asyncio
import logging
import time
from pathlib import Path

from routers import csv_import

logger = logging.getLogger(__name__)

# How often the sweep actually runs. The scheduler ticks every minute, which is far more often
# than a one-hour retention window needs: a file may outlive its window by up to this interval,
# which costs nothing, while scanning every seller's directory sixty times an hour costs real
# syscalls. The sweep decides for itself whether its time has come — no second scheduler, and no
# sleeping inside the tick.
_SWEEP_INTERVAL_SECONDS = 15 * 60

# Monotonic timestamp of the last completed sweep. None means "never ran", so the first tick after
# a restart sweeps immediately rather than waiting out the interval.
_last_sweep_at: float | None = None

# Guards against a second sweep starting while one is still going. A slow filesystem must not
# stack overlapping sweeps deleting the same files underneath each other.
_sweep_lock = asyncio.Lock()


def _sweep_dir(user_dir: Path, cutoff: float) -> int:
    """Delete stale CSVs in ONE seller's directory. Returns how many went."""
    removed = 0
    for entry in user_dir.iterdir():
        try:
            # is_file() follows symlinks, so ask about the link itself first. A symlink is never
            # ours to follow: pointed at something outside the tree it would turn this sweep into
            # a delete-anything primitive. Unlinking the link would still be deleting a thing we
            # did not create, so leave it alone entirely and let it be noticed.
            if entry.is_symlink():
                # No name, no path: the filename is a seller's upload and the directory name is
                # their user id. That an unexpected symlink exists is the whole message.
                logger.warning("uploads cleanup: skipped an unexpected symlink")
                continue
            if not entry.is_file() or entry.suffix.lower() != ".csv":
                continue
            if entry.stat().st_mtime > cutoff:
                continue          # still inside its import session
            entry.unlink()
            removed += 1
        except FileNotFoundError:
            # A confirm running right now may have deleted it between the check and the unlink.
            # That is the outcome we wanted anyway.
            continue
        except OSError:
            # One unreadable or locked file must not cost us the rest of the sweep.
            logger.warning("uploads cleanup: could not remove a file")
            continue
    return removed


def _sweep_now() -> tuple[int, int]:
    """The whole filesystem walk, synchronous. Returns (files removed, directories removed).

    Deliberately a plain function: it is pure blocking I/O, so it runs in a worker thread rather
    than on the event loop that is also serving the API.
    """
    # Read the module attributes at call time rather than binding them at import: the upload root
    # is a module-level default, and resolving it once at import would silently ignore any later
    # reconfiguration — which is exactly how a sweep ends up cleaning a directory nobody uses.
    root = Path(csv_import._UPLOAD_DIR)
    if not root.is_dir():
        return 0, 0

    cutoff = time.time() - csv_import._ORPHAN_TTL_SECONDS
    files = dirs = 0

    for user_dir in root.iterdir():
        try:
            # Same reasoning as above, one level up: a symlinked "seller directory" would let the
            # sweep walk out of uploads/imports entirely. Everything here stays within `root`
            # because we only ever iterate it — no path is built from user input.
            if user_dir.is_symlink() or not user_dir.is_dir():
                continue

            files += _sweep_dir(user_dir, cutoff)

            # An empty directory is just the shape of a seller's id left lying around. Remove it
            # with rmdir, never a recursive delete: if anything is still in there, rmdir refuses,
            # which is exactly the safety we want.
            try:
                next(user_dir.iterdir())
            except StopIteration:
                user_dir.rmdir()
                dirs += 1
        except (FileNotFoundError, StopIteration):
            continue
        except OSError:
            logger.warning("uploads cleanup: could not process a seller directory")
            continue

    return files, dirs


async def run_uploads_cleanup(force: bool = False) -> tuple[int, int]:
    """Remove unconfirmed uploads older than the retention window, everywhere.

    Returns (files removed, directories removed) — (0, 0) when it is not yet time to sweep, or
    when another sweep is already running. `force` skips the interval check; the tests use it,
    and it is what a caller who wants a sweep *now* would reach for.
    """
    global _last_sweep_at

    now = time.monotonic()
    if not force and _last_sweep_at is not None and now - _last_sweep_at < _SWEEP_INTERVAL_SECONDS:
        return 0, 0

    # Never block waiting for the other sweep: this runs on a one-minute tick, so if a sweep is
    # still going, the right move is to leave and let the next tick decide.
    if _sweep_lock.locked():
        return 0, 0

    async with _sweep_lock:
        try:
            # The walk is blocking I/O. Run it on a worker thread so the single-worker backend
            # keeps serving requests while it happens — a directory per seller, scanned on the
            # event loop, would stall every API call for the length of the scan.
            files, dirs = await asyncio.to_thread(_sweep_now)
        finally:
            # Stamp even on failure, so a filesystem that keeps erroring is retried on the
            # interval rather than on every single tick.
            _last_sweep_at = time.monotonic()

    return files, dirs
