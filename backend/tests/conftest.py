"""Shared test guarantees.

Nothing here changes what any test asserts.
"""
import pytest


@pytest.fixture(scope="session")
def _uploads_root(tmp_path_factory):
    """One throwaway upload root for the whole session, removed by pytest afterwards."""
    return tmp_path_factory.mktemp("uploads") / "imports"


@pytest.fixture(autouse=True)
def _uploads_never_touch_the_working_tree(_uploads_root, monkeypatch):
    """Point the upload directory at a temp path for EVERY test.

    Two import tests post real files to the real endpoint, which wrote them to
    backend/uploads/imports/<user_id>/ — inside the working tree. Running the suite therefore
    left directories and CSVs behind: they were still there, untracked, weeks later, and had to
    be inspected before anyone could say whether they were test noise or a seller's data. Test
    output that is indistinguishable from production data is a trap, so the harness makes it
    impossible rather than asking each test to remember.

    Autouse and applied to every test on purpose: the next test to upload something will be
    covered without its author having to know this problem ever existed. `_UPLOAD_DIR` is read
    at call time, so patching the module attribute is enough.
    """
    from routers import csv_import
    monkeypatch.setattr(csv_import, "_UPLOAD_DIR", _uploads_root)
