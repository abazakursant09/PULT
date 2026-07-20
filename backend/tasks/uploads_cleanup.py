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
import logging
import time
from pathlib import Path

from routers import csv_import

logger = logging.getLogger(__name__)


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
                logger.warning("uploads cleanup: skipping symlink %s", entry.name)
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
            logger.warning("uploads cleanup: could not remove an entry in %s", user_dir.name)
            continue
    return removed


async def run_uploads_cleanup() -> int:
    """Remove unconfirmed uploads older than the retention window, everywhere.

    Returns the number of files deleted. Never raises: cleaning up is not worth failing a
    scheduler tick over, and the caller logs whatever comes back.
    """
    # Read the module attributes at call time rather than binding them at import: the upload root
    # is a module-level default, and resolving it once at import would silently ignore any later
    # reconfiguration — which is exactly how a sweep ends up cleaning a directory nobody uses.
    root = Path(csv_import._UPLOAD_DIR)
    if not root.is_dir():
        return 0

    cutoff = time.time() - csv_import._ORPHAN_TTL_SECONDS
    removed = 0

    for user_dir in root.iterdir():
        try:
            # Same reasoning as above, one level up: a symlinked "seller directory" would let the
            # sweep walk out of uploads/imports entirely. Everything here stays within `root`
            # because we only ever iterate it — no path is built from user input.
            if user_dir.is_symlink() or not user_dir.is_dir():
                continue

            removed += _sweep_dir(user_dir, cutoff)

            # An empty directory is just the shape of a seller's id left lying around. Remove it
            # with rmdir, never a recursive delete: if anything is still in there, rmdir refuses,
            # which is exactly the safety we want.
            try:
                next(user_dir.iterdir())
            except StopIteration:
                user_dir.rmdir()
        except (FileNotFoundError, StopIteration):
            continue
        except OSError:
            logger.warning("uploads cleanup: could not process a seller directory")
            continue

    return removed
